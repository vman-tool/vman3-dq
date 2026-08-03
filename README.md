# VMan3 Data Processing Toolkit

A Python package for processing and quality checking VA data.

## Features
- Automatically marks skipped questions based on relevance expressions
- Comprehensive data cleaning pipeline
- Handles complex ODK relevance expressions
- Case-insensitive variable matching
- Detailed debugging output
- Four record-level VA data quality indicators (ICS, RRS, ICI, AID) with
  dataset-level summary statistics

## Installation

```bash
pip install vman3
```

## Usage

```bash
import vman3 as vman
import pandas as pd

# Load your data
data_df = pd.read_csv('va_data.csv')
dict_df = pd.read_csv('dictionary.csv')

# Basic cleaning
processed_data = vman.change_null_toskipped(data_df, dict_df, verbose=True)
```

## Data Quality Indicators (ICS, RRS, ICI, AID)

Four record-level VA data quality indicators, as defined in *Four Practical
Indicators for Real-Time Verbal Autopsy Data Quality Assessment: A
Cross-National Validation Study* (Lyatuu et al.):

- **ICS** - Informative Completeness Score: share of answered binary (id*)
  questions that are a definitive yes/no rather than dk/ref.
- **RRS** - Respondent Reliability Score: weighted composite of relationship
  to deceased, presence at death, recall period, and respondent literacy.
- **ICI** - Internal Consistency Index: share of logical-consistency rules
  (currently nine - C1-C9: pregnancy-in-male, blood-without-cough, four
  symptom-duration-exceeds-illness-duration checks, pregnancy symptoms /
  maternal-death questions answered for a male decedent, and interview
  date preceding death date) not violated by the record. The rule set is
  open-ended by design - N grows as rules are added, nothing else changes.
- **AID** - Average Interview Duration: elapsed minutes between interview
  start and end, handling both full-datetime (2022 instrument) and time-only
  (2016 instrument) fields.

The primary entry point is `run_dqa`. It accepts one or more datasets, each
either a pandas DataFrame - built from a CSV locally, or from database
records in production - or a path to a CSV file, mixed freely. WHO-VA field
names (id10xxx) are resolved case-insensitively, and fields absent from a
given dataset make that indicator (or, for ICI, that specific rule) "not
computable" rather than raising an error.

```python
import pandas as pd
from vman_dq import run_dqa

# Single dataset, from a DataFrame already in memory (e.g. built from a DB query):
results = run_dqa(df)
r = results['dataset_1']
r['ics']['summary']            # {'n', 'mean', 'median', 'sd', 'min', 'max', 'p5', 'p95'}
r['rrs']['tiers']              # {'High': {...}, 'Moderate': {...}, 'Low': {...}}
r['ici']['rule_violations']    # per-rule (C1-C9) violation counts/pct
r['aid']['per_record']         # pandas Series, one value per row

# Multiple datasets at once, plus a Markdown report (Tables 3-8 layout) across all of them:
results = run_dqa(df1, df2, df3, report=True)
print(results['report'])

# CSV paths work the same way, and get labeled by filename automatically:
results = run_dqa('va_2016_tz.csv', 'va_2022_es.csv', report=True)
```

## Command line

### Installing in an isolated environment (recommended)

```bash
cd vman_dq
python3 -m venv venv
source venv/bin/activate        # macOS/Linux
pip install --upgrade pip
pip install -e .                # -e: editable install, picks up local changes immediately
```

This registers the real `run_dqa` console command inside the venv (via the
`[project.scripts]` entry in `pyproject.toml`), so it works directly with no
`python3` prefix or path needed:

```bash
run_dqa file1.csv file2.csv file3.csv -r
run_dqa file1.csv file2.csv file3.csv --report
```

Flags follow standard POSIX/GNU convention: a single dash for a one-letter
short option (`-r`), a double dash for the full-word long option
(`--report`) - both forms are accepted and equivalent.

Prints a summary for each file and, with `-r`/`--report`, a combined
Markdown report (also written to `<first file's directory>/reports/dqa_report.md`,
or `-o/--out-dir` if given).

Deactivate the environment when done with `deactivate`.

### Running without installing

From a clone of this repo, without `pip install`-ing anything, either of
these are equivalent to the `run_dqa` console command above:

```bash
python3 -m vman_dq.cli file1.csv file2.csv file3.csv --report
```

`-m` runs a module by its import name rather than by file path - it finds
`vman_dq`, imports it as a package, then runs `cli.py` inside it as
`__main__`. This matters here specifically because `cli.py` uses a relative
import (`from .dqa import run_dqa`), which only resolves correctly when
Python loads it as part of its package (via `-m`) rather than as a
standalone script by path.

```bash
python3 scripts/run_dqa.py file1.csv file2.csv file3.csv --report
```

A thin wrapper around the same CLI, for anyone who'd rather run a plain
script path than remember the `-m` module syntax.

To regenerate the full validation report against the reference datasets in
`vman_dq/data/` with country/instrument-version metadata for Table 3 (also
serves as a sanity check against the manuscript's published Tables 3-8 -
exact match is not expected since the bundled CSVs have since been updated
with more records):

```bash
python3 scripts/generate_dqa_report.py
```


