"""
Command line for the skills — try any skill on your own files.

EXAMPLES (run from the project root)
------------------------------------
    python -m data_skills list
    python -m data_skills describe trend-analysis
    python -m data_skills run explore-data samples/sales.csv
    python -m data_skills run trend-analysis samples/sales.csv --param value_column=amount
    python -m data_skills run build-dashboard samples/sales.csv --param range=last_3_months
    python -m data_skills run er-diagram samples/orders.csv samples/customers.csv

WHAT YOU GET
------------
The summary and findings print in the terminal. Tables (CSV), reports
(Markdown/HTML), SQL and diagrams are saved to outputs/<skill>/ (git-ignored),
or the folder given with --out.

Files are read with the same safe readers the website uses
(api/services/file_reader.py), and cleaned first unless you pass --raw.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from api.services.cleaning import clean_dataframe
from api.services.file_reader import read_file
from data_skills._core import SkillInputError
from data_skills.registry import describe, list_skills, load_skill, run_skill

_ARTIFACT_EXT = {"markdown": "md", "sql": "sql", "mermaid": "mmd", "html": "html", "json": "json"}


def _load(paths: list[str], raw: bool) -> dict[str, pd.DataFrame]:
    """
    Read files with the website's safe readers and, unless `raw`, clean each table.

    One file can hold several tables (e.g. an Excel workbook with several sheets);
    each becomes its own entry. Untrusted formats such as pickle are never read
    (see api/services/file_reader.py).

    Args:
        paths: file paths given on the command line.
        raw: True to skip `clean_dataframe` and keep the data exactly as read.

    Returns:
        dict[str, pd.DataFrame]: table name (from the reader) -> DataFrame.

    Raises:
        OSError: a file can't be opened.
        ValueError: a file's format is not supported or can't be parsed (from read_file).

    Example:
        >>> tables = _load(["samples/sales.csv"], raw=False)
        >>> list(tables)   # one table, named after the file
    """
    tables: dict[str, pd.DataFrame] = {}
    for p in paths:
        for ds in read_file(Path(p).name, Path(p).read_bytes()):
            df = ds.df if raw else clean_dataframe(ds.df, ds.name)[0]
            tables[ds.name] = df
    return tables


def main(argv: list[str] | None = None) -> int:
    """
    Run the `python -m data_skills` command line: list, describe, or run a skill.

    Commands:
    - list: print every skill grouped by service, in catalog.json order
    - describe <skill>: print the skill's options as JSON
    - run <skill> <files...> [--param NAME=VALUE ...] [--out DIR] [--raw]: read and
      (unless --raw or the skill wants raw data) clean the files, run the skill,
      print the summary and findings, and save tables as CSV, artifacts as files
      (.md, .sql, .mmd, .html, .json by name prefix, else .txt) and result.json to
      outputs/<skill>/ or --out

    Args:
        argv: command-line arguments without the program name; None (default) reads sys.argv.

    Returns:
        int: exit code: 0 on success, 2 when the skill rejects the data or options
        (the message is printed to stderr).

    Raises:
        SystemExit: from argparse, for a missing or unknown command.

    Example:
        python -m data_skills run trend-analysis samples/sales.csv --param value_column=amount
        # prints the summary and findings, then "Saved N table(s), ... to outputs/trend-analysis/"
    """
    # Windows terminals default to an old code page that can't print "•" or "→";
    # switch the output to UTF-8 so findings print on every computer.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m data_skills", description="Run data skills on files.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List every skill by category")
    d = sub.add_parser("describe", help="Show a skill's options")
    d.add_argument("skill")
    r = sub.add_parser("run", help="Run a skill on one or more files")
    r.add_argument("skill")
    r.add_argument("files", nargs="+")
    r.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="Skill option (repeatable)")
    r.add_argument("--out", default=None, help="Folder for outputs (default: outputs/<skill>)")
    r.add_argument("--raw", action="store_true", help="Skip the automatic cleaning step")
    args = parser.parse_args(argv)

    if args.command == "list":
        # Same order as the services on the website (catalog.json order).
        order = [c["id"] for c in json.loads((Path(__file__).parent / "catalog.json").read_text())["categories"]]
        current = None
        for s in sorted(list_skills(), key=lambda s: (order.index(s["category"]) if s["category"] in order else 99, s["id"])):
            if s["category_name"] != current:
                current = s["category_name"]
                print(f"\n{current}")
            print(f"  {s['id']:<26} {s['label']}")
        return 0

    if args.command == "describe":
        print(json.dumps(describe(args.skill), indent=2))
        return 0

    params = dict(p.split("=", 1) for p in args.param)
    try:
        raw = args.raw or load_skill(args.skill).SKILL.wants_raw
        result = run_skill(args.skill, _load(args.files, raw), params)
    except SkillInputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"\n{result.summary}\n")
    for f in result.findings:
        print(f"  • {f}")

    out = Path(args.out or f"outputs/{args.skill}")
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in result.tables.items():
        pd.DataFrame(rows).to_csv(out / f"{name}.csv", index=False)
    for name, text in result.artifacts.items():
        (out / f"{name}.{_ARTIFACT_EXT.get(name.split('_')[0], 'txt')}").write_text(text, encoding="utf-8")
    (out / "result.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(f"\nSaved {len(result.tables)} table(s), {len(result.artifacts)} file(s) and result.json to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
