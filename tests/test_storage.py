"""Keeping the data folder lean without touching anything the app did not make to throw away."""

import os
import pickle
import time

from triton.store import ProjectStore, housekeeping
from triton.validation import ImportResult


def _age(path, seconds):
    old = time.time() - seconds
    for f in [path, *(path.rglob("*") if path.is_dir() else [])]:
        os.utime(f, (old, old))


def test_housekeeping_deletes_only_its_allow_list(tmp_path):
    day = 2 * 24 * 3600
    # What it may delete: an abandoned upload, an old progress note, a half-written file.
    upload = tmp_path / "uploads" / ("a" * 32)
    upload.mkdir(parents=True)
    (upload / "data.xlsx").write_bytes(b"x" * 100)
    progress = tmp_path / "progress" / "design-x.json"
    progress.parent.mkdir()
    progress.write_text("{}")
    tmp = tmp_path / "projects" / "p1" / "s1" / "results.tmp"
    tmp.parent.mkdir(parents=True)
    tmp.write_text("{")
    # What it must never touch, however old: templates, reference files, the kept data itself.
    keep = [
        tmp_path / "templates" / "report.docx",
        tmp_path / "uploads" / "reference.xlsx",
        tmp_path / "uploads" / "office-samples" / "Front Beam.ads",
        tmp_path / "projects" / "p1.json",
        tmp_path / "projects" / "p1" / "s1" / "results.json",
        tmp_path / "projects" / "p1" / "s1" / "workbook.pkl.gz",
        tmp_path / "projects" / "p1" / "s1" / "raw" / "0001.pkl.gz",
        tmp_path / "progress" / "notes.txt",
    ]
    for f in keep:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("keep")
    for f in [upload, progress, tmp, *keep, *(k.parent for k in keep)]:
        _age(f, day)
    # A fresh upload in progress stays too.
    fresh = tmp_path / "uploads" / ("b" * 32)
    fresh.mkdir()
    (fresh / "data.xlsx").write_bytes(b"y")

    gone = housekeeping(tmp_path, force=True)
    assert gone["files"] == 3 and gone["bytes"] >= 100
    assert not upload.exists() and not progress.exists() and not tmp.exists()
    assert all(f.exists() for f in keep) and fresh.exists()
    # It runs at most every few minutes unless forced.
    assert housekeeping(tmp_path) == {"files": 0, "bytes": 0}


def test_an_upload_still_being_added_to_is_not_abandoned(tmp_path):
    upload = tmp_path / "uploads" / ("c" * 32)
    upload.mkdir(parents=True)
    (upload / "data.xlsx").write_bytes(b"x")
    _age(upload, 2 * 24 * 3600)
    (upload / "data.xlsx").write_bytes(b"xy")  # a piece just arrived
    housekeeping(tmp_path, force=True)
    assert upload.exists()


def test_workbooks_are_kept_compressed_and_old_ones_still_load(tmp_path):
    store = ProjectStore(tmp_path)
    (tmp_path / "projects" / "p1.json").write_text("{}")
    result = ImportResult(sheets=[])
    store.save_workbook("p1", "s1", "w.xlsx", result)
    d = tmp_path / "projects" / "p1" / "s1"
    assert (d / "workbook.pkl.gz").exists() and not (d / "workbook.pkl").exists()
    assert isinstance(store.load_workbook("p1", "s1"), ImportResult)
    # A workbook saved before compression still loads.
    (d / "workbook.pkl.gz").unlink()
    with (d / "workbook.pkl").open("wb") as f:
        pickle.dump(result, f)
    assert isinstance(store.load_workbook("p1", "s1"), ImportResult)
    assert store.delete_results("p1", "s1") == 0
    usage = store.usage("p1", ["s1"])["s1"]
    assert usage["workbook"] > 0 and usage["results"] == 0
