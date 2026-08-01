"""
Command-line entry point for run_dqa.

Installed as a console script (see pyproject.toml [project.scripts]), so
after `pip install vman_dq` this is available directly:

    run_dqa file1.csv file2.csv file3.csv -r
    run_dqa file1.csv file2.csv file3.csv --report
"""

import argparse
import sys
from typing import Optional, Sequence

from .dqa import run_dqa


def _print_summary(label: str, r: dict) -> None:
    print(f"=== {label} (n={r['n_records']}) ===")
    ics, rrs, ici, aid = r["ics"], r["rrs"], r["ici"], r["aid"]
    print(f"  ICS  mean={ics['summary']['mean']}")
    print(f"  RRS  computable={rrs['computable']}  mean={rrs['summary']['mean']}")
    print(
        f"  ICI  mean={ici['summary']['mean']}  "
        f"rules_applied={ici['rules_applied']}  "
        f"rules_excluded={ici['rules_excluded_missing_fields']}  "
        f"pass_all={ici['records_passing_all_rules']}"
    )
    print(f"  AID  computable={aid['computable']}  median={aid['summary']['median']}")
    print()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_dqa",
        description=(
            "Compute VA data quality indicators (ICS, RRS, ICI, AID) for one "
            "or more VA data CSV files."
        ),
    )
    parser.add_argument("files", nargs="+", help="One or more VA data CSV files")
    parser.add_argument(
        "-r", "--report", dest="report", action="store_true",
        help="Also render a Markdown report across all input files "
             "(written to <first file's directory>/reports/dqa_report.md)",
    )
    parser.add_argument(
        "-o", "--out-dir", dest="out_dir", default=None,
        help="Directory to write the report to (with -report). "
             "Default: reports/ next to the first input file.",
    )
    args = parser.parse_args(argv)

    try:
        results = run_dqa(*args.files, report=args.report, out_dir=args.out_dir)
    except Exception as exc:  # surface a clean CLI error instead of a traceback
        print(f"run_dqa: {exc}", file=sys.stderr)
        return 1

    for label, r in results.items():
        if label == "report":
            continue
        _print_summary(label, r)

    if args.report:
        print(results["report"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())