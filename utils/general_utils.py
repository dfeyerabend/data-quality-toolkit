from typing import Any, Dict

import pandas as pd


def summary_from_report(report: Dict[str, Any]) -> pd.DataFrame:
    """Build the one-row-per-column overview table from a check report.

    Pipeline: shared by both engines so their overview tables are identical;
    each engine returns (this table, report).

    Args:
        report: Per-column report dict as produced by the check engines.

    Returns:
        A DataFrame with one row per column of aggregate counts and rates.
        Blocks an engine does not produce (e.g. type inference for DuckDB)
        leave their columns as None.
    """
    rows = []
    for col, col_report in report.items():
        issues = col_report["issues"]
        n_values = col_report["n_values"]
        n_missing = issues["missing_values"]["count"]
        n_present = n_values - n_missing

        numeric = issues.get("numeric_overview")
        if numeric is not None:
            n_negative = numeric["n_negative"]
            n_low = numeric["outliers"]["n_low"]
            n_high = numeric["outliers"]["n_high"]
        else:
            n_negative = n_low = n_high = None

        uniqueness = issues.get("uniqueness")
        if uniqueness is not None:
            n_unique = uniqueness["n_unique"]
            pct_unique = uniqueness["pct"]
        else:
            n_unique = pct_unique = None

        wrong = issues.get("wrong_format")
        if wrong is not None:
            n_wrong_dtype = wrong["wrong_dtype_count"]
            pct_wrong_dtype = round(n_wrong_dtype / n_present * 100, 2) if n_present else 0.0
        else:
            n_wrong_dtype = pct_wrong_dtype = None

        rows.append({
            "column": col,
            "dtype": col_report["dtype"],
            "n_values": n_values,
            "n_missing": n_missing,
            "pct_missing": issues["missing_values"]["pct"],
            "n_present": n_present,
            "n_wrong_dtype": n_wrong_dtype,
            "pct_wrong_dtype": pct_wrong_dtype,
            "n_negative": n_negative,
            "n_outlier_low": n_low,
            "n_outlier_high": n_high,
            "n_unique": n_unique,
            "pct_unique": pct_unique,
        })
    return pd.DataFrame(rows)
