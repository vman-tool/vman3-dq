#!/usr/bin/env python3
"""
Dev script: compute the four DQA indicators (ICS, RRS, ICI, AID) against the
sample validation datasets in vman_dq/data/ and write a Markdown report to
vman_dq/reports/dqa_validation_report.md.

Not part of the installable package - this is a local validation tool for
comparing output against the source manuscript's Tables 3-8. Run from the
repo root:

    python3 scripts/generate_dqa_report.py
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from vman_dq.report import dqa_report  # noqa: E402

DATA_DIR = REPO_ROOT / "vman_dq" / "data"
OUT_DIR = REPO_ROOT / "reports"

DATASETS = {
    "Tanzania (2016)": {
        "file": "va_2016_tz.csv",
        "country": "Tanzania",
        "who_va_version": "2016",
    },
    "Eswatini (2022)": {
        "file": "va_2022_es.csv",
        "country": "Eswatini",
        "who_va_version": "2022",
    },
    "Nigeria (2022)": {
        "file": "va_2022_ng.csv",
        "country": "Nigeria",
        "who_va_version": "2022",
    },
}


def main() -> None:
    dataframes = {}
    meta = {}
    for label, cfg in DATASETS.items():
        path = DATA_DIR / cfg["file"]
        print(f"Loading {path} ...")
        df = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
        dataframes[label] = df
        meta[label] = {"country": cfg["country"], "who_va_version": cfg["who_va_version"]}

    report_md = dqa_report(
        dataframes, meta=meta,
        title="VMan3 DQA Indicator Validation Report (vman_dq)",
    )

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "dqa_validation_report.md"
    out_path.write_text(report_md)
    print(f"\nReport written to {out_path}")
    print("\n" + "=" * 70 + "\n")
    print(report_md)


if __name__ == "__main__":
    main()