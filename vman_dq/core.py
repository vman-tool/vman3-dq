import re
import time
import pandas as pd
import argparse
import chardet
import numpy as np
from typing import Callable, Dict, Optional
import os
from importlib.resources import files, as_file


def _parse_relevance_on_eval_df(
    eval_df: pd.DataFrame,
    col_case_mapping: Dict[str, str],
    eval_local_dict: Dict[str, pd.Series],
    relevance_expr: str,
    verbose: bool = False,
) -> pd.Series:
    """Evaluate one ODK relevance expression on a pre-built eval_df.

    Extracted from parse_odk_relevance_to_mask so that change_null_toskipped
    can build eval_df / eval_local_dict once and reuse them across all
    dictionary rows instead of copying the dataframe on every call.
    """
    expr = str(relevance_expr).strip()
    expr = ' '.join(expr.split())
    expr = str(relevance_expr).strip()  # preserves original double-strip behaviour

    def convert_selected(match):
        var = match.group(1).strip().lower()
        value = match.group(2).strip()
        actual_col = col_case_mapping.get(var, 'False')
        return f"({actual_col} == '{value}')"

    expr = re.sub(
        r"selected\(\s*\{?([^}]+)\}?\s*,\s*'([^']+)'\s*\)",
        convert_selected,
        expr,
        flags=re.IGNORECASE,
    )

    # Any ${var} not inside a selected(...) call (e.g. a direct comparison
    # like ${ageInMonthsByYear}>=48) was never substituted at all before this
    # point - selected(...) resolves its own variable via convert_selected
    # above, independent of this pass, so running this after it is safe and
    # doesn't double-substitute. Left as ${var} (and so still hits the
    # existing eval() failure path below) when the column genuinely isn't in
    # this dataframe, rather than guessing a fallback value that could
    # silently evaluate to something plausible-but-wrong.
    def convert_bare_var(match):
        var = match.group(1).strip().lower()
        actual_col = col_case_mapping.get(var)
        return actual_col if actual_col is not None else match.group(0)

    expr = re.sub(r"\$\{([^}]+)\}", convert_bare_var, expr)

    if 'selected' in expr or 'True' in expr:
        print(f"Warning: Raw boolean in expression: {expr}")

    expr = (expr
            .replace(" and ", " & ")
            .replace(" or ", " | ")
            .replace("not(", "~(")
            .replace("! =", " != ")
            .replace(">==", " >= ")
            .replace("<==", " <= ")
            .replace("?=", "==")
            )

    # XLSForm/ODK relevance syntax uses a bare '=' for equality (e.g.
    # Id10278 ='no'), not '=='. The replacements above only cover a
    # handful of adjacent-character special cases (?=, >==, <==) - this
    # catches every other bare '=' that isn't already part of a real
    # Python/pandas operator (==, !=, >=, <=), which is the far more common
    # case in practice (most relevance rules use plain '=').
    expr = re.sub(r'(?<![=!<>])=(?!=)', '==', expr)

    expr = re.sub(r'string-length\(\s*([^)]+)\s*\)\s*==\s*0', r'(\1 == "")', expr)
    expr = re.sub(r'string-length\(\s*([^)]+)\s*\)\s*>=\s*1', r'(\1 != "")', expr)

    if expr.count("(") != expr.count(")"):
        if verbose:
            print(f"Parentheses mismatch in expression: {expr}")
        return pd.Series(False, index=eval_df.index)

    try:
        return eval_df.eval(expr, engine='python', local_dict=eval_local_dict)
    except Exception as e:
        print(f"Error evaluating expression: {expr} \nThe error is: {str(e)}")
        return pd.Series(False, index=eval_df.index)


def parse_odk_relevance_to_mask(
    data_df: pd.DataFrame, relevance_expr: str, verbose: bool = False
) -> pd.Series:
    # Work with a clean copy of the DataFrame
    eval_df = data_df.copy()
    col_case_mapping = {col.lower(): col for col in eval_df.columns}

    # Convert all columns to string type for safer evaluation
    for col in eval_df.columns:
        if pd.api.types.is_object_dtype(eval_df[col]):
            eval_df[col] = eval_df[col].astype(str)

    eval_local_dict = {col: eval_df[col] for col in eval_df.columns}
    return _parse_relevance_on_eval_df(eval_df, col_case_mapping, eval_local_dict, relevance_expr, verbose)


def change_null_toskipped(
    data_df: pd.DataFrame,
    dictionary_df: Optional[pd.DataFrame] = None,
    verbose: bool = False,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> pd.DataFrame:
    """
    Populates NaN/NULL with 'skipped' in variables that were conditionally not shown.

    Parameters:
    - data_df: DataFrame containing the raw VA data
    - dictionary_df: DataFrame containing the xForm-style dictionary
    - verbose: Print debugging info if True
    - progress_callback: Optional callable(pct: int) invoked with 0-100 as the
                         relevance-rule loop progresses. Use this to drive a
                         progress bar without polling.

    Returns:
    - Updated DataFrame with 'skipped' in hidden-but-null fields
    """
    data_df = data_df.drop(columns=[col for col in data_df.columns if "_check" in col.lower()])

    if verbose:
        print(f"Number of NULLs before cleaning {data_df.isna().sum().sum():,}")

    if dictionary_df is None:
        try:
            ref = files('vman_dq.data').joinpath('dictionary.csv')
            with as_file(ref) as dict_path:
                dictionary_df = pd.read_csv(dict_path)
                if verbose:
                    print("Loaded default dictionary from package data")
        except Exception as e:
            raise ValueError("Could not load default dictionary from package") from e

    dictionary_df = dictionary_df[
        (dictionary_df['relevant'].notna()) &
        (~dictionary_df['relevant'].str.contains('\?', na=False))
    ].copy()

    col_case_mapping = {col.lower(): col for col in data_df.columns}

    dictionary_df['name'] = dictionary_df['name'].str.lower().str.strip()
    dictionary_df = dictionary_df[~dictionary_df['name'].str.contains('_check', case=False, na=False)]

    target_vars = dictionary_df[
        dictionary_df['type'].str.contains('select_one|text', na=False) &
        dictionary_df['relevant'].notna()
    ][['name', 'relevant']].copy()

    # ── Build eval_df and its support structures ONCE ─────────────────────────
    # Previously this copy + string conversion happened inside parse_odk_relevance_to_mask
    # on every dictionary row (O(n_rules) copies). Now it runs once.
    t0 = time.perf_counter()
    eval_df = data_df.copy()
    col_case_mapping_eval = {col.lower(): col for col in eval_df.columns}
    for col in eval_df.columns:
        if pd.api.types.is_object_dtype(eval_df[col]):
            eval_df[col] = eval_df[col].astype(str)
    eval_local_dict = {col: eval_df[col] for col in eval_df.columns}

    if verbose:
        print(f"eval_df prepared in {time.perf_counter() - t0:.3f}s")

    # ── Resolve dictionary names to actual df column names; drop unknowns ─────
    target_vars['df_col'] = target_vars['name'].apply(
        lambda n: col_case_mapping.get(n.strip())
    )
    target_vars = target_vars[target_vars['df_col'].notna()].reset_index(drop=True)
    n_target = len(target_vars)

    if verbose:
        print(f"Evaluating {n_target} relevance rules over {len(data_df)} records...")

    # ── Lazy null-mask cache per column ───────────────────────────────────────
    # Built on first access; invalidated when a column is written (values changed
    # from null → 'skipped', so the mask must be recomputed on next access).
    null_masks: Dict[str, pd.Series] = {}

    def _null_mask(col: str) -> pd.Series:
        if col not in null_masks:
            s = data_df[col]
            null_masks[col] = (
                s.isna() |
                s.astype(str).str.strip().str.upper().isin(["NULL", "NA", ""])
            )
        return null_masks[col]

    last_reported = -1
    t1 = time.perf_counter()

    for i, (_, row) in enumerate(target_vars.iterrows()):
        df_var_name = row['df_col']
        relevance = str(row['relevant']).strip()

        try:
            should_show = _parse_relevance_on_eval_df(
                eval_df, col_case_mapping_eval, eval_local_dict, relevance, verbose=verbose
            )

            if isinstance(should_show, pd.Series):
                mask = (~should_show) & _null_mask(df_var_name)
                if mask.any():
                    if pd.api.types.is_numeric_dtype(data_df[df_var_name]):
                        data_df[df_var_name] = data_df[df_var_name].astype(object)
                    data_df.loc[mask, df_var_name] = 'skipped'
                    null_masks.pop(df_var_name, None)  # invalidate cached mask

        except Exception as e:
            if verbose:
                print(f"Error processing '{row['name']}' with relevance '{relevance}': {str(e)}")

        if progress_callback and n_target > 0:
            pct = int((i + 1) / n_target * 100)
            if pct >= last_reported + 5 or i == n_target - 1:
                progress_callback(pct)
                last_reported = pct

    if verbose:
        print(
            f"Relevance loop: {n_target} rules × {len(data_df)} records "
            f"in {time.perf_counter() - t1:.3f}s"
        )

    # ── Age column backfill ───────────────────────────────────────────────────
    col_case_mapping = {col.lower(): col for col in data_df.columns}

    age_col      = col_case_mapping.get('ageinyears')
    age_col2     = col_case_mapping.get('ageinyears2')
    neonatal_col = col_case_mapping.get('isneonatal')
    age_adult_col = col_case_mapping.get('age_adult')

    if age_col and age_col2 in data_df.columns:
        data_df[age_col] = data_df[age_col].fillna(data_df[age_col2])
        if verbose:
            print(f"Updated {age_col} with values from {age_col2}")

    if age_col and neonatal_col in data_df.columns:
        neonatal_numeric = pd.to_numeric(data_df[neonatal_col], errors='coerce')
        data_df.loc[data_df[age_col].isna() & (neonatal_numeric == 1), age_col] = 0
        if verbose:
            print(f"Set {age_col} to 0 for neonatal cases")

    if age_col and age_adult_col in data_df.columns:
        age_adult_numeric = pd.to_numeric(data_df[age_adult_col], errors='coerce')
        data_df[age_col] = data_df[age_col].fillna(
            age_adult_numeric.where(
                (age_adult_numeric.notna()) &
                (age_adult_numeric != 999) &
                (age_adult_numeric <= 120)
            )
        )
        if verbose:
            print(f"Updated {age_col} adults if NULL with valid values from {age_adult_col}")

    if verbose:
        print(f"Number of NULLs after cleaning {data_df.isna().sum().sum():,}")
        print("\n[DEBUG check_input] Processing Complete")

    return data_df


def pyCrossVA(input: str, key: str):
    """
    inherit from pyCrossVA library
    convert WHOVA structure into ccva struture
    """
    from pycrossva.transform import transform
    ccva_data = transform(("2016WHOv151", "InterVA5"), input, raw_data_id="_key", lower=True, verbose=5)
    return ccva_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, required=True, help="Path to input CSV")
    parser.add_argument("--verbose", type=bool, required=False, help="Print output to terminal")
    args = parser.parse_args()

    with open(args.input, 'rb') as file:
        result = chardet.detect(file.read())
        encoding = result['encoding']

    print("\n Reading the input file")
    df = pd.read_csv(args.input, encoding=encoding, low_memory=False)
    change_null_toskipped(df, verbose=args.verbose)
