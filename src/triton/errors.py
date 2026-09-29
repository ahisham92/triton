"""Server errors with a code: the page shows the code, and the full traceback is kept under it in
``<data>/errors/<code>.txt`` (the newest 100), so the error can be reported by its code and found."""

from __future__ import annotations

import os
import secrets
import time
import traceback
from pathlib import Path

KEEP = 100


def folder() -> Path:
    root = Path(os.environ.get("TRITON_DATA_DIR", "data")) / "errors"
    root.mkdir(parents=True, exist_ok=True)
    return root


def where(exc: BaseException) -> str:
    """The innermost Triton line the error came from: file:line in function."""
    frames = [
        f
        for f in traceback.extract_tb(exc.__traceback__)
        if "triton" in f.filename and f.name != "_coded_errors"
    ]
    if not frames:
        return ""
    f = frames[-1]
    return f"{Path(f.filename).name}:{f.lineno} in {f.name}"


def record(exc: BaseException, method: str, path: str) -> dict:
    """Keep the error under a new code; the answer the page shows for it."""
    code = f"E{time.strftime('%d%H%M')}-{secrets.token_hex(2).upper()}"
    element = getattr(exc, "triton_element", None)
    text = f"{type(exc).__name__}: {exc}".strip()
    at = where(exc)
    lines = [
        f"Code: {code}",
        f"When: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Request: {method} {path}",
        *([f"Element: {element}"] if element else []),
        f"Error: {text}",
        "",
        "".join(traceback.format_exception(exc)),
    ]
    try:
        root = folder()
        (root / f"{code}.txt").write_text("\n".join(lines), "utf-8")
        for old in sorted(root.glob("E*.txt"), key=lambda p: p.stat().st_mtime)[:-KEEP]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # the answer still names the error
    said = f"Server error {code}: {text[:300]}"
    if element:
        said += f" (while designing {element})"
    if at:
        said += f" [{at}]"
    return {"detail": said, "code": code, "where": at, "element": element}


def read(code: str) -> str | None:
    if not code.replace("-", "").isalnum():
        return None
    try:
        return (folder() / f"{code}.txt").read_text("utf-8")
    except FileNotFoundError:
        return None
