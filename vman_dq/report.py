"""
Markdown report generator for the four DQA indicators (ICS, RRS, ICI, AID),
reproducing the table structure used in the source manuscript (Tables 3-8)
for an arbitrary set of datasets.

This module only formats results already computed by `vman_dq.dqa`; it has
no I/O of its own beyond returning a string, so it works identically
whether the input came from CSV files (local validation) or records
fetched from a database (production use).
"""

from typing import Dict, Optional

from .dqa import _run_dqa_single

__all__ = ["dqa_report"]


def _fmt(v, decimals: int = 1) -> str:
    if v is None:
        return "-"
    return f"{v:.{decimals}f}"


def _fmt_pct_n(n: Optional[int], pct: Optional[float]) -> str:
    if n is None or pct is None:
        return "-"
    return f"{n} ({pct:.1f}%)"


def _table_3_overview(results: Dict[str, dict], meta: Dict[str, dict]) -> str:
    lines = [
        "### Table 3. Dataset characteristics",
        "",
        "| Dataset | Country | WHO-VA version | N | AID computable | RRS computable |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        m = meta.get(name, {})
        aid_ok = "Yes" if r["aid"]["computable"] else "No"
        rrs_ok = "Yes" if r["rrs"]["computable"] else "No"
        lines.append(
            f"| {name} | {m.get('country', '-')} | {m.get('who_va_version', '-')} | "
            f"{r['n_records']} | {aid_ok} | {rrs_ok} |"
        )
    return "\n".join(lines)


def _table_4_ics(results: Dict[str, dict]) -> str:
    lines = [
        "### Table 4. ICS results by dataset",
        "",
        "| Dataset | N | Mean (%) | Median (%) | SD | Min (%) | 5th pct (%) | 95th pct (%) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        s = r["ics"]["summary"]
        lines.append(
            f"| {name} | {s['n']} | {_fmt(s['mean'])} | {_fmt(s['median'])} | "
            f"{_fmt(s['sd'])} | {_fmt(s['min'])} | {_fmt(s['p5'])} | {_fmt(s['p95'])} |"
        )
    return "\n".join(lines)


def _table_5_rrs_continuous(results: Dict[str, dict]) -> str:
    lines = [
        "### Table 5. RRS continuous summary by dataset",
        "",
        "| Dataset | N (eligible) | Mean | Median | SD | Min | Max |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        s = r["rrs"]["summary"]
        if s["n"] == 0:
            lines.append(f"| {name} | 0 | - | - | - | - | - |")
            continue
        lines.append(
            f"| {name} | {s['n']} | {_fmt(s['mean'])} | {_fmt(s['median'])} | "
            f"{_fmt(s['sd'])} | {_fmt(s['min'])} | {_fmt(s['max'])} |"
        )
    return "\n".join(lines)


def _table_6_rrs_tiers(results: Dict[str, dict]) -> str:
    lines = [
        "### Table 6. RRS tier distribution by dataset",
        "",
        "| Dataset | High (>= 80) | Moderate (50-79) | Low (< 50) |",
        "|---|---|---|---|",
    ]
    for name, r in results.items():
        t = r["rrs"]["tiers"]
        if not t:
            lines.append(f"| {name} | - | - | - |")
            continue
        lines.append(
            f"| {name} | {t['High']['pct']:.1f}% | {t['Moderate']['pct']:.1f}% | {t['Low']['pct']:.1f}% |"
        )
    return "\n".join(lines)


def _table_7_ici(results: Dict[str, dict]) -> str:
    names = list(results.keys())
    all_rules = []
    for r in results.values():
        for rid in r["ici"]["rules_applied"]:
            if rid not in all_rules:
                all_rules.append(rid)
    all_rules.sort()

    header = "| Rule | Description | " + " | ".join(f"{n} (n={results[n]['n_records']})" for n in names) + " |"
    sep = "|---|---|" + "---|" * len(names)
    lines = ["### Table 7. ICI results and rule-level violation counts", "", header, sep]

    from .dqa import ICI_RULE_DESCRIPTIONS
    for rid in all_rules:
        desc = ICI_RULE_DESCRIPTIONS.get(rid, "")
        row = [rid, desc]
        for name in names:
            rv = results[name]["ici"]["rule_violations"].get(rid)
            if rv is None:
                row.append("n/a (field absent)")
            else:
                row.append(_fmt_pct_n(rv["n"], rv["pct"]))
        lines.append("| " + " | ".join(row) + " |")

    pass_row = ["Records passing all applied rules", ""]
    err_row = ["Mean errors per record", ""]
    for name in names:
        pa = results[name]["ici"]["records_passing_all_rules"]
        me = results[name]["ici"]["mean_errors_per_record"]
        pass_row.append(f"{pa['pct']:.1f}%" if pa else "-")
        err_row.append(_fmt(me, 3) if me is not None else "-")
    lines.append("| " + " | ".join(pass_row) + " |")
    lines.append("| " + " | ".join(err_row) + " |")
    return "\n".join(lines)


def _table_8_aid(results: Dict[str, dict]) -> str:
    lines = [
        "### Table 8. AID results by dataset",
        "",
        "| Dataset | N (valid) | Mean (min) | Median (min) | SD | 5th pct (min) | 95th pct (min) |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        s = r["aid"]["summary"]
        if s["n"] == 0:
            lines.append(f"| {name} | 0 | - | - | - | - | - |")
            continue
        lines.append(
            f"| {name} | {s['n']} | {_fmt(s['mean'])} | {_fmt(s['median'])} | "
            f"{_fmt(s['sd'])} | {_fmt(s['p5'])} | {_fmt(s['p95'])} |"
        )
    return "\n".join(lines)


def dqa_report(
    dataframes: Dict[str, "pd.DataFrame"],
    meta: Optional[Dict[str, dict]] = None,
    title: str = "VMan3 DQA Indicator Report",
) -> str:
    """Compute all four indicators for each named DataFrame and render a
    Markdown report with the same table structure as manuscript Tables 3-8.

    Parameters
    ----------
    dataframes: mapping of dataset label -> DataFrame of VA records
    meta: optional mapping of dataset label -> {"country": ..., "who_va_version": ...},
          used only to populate Table 3
    """
    meta = meta or {}
    results = {name: _run_dqa_single(df) for name, df in dataframes.items()}

    sections = [
        f"# {title}",
        "",
        "Indicators: Informative Completeness Score (ICS), Respondent Reliability "
        "Score (RRS), Internal Consistency Index (ICI), Average Interview Duration "
        "(AID). Definitions follow *Four Practical Indicators for Real-Time Verbal "
        "Autopsy Data Quality Assessment* (Lyatuu et al.); ICI's C1 and C6-C9 "
        "follow that manuscript's methods section 2.3.3, extended with C2-C5 "
        "(age-group- and sex-restricted questions answered outside their WHO "
        "xForm-defined group, using field lists hand-verified against the WHO "
        "2016/2022 xForms' variable-mapping sheet). The rule count (N) is "
        "open-ended and reflects only the rules computable for each dataset.",
        "",
        _table_3_overview(results, meta),
        "",
        _table_4_ics(results),
        "",
        "### Table 5 & 6. Respondent Reliability Score (RRS)",
        "",
        _table_5_rrs_continuous(results),
        "",
        _table_6_rrs_tiers(results),
        "",
        _table_7_ici(results),
        "",
        _table_8_aid(results),
        "",
    ]
    return "\n".join(sections)