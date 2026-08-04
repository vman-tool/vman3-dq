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
- **ICI** - Internal Consistency Index: share of nine logical-consistency
  rules not violated by the record. C2-C5's field lists are hand-verified
  (each field confirmed `select_one` with a "yes" option) against a
  variable-mapping sheet added to the WHO 2016/2022 xForm workbooks
  (`vman_ml/resources/va_instr_*.xlsx`, `mapping` sheet), rather than
  exhaustively auto-derived from every age/sex-tagged xForm field - an
  earlier auto-derived attempt (checking ~40-300 fields per rule) flagged
  effectively every record in every dataset tested, since even a small
  per-field false-positive rate compounds into near-certain flagging when
  OR'd across hundreds of fields. The rule set is open-ended by design -
  N grows as rules are added, nothing else changes.

  | Rule | Condition | Fields | Logic |
  |---|---|---|---|
  | C1 | Interview date precedes death date | `id10012` (interview), `id10023` (death) | `id10012 < id10023` |
  | C2 | Adult-only questions answered for a child or neonate | `id10138`, `id10170`, `id10237`, `id10212`, `id10411` | `(isChild==1 OR isNeonatal==1) AND (id10138=='yes' OR id10170=='yes' OR id10237=='yes' OR id10212=='yes' OR id10411=='yes')` |
  | C3 | Child-only questions answered for an adult or neonate | `id10185`, `id10269`, `id10369` | `(isAdult==1 OR isNeonatal==1) AND (id10185=='yes' OR id10269=='yes' OR id10369=='yes')` |
  | C4 | Neonate-only questions answered for an adult or child | `id10104`, `id10105`, `id10107`, `id10377`, `id10109` | `(isAdult==1 OR isChild==1) AND (id10104=='yes' OR id10105=='yes' OR id10107=='yes' OR id10377=='yes' OR id10109=='yes')` |
  | C5 | Female-only (pregnancy/maternal) questions answered for a male | `id10294`, `id10305`, `id10304`, `id10328`, `id10340` | `sex=='male' AND (id10294=='yes' OR id10305=='yes' OR id10304=='yes' OR id10328=='yes' OR id10340=='yes')` |
  | C6 | Fever duration exceeds total illness duration | `id10120`, `id10148` | `id10148 (fever days) > id10120 (illness days)` |
  | C7 | Cough duration exceeds total illness duration | `id10120`, `id10154` | `id10154 (cough days) > id10120 (illness days)` |
  | C8 | Diarrhoea duration exceeds total illness duration | `id10120`, `id10182` | `id10182 (diarrhoea days) > id10120 (illness days)` |
  | C9 | Breathlessness duration exceeds total illness duration | `id10120`, `id10161` | `id10161 (breathlessness days) > id10120 (illness days)` |

  Notes:
  - C2's `id10411` (alcohol consumption) replaces an originally-proposed
    `id10487` (COVID-19 contact), which doesn't exist in the Tanzania 2016
    dataset - that field, along with two siblings (`id10485`, `id10486`),
    is part of a small COVID-19 module added to the WHO instrument after
    2020, which Tanzania's 2016 collection predates and never had.
  - C4's `id10109` replaces an originally-proposed `id10376`, which has no
    "yes" response option in its choice list (`before`/`after`/`dk`/`ref`)
    and so was never computable as written. `id10109` is also the field
    the earlier, since-removed rule "C7" mischecked against sex instead of
    neonatal status - this is its correct home.
  - C6-C9 only flag when the underlying symptom was itself reported (e.g.
    C6 requires `id10147=='yes'`, not just a fever-duration value present).
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


