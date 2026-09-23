"""Web API: upload a Plaxis workbook and get the import and validation report."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .reader import UnsupportedWorkbook
from .validation import import_workbook

STATIC = Path(__file__).parent / "static"
ALLOWED = {".xlsb", ".xlsx", ".xlsm"}

app = FastAPI(title="Triton")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/workbooks/check")
def check_workbook(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Upload a .xlsb, .xlsx or .xlsm file (got '{suffix or 'no extension'}').")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        try:
            result = import_workbook(path)
        except UnsupportedWorkbook as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # corrupt or password-protected files
            raise HTTPException(400, f"Could not read the workbook: {e}") from e
    return {"file": file.filename, **result.summary()}
