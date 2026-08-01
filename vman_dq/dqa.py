"""
Four VA data quality indicators, as defined in:

"Four Practical Indicators for Real-Time Verbal Autopsy Data Quality
Assessment: A Cross-National Validation Study" (Lyatuu et al.)

- ICS: Informative Completeness Score
- RRS: Respondent Reliability Score
- ICI: Internal Consistency Index
- AID: Average Interview Duration

All four are computed at the individual-record level from a pandas
DataFrame, then aggregated into dataset-level summary statistics. The
DataFrame can come from a CSV (local validation) or be built directly
from database records (production use in vman3) - these functions have
no I/O of their own.

Field names follow the WHO-VA instrument convention (id10xxx) and are
resolved case-insensitively, since column casing differs across exports
(e.g. "Id10011" vs "id10011"). When a field required by an indicator is
absent from the input DataFrame, that indicator (or, for ICI, that
specific rule) is treated as not computable for the dataset rather than
raising an error - consistent with the source manuscript's treatment of
field absence as a data limitation, not an indicator failure.

Note on ICI: the manuscript's original validated results (Table 7) and
appendix (Table A3) used six consistency rules (C1-C6). As of this
version, ICI has been expanded to the full nine-rule set drafted in the
manuscript's methods section (2.3.3): C1-C6 as before, plus C7
(pregnancy-related symptoms reported for a male decedent), C8 (maternal
death questions answered for a male decedent), and C9 (interview date
precedes death date - a temporal impossibility).

The rule set is intentionally open-ended: ICI's denominator is the
number of rules actually applicable to a given dataset (N), not a
hardcoded count, so adding a tenth rule later only means adding one
entry to the rule registry below - nothing else in this module, or in
callers of compute_ici()/run_dqa(), needs to change.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

__all__ = [
    "compute_ics",
    "compute_rrs",
    "compute_ici",
    "compute_aid",
    "run_dqa",
]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BINARY_VALS = {"yes", "no", "dk", "ref"}
_INFORMATIVE_VALS = {"yes", "no"}

_TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


def _dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop case-insensitive duplicate columns, keeping the first occurrence.

    Some exports (e.g. the 2016 Tanzania dataset) contain duplicate column
    headers for a subset of fields; the manuscript's stated handling is to
    retain the first occurrence.
    """
    seen = set()
    keep = []
    for c in df.columns:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        keep.append(c)
    return df[keep]


def _col(df: pd.DataFrame, name: str) -> Optional[str]:
    """Case-insensitive column lookup. Returns the actual column name, or None."""
    name_l = name.lower()
    for c in df.columns:
        if c.lower() == name_l:
            return c
    return None


def _yn_series(df: pd.DataFrame, field: str) -> Optional[pd.Series]:
    c = _col(df, field)
    if c is None:
        return None
    return df[c].astype(str).str.strip().str.lower()


def _num_series(df: pd.DataFrame, field: str) -> Optional[pd.Series]:
    c = _col(df, field)
    if c is None:
        return None
    return pd.to_numeric(df[c], errors="coerce")


def _parse_time_of_day_minutes(value) -> Optional[float]:
    m = _TIME_ONLY_RE.match(str(value).strip())
    if not m:
        return None
    h, mnt, sec, ampm = m.groups()
    h, mnt = int(h), int(mnt)
    sec = int(sec) if sec else 0
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h != 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
    return h * 60 + mnt + sec / 60.0


def _parse_date_series(s: pd.Series) -> pd.Series:
    """Parse a column of date/datetime strings that mix formats within the
    same field (observed in practice: some records ISO 8601, others
    locale DD/MM/YYYY, within the *same* WHO-VA export). A single
    dayfirst setting applied uniformly either breaks pandas' fast ISO
    parse path or misreads slash dates, so this tries the unambiguous
    default parse first and only falls back to dayfirst=True for the
    remaining unparsed values.
    """
    s = s.astype(str).str.strip()
    parsed = pd.to_datetime(s, errors="coerce", utc=True)
    still_na = parsed.isna() & s.notna() & (s != "") & (s.str.lower() != "nan")
    if still_na.any():
        fallback = pd.to_datetime(s[still_na], errors="coerce", utc=True, dayfirst=True)
        parsed = parsed.copy()
        parsed.loc[still_na] = fallback
    return parsed


def _dist_stats(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"n": 0, "mean": None, "median": None, "sd": None,
                "min": None, "max": None, "p5": None, "p95": None}
    return {
        "n": int(s.shape[0]),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "sd": float(s.std()) if s.shape[0] > 1 else 0.0,
        "min": float(s.min()),
        "max": float(s.max()),
        "p5": float(s.quantile(0.05)),
        "p95": float(s.quantile(0.95)),
    }


def _tier_counts(s: pd.Series, high: float, moderate_low: float,
                  labels: Tuple[str, str, str] = ("High", "Moderate", "Low")) -> dict:
    s = s.dropna()
    n = len(s)
    if n == 0:
        return {}
    high_n = int((s >= high).sum())
    mod_n = int(((s >= moderate_low) & (s < high)).sum())
    low_n = int((s < moderate_low).sum())
    return {
        labels[0]: {"n": high_n, "pct": high_n / n * 100},
        labels[1]: {"n": mod_n, "pct": mod_n / n * 100},
        labels[2]: {"n": low_n, "pct": low_n / n * 100},
    }


# ---------------------------------------------------------------------------
# 2.3.1 Informative Completeness Score (ICS)
# ---------------------------------------------------------------------------

def compute_ics(df: pd.DataFrame, id_prefix: str = "id") -> pd.Series:
    """Per-record ICS (0-100): share of answered binary fields that are
    definitive yes/no rather than dk/ref. NaN for records with zero binary
    responses (not computable), matching the manuscript's definition.
    """
    df = _dedupe_columns(df)
    id_cols = [c for c in df.columns if c.lower().startswith(id_prefix)]
    if not id_cols:
        return pd.Series(np.nan, index=df.index)

    sub = df[id_cols].astype(str).apply(lambda col: col.str.strip().str.lower())
    is_binary = sub.isin(_BINARY_VALS)
    is_informative = sub.isin(_INFORMATIVE_VALS)

    total = is_binary.sum(axis=1)
    informative = is_informative.sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        ics = (informative / total.replace(0, np.nan)) * 100
    return ics


# ---------------------------------------------------------------------------
# 2.3.2 Respondent Reliability Score (RRS)
# ---------------------------------------------------------------------------

_RRS_REL_40 = {"spouse", "parent", "child"}
_RRS_REL_20 = {"family_member"}
_RRS_EDU_10 = {"primary_school", "secondary_school", "higher_than_secondary_school"}
_RRS_EDU_5 = {"no_formal_education"}


def compute_rrs(df: pd.DataFrame) -> pd.Series:
    """Per-record RRS (0-100). NaN if the recall period (death date vs
    interview date) cannot be parsed, or if the dataset lacks the fields
    RRS requires (id10008, id10009, id10023, and id10012 or id10011).
    """
    df = _dedupe_columns(df)

    rel_col = _col(df, "id10008")
    prox_col = _col(df, "id10009")
    death_col = _col(df, "id10023")
    intv_col = _col(df, "id10012") or _col(df, "id10011")

    if rel_col is None or prox_col is None or death_col is None or intv_col is None:
        return pd.Series(np.nan, index=df.index)

    # Wrel: relationship to deceased (max 40)
    rel = df[rel_col].astype(str).str.strip().str.lower()
    wrel = pd.Series(10.0, index=df.index)
    wrel[rel.isin(_RRS_REL_40)] = 40.0
    wrel[rel.isin(_RRS_REL_20)] = 20.0

    # Wprox: presence at death (max 30)
    prox = df[prox_col].astype(str).str.strip().str.lower()
    wprox = pd.Series(0.0, index=df.index)
    wprox[prox == "yes"] = 30.0
    wprox[prox == "no"] = 15.0

    # Wrec: recall period, interview date minus death date (max 20)
    death_dt = _parse_date_series(df[death_col])
    intv_dt = _parse_date_series(df[intv_col])
    recall_days = (intv_dt - death_dt).dt.total_seconds() / 86400.0

    computable_recall = recall_days.notna()
    wrec = pd.Series(np.nan, index=df.index)
    # A negative recall period (interview predating recorded death) is not
    # meaningful as a "short recall" - scored 0 rather than the naive <90 bucket.
    wrec[computable_recall & (recall_days < 0)] = 0.0
    wrec[computable_recall & (recall_days >= 0) & (recall_days < 90)] = 20.0
    wrec[computable_recall & (recall_days >= 90) & (recall_days < 180)] = 15.0
    wrec[computable_recall & (recall_days >= 180) & (recall_days < 365)] = 10.0
    wrec[computable_recall & (recall_days >= 365)] = 0.0

    # Wedu: literacy/education proxy (max 10)
    lit_col = _col(df, "id10064")
    edu_col = _col(df, "id10063")
    wedu = pd.Series(np.nan, index=df.index)
    if lit_col is not None:
        lit = df[lit_col].astype(str).str.strip().str.lower()
        wedu[lit == "yes"] = 10.0
        wedu[lit == "no"] = 5.0
    if edu_col is not None:
        edu = df[edu_col].astype(str).str.strip().str.lower()
        still_na = wedu.isna()
        wedu[still_na & edu.isin(_RRS_EDU_10)] = 10.0
        wedu[still_na & edu.isin(_RRS_EDU_5)] = 5.0
    wedu = wedu.fillna(7.0)  # unknown/dk literacy - neutral midpoint, not excluded

    rrs = wrel + wprox + wrec + wedu
    rrs = rrs.where(computable_recall)
    return rrs


# ---------------------------------------------------------------------------
# 2.3.3 Internal Consistency Index (ICI)
# ---------------------------------------------------------------------------

# rule_id -> (description, symptom-occurred field, duration field)
# Each rule is only flagged when the symptom was reported ("yes") AND both
# duration fields are present and positive - matching Table 7 / Table A3.
_ICI_DURATION_RULES = {
    "C3": ("Fever duration exceeds total illness duration", "id10147", "id10148"),
    "C4": ("Cough duration exceeds total illness duration", "id10153", "id10154"),
    "C5": ("Diarrhoea duration exceeds total illness duration", "id10181", "id10182"),
    "C6": ("Breathlessness duration exceeds total illness duration", "id10159", "id10161"),
}

# C7's two pregnancy-related symptom fields (id10109, id10110) and C8's
# maternal-death-review field (id10344) don't carry question text in the
# xForm dictionary (type/name/relevant only) - their conditions below are
# inferred from the manuscript's C7/C8 descriptions and the instrument's
# relevance-chain grouping (id10344 is only reachable via the id10305/
# id10342/id10343 maternal-death branch), not from a literal label. Worth
# a sanity check against the actual instrument wording.
ICI_RULE_DESCRIPTIONS = {
    "C1": "Pregnancy reported for a male decedent",
    "C2": "Blood reported in cough without cough",
    **{rid: desc for rid, (desc, _, _) in _ICI_DURATION_RULES.items()},
    "C7": "Pregnancy-related symptoms reported for a male decedent",
    "C8": "Maternal death questions answered for a male decedent",
    "C9": "Interview date precedes death date (temporal impossibility)",
}


def compute_ici(df: pd.DataFrame, gender_field: str = "id10019"
                 ) -> Tuple[pd.Series, pd.DataFrame, Dict[str, bool]]:
    """Per-record ICI (0-100%) plus a boolean violation-flags DataFrame (one
    column per applied rule) and a computability map for every defined rule.

    The rule set (currently C1-C9, see ICI_RULE_DESCRIPTIONS) is treated as
    open-ended: rules whose required fields are absent from the input
    DataFrame are excluded from that dataset's denominator N entirely (not
    scored as violations, not scored as passes), so N can grow as more
    rules are added without changing this function's contract.
    """
    df = _dedupe_columns(df)
    ill = _num_series(df, "id10120")

    rule_series: Dict[str, pd.Series] = {}
    rule_computable: Dict[str, bool] = {}

    # C1: pregnancy reported for a male decedent
    gender = _yn_series(df, gender_field)
    preg = _yn_series(df, "id10305")
    if gender is not None and preg is not None:
        rule_series["C1"] = (gender == "male") & (preg == "yes")
        rule_computable["C1"] = True
    else:
        rule_computable["C1"] = False

    # C2: blood reported in cough without a reported cough
    cough_yn = _yn_series(df, "id10153")
    blood = _yn_series(df, "id10157")
    if cough_yn is not None and blood is not None:
        rule_series["C2"] = (cough_yn == "no") & (blood == "yes")
        rule_computable["C2"] = True
    else:
        rule_computable["C2"] = False

    # C3-C6: symptom duration exceeds total illness duration
    for rid, (_, had_field, dur_field) in _ICI_DURATION_RULES.items():
        had = _yn_series(df, had_field)
        dur = _num_series(df, dur_field)
        if had is not None and dur is not None and ill is not None:
            rule_series[rid] = (had == "yes") & (ill > 0) & (dur > ill)
            rule_computable[rid] = True
        else:
            rule_computable[rid] = False

    # C7: pregnancy-related symptoms (id10109 and/or id10110) reported for a male
    preg_sym_1 = _yn_series(df, "id10109")
    preg_sym_2 = _yn_series(df, "id10110")
    if gender is not None and preg_sym_1 is not None and preg_sym_2 is not None:
        rule_series["C7"] = (gender == "male") & ((preg_sym_1 == "yes") | (preg_sym_2 == "yes"))
        rule_computable["C7"] = True
    else:
        rule_computable["C7"] = False

    # C8: maternal-death-review question (id10344) has any substantive answer
    # for a male decedent - it should only be reachable via the maternal-death
    # branch, so any value at all indicates the skip logic was bypassed.
    maternal_q = _yn_series(df, "id10344")
    if gender is not None and maternal_q is not None:
        rule_series["C8"] = (gender == "male") & maternal_q.isin(_BINARY_VALS)
        rule_computable["C8"] = True
    else:
        rule_computable["C8"] = False

    # C9: interview date precedes death date (temporal impossibility)
    death_col = _col(df, "id10023")
    intv_col = _col(df, "id10012")
    if death_col is not None and intv_col is not None:
        death_dt = _parse_date_series(df[death_col])
        intv_dt = _parse_date_series(df[intv_col])
        both_known = death_dt.notna() & intv_dt.notna()
        rule_series["C9"] = both_known & (intv_dt < death_dt)
        rule_computable["C9"] = True
    else:
        rule_computable["C9"] = False

    active = list(rule_series.keys())
    if not active:
        return pd.Series(np.nan, index=df.index), pd.DataFrame(index=df.index), rule_computable

    flags = pd.DataFrame({rid: rule_series[rid].fillna(False) for rid in active}, index=df.index)
    n_rules = len(active)
    n_violations = flags.sum(axis=1)
    ici = (1 - n_violations / n_rules) * 100
    return ici, flags, rule_computable


# ---------------------------------------------------------------------------
# 2.3.4 Average Interview Duration (AID)
# ---------------------------------------------------------------------------

def compute_aid(df: pd.DataFrame, max_minutes: float = 480.0) -> pd.Series:
    """Per-record interview duration in minutes (id10481 - id10011).

    Handles both full ISO 8601 datetimes (2022 instrument) and time-only
    HH:MM(:SS) values (2016 instrument), the latter via modular addition of
    1,440 minutes for an apparent midnight crossing. Records with
    non-positive or implausible (>= max_minutes) durations are excluded, as
    is the whole indicator when id10011/id10481 are absent from the dataset.
    """
    df = _dedupe_columns(df)
    start_col = _col(df, "id10011")
    end_col = _col(df, "id10481")
    if start_col is None or end_col is None:
        return pd.Series(np.nan, index=df.index)

    start_raw = df[start_col].astype(str).str.strip()
    end_raw = df[end_col].astype(str).str.strip()
    present = (
        df[start_col].notna() & df[end_col].notna()
        & (start_raw != "") & (end_raw != "")
        & (start_raw.str.lower() != "nan") & (end_raw.str.lower() != "nan")
    )

    start_ts = pd.to_datetime(start_raw, errors="coerce", utc=True)
    end_ts = pd.to_datetime(end_raw, errors="coerce", utc=True)

    minutes = pd.Series(np.nan, index=df.index)
    full_dt_mask = present & start_ts.notna() & end_ts.notna()
    minutes.loc[full_dt_mask] = (
        (end_ts - start_ts).dt.total_seconds()[full_dt_mask] / 60.0
    )

    # Time-only fallback for rows where full-datetime parsing failed
    needs_fallback = present & ~full_dt_mask
    for idx in df.index[needs_fallback]:
        s_min = _parse_time_of_day_minutes(start_raw.at[idx])
        e_min = _parse_time_of_day_minutes(end_raw.at[idx])
        if s_min is None or e_min is None:
            continue
        d = e_min - s_min
        if d < 0:
            d += 1440
        minutes.at[idx] = d

    minutes = minutes.where((minutes > 0) & (minutes < max_minutes))
    return minutes


# ---------------------------------------------------------------------------
# Orchestrator: all four indicators + dataset-level summary (2.4)
# ---------------------------------------------------------------------------

def _run_dqa_single(df: pd.DataFrame) -> dict:
    """Compute all four indicators for a single DataFrame of VA records.

    Internal helper behind the public run_dqa() (which handles multiple
    inputs, file loading, labeling, and reporting). Returns both per-record
    scores and dataset-level summary statistics (mirroring manuscript
    Tables 4-8).
    """
    df = _dedupe_columns(df)

    ics = compute_ics(df)
    rrs = compute_rrs(df)
    ici, ici_flags, ici_computable = compute_ici(df)
    aid = compute_aid(df)

    ici_block = {
        "per_record": ici,
        "summary": _dist_stats(ici),
        "tiers": _tier_counts(ici, 90, 70, ("Excellent", "Good", "Critical")),
        "rules_applied": list(ici_flags.columns),
        "rules_excluded_missing_fields": [r for r, ok in ici_computable.items() if not ok],
        "rule_violations": {
            rid: {
                "description": ICI_RULE_DESCRIPTIONS[rid],
                "n": int(ici_flags[rid].sum()),
                "pct": float(ici_flags[rid].mean() * 100),
            }
            for rid in ici_flags.columns
        },
    }
    if not ici_flags.empty:
        n_clean = int((ici_flags.sum(axis=1) == 0).sum())
        ici_block["records_passing_all_rules"] = {
            "n": n_clean, "pct": n_clean / len(ici_flags) * 100,
        }
        ici_block["mean_errors_per_record"] = float(ici_flags.sum(axis=1).mean())
    else:
        ici_block["records_passing_all_rules"] = None
        ici_block["mean_errors_per_record"] = None

    return {
        "n_records": len(df),
        "ics": {
            "computable": ics.notna().any(),
            "per_record": ics,
            "summary": _dist_stats(ics),
            "tiers": _tier_counts(ics, 90, 70, ("High", "Moderate", "Low")),
        },
        "rrs": {
            "computable": rrs.notna().any(),
            "per_record": rrs,
            "summary": _dist_stats(rrs),
            "tiers": _tier_counts(rrs, 80, 50, ("High", "Moderate", "Low")),
        },
        "ici": ici_block,
        "aid": {
            "computable": aid.notna().any(),
            "per_record": aid,
            "summary": _dist_stats(aid),
        },
    }


# ---------------------------------------------------------------------------
# Public entry point: run against one or more DataFrames or CSV files
# ---------------------------------------------------------------------------

DatasetInput = Union[str, "os.PathLike[str]", pd.DataFrame]


def run_dqa(
    *inputs: DatasetInput,
    report: bool = False,
    labels: Optional[List[str]] = None,
    title: str = "VMan3 DQA Indicator Report",
    out_dir: Optional[Union[str, "os.PathLike[str]"]] = None,
) -> Dict[str, dict]:
    """Compute ICS, RRS, ICI, and AID for one or more VA datasets.

    This is the primary entry point (`from vman_dq import run_dqa`). Each
    positional argument may be a pandas DataFrame of VA records, or a path
    to a CSV file to load. It works identically for a single dataset or
    several - the return value is always keyed by dataset label:

        run_dqa(df)                          -> {"dataset_1": {...}}
        run_dqa(df1, df2, df3)               -> {"dataset_1": {...}, "dataset_2": {...}, ...}
        run_dqa("a.csv", "b.csv")            -> {"a": {...}, "b": {...}}
        run_dqa(df1, df2, report=True)       -> {..., "report": "<markdown>"}

    Parameters
    ----------
    *inputs
        One or more DataFrames and/or CSV paths (mixing both is fine).
    report
        If True, also render a Markdown report across all inputs (see
        dqa_report()) under the "report" key. When any input was a CSV
        path, the report is additionally written to disk (out_dir, or
        "<first CSV's directory>/reports/dqa_report.md" by default).
    labels
        Optional custom label per dataset, same order as inputs. Defaults
        to the CSV filename (without extension) for file inputs, or
        "dataset_<n>" for bare DataFrames.
    out_dir
        Directory to write the report file to when report=True and at
        least one input was a CSV path.

    Returns
    -------
    dict
        {label: result-of-run_dqa-for-one-dataset, ..., "report": "<markdown>"?}
    """
    if not inputs:
        raise ValueError("run_dqa() requires at least one DataFrame or CSV file path")
    if labels is not None and len(labels) != len(inputs):
        raise ValueError("labels must have the same length as inputs")

    dataframes: Dict[str, pd.DataFrame] = {}
    first_csv_dir: Optional[Path] = None

    for i, item in enumerate(inputs):
        if isinstance(item, pd.DataFrame):
            label = labels[i] if labels else f"dataset_{i + 1}"
            df = item
        else:
            path = Path(item)
            label = labels[i] if labels else path.stem
            df = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
            if first_csv_dir is None:
                first_csv_dir = path.resolve().parent
        if label in dataframes:
            raise ValueError(f"Duplicate dataset label: {label!r} - pass explicit `labels=`")
        dataframes[label] = df

    results: Dict[str, dict] = {label: _run_dqa_single(df) for label, df in dataframes.items()}

    if report:
        from .report import dqa_report  # local import: avoids a report.py <-> dqa.py import cycle

        report_md = dqa_report(dataframes, title=title)
        results["report"] = report_md

        if first_csv_dir is not None:
            out = Path(out_dir) if out_dir else (first_csv_dir / "reports")
            out.mkdir(parents=True, exist_ok=True)
            out_path = out / "dqa_report.md"
            out_path.write_text(report_md)

    return results