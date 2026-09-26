"""Designs that run on the server with no page open: "Design all sections" handed to a runner.

The web workers only work while they answer a request, and a host cuts a long request off, so a
design driven by the page stops when the page closes. The runner is a separate program that keeps
going on its own (on PythonAnywhere an Always-on task: ``python -m triton.cli runner``). The page
leaves a queue file per project in ``<data>/queue``; the runner designs its sections one after
another, each exactly as the Design button would, and writes how each went back into the file.

Only one runner works at a time (a lock file); a second one started by mistake waits its turn.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import atomic

ALIVE_S = 60  # a runner that has not said it is there for this long is taken to be off
BEAT_S = 10
BUSY_TRIES = 12  # a section a window is designing is tried again this many times, BUSY_WAIT_S apart
BUSY_WAIT_S = 15
WORKERS = 2  # sections designed at the same time by the runner


def folder(data: str | Path | None = None) -> Path:
    root = Path(data or os.environ.get("TRITON_DATA_DIR", "data")) / "queue"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _beat_file(data: str | Path | None = None) -> Path:
    return folder(data) / "runner.beat"


def alive(data: str | Path | None = None) -> bool:
    """Whether a runner is on: it touches its beat file every few seconds."""
    try:
        return time.time() - _beat_file(data).stat().st_mtime < ALIVE_S
    except FileNotFoundError:
        return False


def queue_path(project_id: str, data: str | Path | None = None) -> Path:
    if not project_id.isalnum():
        raise ValueError(project_id)
    return folder(data) / f"{project_id}.json"


def load(project_id: str, data: str | Path | None = None) -> dict[str, Any] | None:
    try:
        return json.loads(queue_path(project_id, data).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def save(queue: dict[str, Any], data: str | Path | None = None) -> None:
    atomic.write_text(queue_path(queue["pid"], data), json.dumps(queue))


def lock(project_id: str, data: str | Path | None = None):
    """Held while the page or the runner reads a queue file, changes it and saves it."""
    return atomic.locked(queue_path(project_id, data).with_suffix(".lock"))


def finished(queue: dict[str, Any] | None) -> bool:
    return not queue or all(s["state"] not in ("waiting", "running") for s in queue["sections"])


def new_queue(project_id: str, sections: list[tuple[str, str]], mode: str) -> dict[str, Any]:
    return {
        "pid": project_id,
        "mode": mode,
        "asked_at": time.time(),
        "stop": False,
        "sections": [{"id": sid, "name": name, "state": "waiting", "note": ""} for sid, name in sections],
    }


def _update(project_id: str, change, data: str | Path | None = None) -> dict[str, Any] | None:
    with lock(project_id, data):
        q = load(project_id, data)
        if q is None:
            return None
        change(q)
        save(q, data)
        return q


def _next(data: str | Path | None = None) -> str | None:
    """The project whose queue was asked for first among those with sections waiting."""
    waiting = []
    for f in folder(data).glob("*.json"):
        try:
            q = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not q.get("stop") and any(s["state"] == "waiting" for s in q.get("sections", [])):
            waiting.append((f.stat().st_mtime, q["pid"]))
    return min(waiting)[1] if waiting else None


def _outcome(res: dict[str, Any]) -> tuple[str, str]:
    """The state and note a design's answer gives its section."""
    if res.get("stopped"):
        return "stopped", "Stopped"
    kinds = ("piles", "combi_walls", "beams", "slabs", "sheet_pile_walls", "approach_slabs")
    done = set(res.get("designed") or [])
    designed = [e for k in kinds for e in res.get(k) or [] if e.get("element") in done]
    unsafe = sum(1 for e in designed if e.get("passed") is False)
    return "done", f"{unsafe} unsafe" if unsafe else "All safe"


def design_one(project_id: str, section_id: str, mode: str, sleep=time.sleep) -> tuple[str, str]:
    """Design every element of one section, as the Design button does; returns (state, note)."""
    from fastapi import HTTPException

    from . import api
    from .design.stop import Stopped

    for tries in range(BUSY_TRIES + 1):
        try:
            res = api.design_section(project_id, section_id, api.DesignRequest(mode=mode, run="server"))
            return _outcome(res)
        except Stopped:
            return "stopped", "Stopped"
        except HTTPException as e:
            if e.status_code == 409 and e.detail == api.BUSY and tries < BUSY_TRIES:
                sleep(BUSY_WAIT_S)  # a window is designing it: wait for it to finish
                continue
            return "failed", str(e.detail)
        except Exception as e:  # noqa: BLE001 - one section failing leaves the others to design
            return "failed", f"{type(e).__name__}: {e}"
    return "failed", "Another window kept designing it."


def code_stamp() -> str:
    """Which Triton code is on disk: a site update (git pull) changes it."""
    here = Path(__file__).parent
    files = sorted(here.rglob("*.py"))
    return "|".join(f"{f.relative_to(here)}:{f.stat().st_mtime_ns}:{f.stat().st_size}" for f in files)


def _claim(project_id: str | None = None, data: str | Path | None = None) -> tuple[str, str, str] | None:
    """Mark the next waiting section running (of ``project_id``, else of the queue asked for first)
    and return (project, section, mode)."""
    pid = project_id or _next(data)
    if pid is None:
        return None
    got: list[tuple[str, str, str]] = []

    def start(q):
        if q.get("stop"):
            return
        todo = next((s for s in q["sections"] if s["state"] == "waiting"), None)
        if todo is not None:
            todo.update(state="running", started=time.time())
            got.append((pid, todo["id"], q.get("mode") or "detailed"))

    _update(pid, start, data)
    return got[0] if got else None


def _finish(project_id: str, section_id: str, state: str, note: str, data: str | Path | None = None) -> None:
    def end(q):
        for s in q["sections"]:
            if s["id"] == section_id:
                s.update(state=state, note=note, finished=time.time())
        if state == "stopped" or q.get("stop"):
            q["stop"] = True
            for s in q["sections"]:
                if s["state"] == "waiting":
                    s.update(state="stopped", note="Not started")

    _update(project_id, end, data)


def work(project_id: str, data: str | Path | None = None, sleep=time.sleep, updated=lambda: False) -> None:
    """Design the queue's waiting sections one after another, until it is done or stopped (or the
    code was updated: the runner starts again on the new code and carries on)."""
    while not updated():
        job = _claim(project_id, data)
        if job is None:
            return
        pid, sid, mode = job
        _finish(pid, sid, *design_one(pid, sid, mode, sleep), data)


def _design_in_child(pid: str, sid: str, mode: str) -> tuple[str, str]:  # pragma: no cover
    return design_one(pid, sid, mode)


def _die_with_runner() -> None:  # pragma: no cover
    """A design process ends when the runner does (the task stopped or restarted), not left over."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def run_forever(
    data: str | Path | None = None, idle_s: float = 5.0, workers: int = WORKERS
) -> None:  # pragma: no cover
    """The runner: say it is there, and design whatever is queued, for as long as it runs. Up to
    ``workers`` sections are designed at the same time, each in a process of its own."""
    import fcntl
    import multiprocessing
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from concurrent.futures.process import BrokenProcessPool

    if data:
        os.environ["TRITON_DATA_DIR"] = str(data)
    with open(folder(data) / "runner.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # another runner already on: wait until it stops

        # A section left "running" by a runner that was stopped (the task restarted) starts again.
        for f2 in folder(data).glob("*.json"):
            try:
                pid = json.loads(f2.read_text("utf-8"))["pid"]
            except (OSError, ValueError, KeyError):
                continue
            _update(pid, _requeue, data)
        # The design processes are made before the beat thread starts (a fork copies no threads).
        pool = ProcessPoolExecutor(
            max(1, workers), mp_context=multiprocessing.get_context("fork"), initializer=_die_with_runner
        )

        def beat() -> None:
            while True:
                _beat_file(data).touch()
                time.sleep(BEAT_S)

        threading.Thread(target=beat, daemon=True).start()
        print(f"Triton runner on ({workers} at a time), queue in {folder(data)}", flush=True)
        started = code_stamp()
        running: dict = {}  # future -> (project, section)
        while True:
            fresh = code_stamp() == started
            # Start sections while there is room (none once the code was updated).
            while fresh and len(running) < workers:
                job = _claim(None, data)
                if job is None:
                    break
                pid, sid, mode = job
                print(f"{time.strftime('%H:%M:%S')} designing {pid} / {sid}", flush=True)
                running[pool.submit(_design_in_child, pid, sid, mode)] = (pid, sid)
            if not running:
                if not fresh:
                    # A site update: stop, and PythonAnywhere starts the task again on the new code
                    # (the queue carries on from where it was).
                    print(f"{time.strftime('%H:%M:%S')} Triton was updated: starting again", flush=True)
                    pool.shutdown()
                    raise SystemExit(3)
                time.sleep(idle_s)
                continue
            done, _ = wait(running, timeout=idle_s, return_when=FIRST_COMPLETED)
            broken = False
            for fut in done:
                pid, sid = running.pop(fut)
                try:
                    state, note = fut.result()
                except BrokenProcessPool:
                    broken = True
                    state, note = "failed", "The design process stopped (out of memory?)."
                except Exception as e:  # noqa: BLE001
                    state, note = "failed", f"{type(e).__name__}: {e}"
                _finish(pid, sid, state, note, data)
                print(f"{time.strftime('%H:%M:%S')} {pid} / {sid}: {state} {note}", flush=True)
            if broken:
                pool.shutdown(wait=False, cancel_futures=True)
                for pid, sid in running.values():
                    _finish(pid, sid, "failed", "The design process stopped (out of memory?).", data)
                running.clear()
                pool = ProcessPoolExecutor(
                    max(1, workers),
                    mp_context=multiprocessing.get_context("fork"),
                    initializer=_die_with_runner,
                )


def _requeue(q: dict[str, Any]) -> None:
    for s in q["sections"]:
        if s["state"] == "running":
            s.update(state="waiting", note="")
