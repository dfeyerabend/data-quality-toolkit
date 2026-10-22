"""pandas engine for the data-quality tool: column diagnostics on a DataFrame.

Produces the shared per-column report (same shape as the other engines), so a
report is interchangeable regardless of which engine built it. Cross-engine
definitions (missing tokens, IQR factor, uniqueness heuristic) come from
``engines.engines_config`` so the engines cannot drift apart.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .engines_config import (
    MISSING_VALUE_TOKENS,
    IQR_FACTOR,
    UNIQUENESS_NAME_SUBSTRING,
)
from utils.general_utils import summary_from_report

# Regex used only for per-cell type inference (numeric and ISO datetime forms).
_int_re = re.compile(r"^[+-]?\d+$")
_float_re = re.compile(r"^[+-]?((\d+\.\d+)|(\.\d+)|(\d+\.))$")
_thousands_re = re.compile(r"^[+-]?\d{1,3}(?:[,_]\d{3})+(?:\.\d+)?$")
_percent_re = re.compile(r"^[+-]?\d+(?:\.\d+)?%$")
_iso_dt_re = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?$")


# === HELPER FUNCTIONS ===

def _is_missing(val: Any) -> bool:
    """Return True if a single cell counts as missing.

    A value is missing when it is None/NA/NaN, or a string that is empty,
    whitespace-only, or a known missing token (compared case-insensitively).

    Args:
        val: The raw cell value.

    Returns:
        True if the value is considered missing, else False.
    """
    if val is None or val is pd.NA:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    if isinstance(val, str):
        s = val.strip()
        if s == "":                               # empty or whitespace-only
            return True
        return s.lower() in MISSING_VALUE_TOKENS   # named nulls, case-insensitive
    return False


def _try_datetime(s: str):
    """Parse a string to a datetime, or return None if it is not one.

    Args:
        s: The candidate date string.

    Returns:
        A parsed pandas Timestamp, or None if parsing fails.
    """
    if len(s) < 8:                  # too short for a full date: skip the costly parse
        return None
    try:
        return pd.to_datetime(s, errors="raise")
    except Exception:
        return None


def infer_scalar_type(val: object) -> str:
    """Infer the likely type of a single cell value.

    Why: a few bad cells must not retype a whole column, so the type is
    inferred per cell and the column type is taken as the majority of these.

    Args:
        val: The raw cell value.

    Returns:
        One of "missing", "bool", "datetime", "int", "float", "string", "other".
    """
    if _is_missing(val):
        return "missing"
    if isinstance(val, (bool, np.bool_)):   # before int: bool is a subclass of int
        return "bool"
    if isinstance(val, (pd.Timestamp, datetime, date)):
        return "datetime"
    if isinstance(val, (int, np.integer)) and not isinstance(val, (bool, np.bool_)):
        return "int"
    if isinstance(val, (float, np.floating)):
        return "float"
    if isinstance(val, str):
        s = val.strip()
        if _int_re.match(s):
            return "int"
        if _float_re.match(s) or _thousands_re.match(s) or _percent_re.match(s):
            return "float"
        if s.endswith("%"):
            core = s[:-1]
            if _int_re.match(core) or _float_re.match(core):
                return "float"
        try:
            num = pd.to_numeric(s)
            if not pd.isna(num):
                return "int" if float(num).is_integer() else "float"
        except Exception:
            pass
        if _iso_dt_re.match(s):
            dt = _try_datetime(s)
            if dt is not None:
                return "datetime"
        dt = _try_datetime(s)
        if dt is not None:
            return "datetime"
        return "string"
    return "other"


def infer_series_value_types(series: pd.Series) -> pd.Series:
    """Infer the scalar type of every value in a Series.

    Args:
        series: The column to classify.

    Returns:
        A Series of type-name strings, aligned to the input index.
    """
    return series.map(infer_scalar_type)


def iqr_bounds(series: pd.Series) -> Tuple[float, float]:
    """Compute IQR-based lower and upper outlier bounds for a numeric Series.

    Args:
        series: Non-missing numeric values to compute quartiles from.

    Returns:
        A (lower, upper) tuple: Q1 - IQR_FACTOR*IQR and Q3 + IQR_FACTOR*IQR.
    """
    Q1 = series.quantile(0.25)
    Q3 = series.quantile(0.75)
    IQR = Q3 - Q1
    lower = Q1 - IQR_FACTOR * IQR
    upper = Q3 + IQR_FACTOR * IQR
    return lower, upper


# === CHECK ENTRY POINT ===

def inspect_data_pandas(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    custom_numeric_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Run per-column diagnostics and return a summary table and a report.

    Pipeline: pandas engine's check entry point, called by ``core.check_data``
    when ``engine="pandas"``.

    Args:
        df: The DataFrame to inspect.
        columns: Optional subset of columns to inspect; all columns if None.
        custom_numeric_ranges: Optional {column: (lower, upper)} overriding the
            IQR outlier bounds for those columns.

    Returns:
        A (summary_df, report) tuple: summary_df has one row per column with
        aggregate counts; report is the per-column JSON-like dict.

    Raises:
        TypeError: If ``columns`` is not a list of column names.
        KeyError: If any name in ``columns`` is absent from the DataFrame.
    """
    if columns is not None:
        try:
            missing = [c for c in columns if c not in df.columns]
        except TypeError:
            raise TypeError("Columns must be a list of strings")
        if missing:
            raise KeyError(f"Columns {missing} not found in DataFrame")
        cols = list(columns)
    else:
        cols = list(df.columns)

    report: Dict[str, Any] = {}

    for col in cols:
        s = df[col]
        n = len(s)

        # Per-cell type classification drives missingness, the column type, and wrong-type counts.
        types_all = infer_series_value_types(s)
        non_missing_mask = types_all != "missing"
        missing_values_count = int((~non_missing_mask).sum())
        missing_values_rate = (missing_values_count / n * 100) if n else 0.0
        present_values_count = int(non_missing_mask.sum())

        if present_values_count == 0:
            inferred_type = None
            wrong_dtype_mask = pd.Series(False, index=s.index)
            wrong_dtype_count = 0
        else:
            inferred_type = types_all[non_missing_mask].value_counts().idxmax()  # majority non-missing type
            wrong_dtype_mask = non_missing_mask & (types_all != inferred_type)
            wrong_dtype_count = int(wrong_dtype_mask.sum())

        # Outliers and negatives only apply once the column reads as numeric.
        lower_iqr_count = upper_iqr_count = n_negative = None
        numeric_overview = None
        if inferred_type in ("int", "float"):
            temp_num = pd.to_numeric(df[col], errors="coerce")
            clean_num = temp_num.dropna()
            n_negative = int((temp_num < 0).sum())
            custom_range = custom_numeric_ranges.get(col) if custom_numeric_ranges else None
            if custom_range is not None:
                lower, upper = custom_range            # user bounds override IQR
                lower_iqr_count = int((temp_num < lower).sum())
                upper_iqr_count = int((temp_num > upper).sum())
            elif clean_num.empty:
                lower = upper = np.nan
                lower_iqr_count = upper_iqr_count = 0
            else:
                lower, upper = iqr_bounds(clean_num)
                lower_iqr_count = int((temp_num < lower).sum())
                upper_iqr_count = int((temp_num > upper).sum())
            numeric_overview = {
                "n_negative": n_negative,
                "outliers": {
                    "n_low": lower_iqr_count,
                    "n_high": upper_iqr_count,
                    "lower": lower,
                    "upper": upper,
                },
            }

        # Uniqueness flags key-like columns: text columns or names containing the id marker.
        if inferred_type == "string" or UNIQUENESS_NAME_SUBSTRING in str(col).lower():
            n_unique = int(s.nunique(dropna=True))
            unique_rate = (n_unique / n * 100) if n else 0.0
            uniqueness = {"n_unique": n_unique, "pct": round(unique_rate, 2)}
        else:
            uniqueness = None

        report[col] = {
            "dtype": inferred_type,
            "n_values": n,
            "issues": {
                "missing_values": {
                    "count": missing_values_count,
                    "pct": round(missing_values_rate, 2),
                },
                "numeric_overview": numeric_overview,
                "uniqueness": uniqueness,
                "wrong_format": {
                    "wrong_dtype_count": int(wrong_dtype_count),
                    "wrong_dtype_indices": s.index[wrong_dtype_mask].tolist(),
                    "wrong_dtype_types": types_all[wrong_dtype_mask].unique().tolist(),
                    "wrong_dtype_values": s[wrong_dtype_mask].unique().tolist(),
                },
            },
        }

    summary_df = summary_from_report(report)
    return summary_df, report


# === FIX ENTRY POINT ===

def clean_data_pandas(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    inplace: bool = False,
    rules: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Clean a DataFrame, or a selected subset of its columns.

    The cleaning transformations are not implemented yet; the function returns
    the data unchanged (a copy unless ``inplace`` is set), so callers and the
    engine API are already stable.

    Args:
        df: The input DataFrame to clean.
        columns: Subset of columns to clean. If None, all columns are considered.
        inplace: If True, operate on and return the same object; if False, work
            on a copy and return the cleaned copy.
        rules: Column-specific cleaning instructions; currently unused, kept for
            API stability.

    Returns:
        The DataFrame (the same object if ``inplace`` is True, else a copy).
    """
    if not inplace:
        df = df.copy()
    return df
