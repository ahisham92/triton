"""Command line: ``triton check <workbook>`` prints the validation report."""

from __future__ import annotations

import argparse
import json
import sys

from .validation import import_workbook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triton")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Import a Plaxis workbook and list problems found.")
    check.add_argument("workbook")
    check.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    serve = sub.add_parser("serve", help="Run the web app.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--data",
        help="Folder for projects and uploaded workbooks (default: TRITON_DATA_DIR, else ./data).",
    )
    runner = sub.add_parser(
        "runner", help="Design what the page queued (Design all sections), with no page open."
    )
    runner.add_argument("--data", help="The same data folder as the web app (default: TRITON_DATA_DIR).")
    runner.add_argument(
        "--workers", type=int, default=2, help="Sections designed at the same time (default 2)."
    )
    args = parser.parse_args(argv)

    if args.command == "runner":
        from .runner import run_forever

        run_forever(args.data, workers=args.workers)
        return 0

    if args.command == "serve":
        import os

        import uvicorn

        if args.data:
            os.environ["TRITON_DATA_DIR"] = args.data
        data = os.environ.get("TRITON_DATA_DIR", "data")
        print(f"Triton on http://{args.host}:{args.port}, data in {data}")
        uvicorn.run("triton.api:app", host=args.host, port=args.port)
        return 0

    summary = import_workbook(args.workbook).summary()
    if args.json:
        json.dump(summary, sys.stdout, indent=2, default=str)
        print()
        return 1 if summary["counts"]["error"] else 0

    c = summary["counts"]
    print(f"{c['error']} error(s), {c['warning']} warning(s), {c['info']} automatic clean-up(s)\n")
    width = max((len(e) for e in summary["elements"]), default=7)
    combos = [x["name"] for x in summary["combinations"]]
    print("".ljust(width) + "  " + "  ".join(combos))
    marks = {"ok": "ok", "warning": "!", "error": "X", "missing": "-"}
    for e in summary["elements"]:
        cells = [marks[summary["coverage"][e][k]].center(len(k)) for k in combos]
        print(e.ljust(width) + "  " + "  ".join(cells))
    print()
    for i in summary["issues"]:
        if i["severity"] == "info":
            continue
        where = i["sheet"] or " ".join(x for x in (i["element"], i["combination"]) if x)
        rows = f" (rows {', '.join(map(str, i['rows']))})" if i["rows"] else ""
        print(f"[{i['severity'].upper()}] {where}: {i['message']}{rows}")
    return 1 if c["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
