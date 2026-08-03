#!/usr/bin/env python3
"""
Thin wrapper so run_dqa can be invoked as a script directly from a clone of
this repo, without needing `pip install` first:

    python3 scripts/run_dqa.py file1.csv file2.csv file3.csv --report

Equivalent to (once the package is installed): run_dqa file1.csv file2.csv file3.csv --report
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vman_dq.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())