from pathlib import Path

import pandas as pd   # nur noch für clean_data_duckdb (Platzhalter)

from .engines_config import (
    MISSING_VALUE_TOKENS,
    IQR_FACTOR,
    UNIQUENESS_NAME_SUBSTRING,
)
from utils.general_utils import summary_from_report

# Precomputed SQL IN-list of the shared missing tokens, e.g. "na', nan, ..."
_MISSING_TOKENS_SQL = ", ".join(f"'{t}'" for t in sorted(MISSING_VALUE_TOKENS))

# DuckDB type names as returned by DESCRIBE
_INTEGER_TYPES = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
                  "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE"}

def _is_integer(dtype: str) -> bool:
    return dtype in _INTEGER_TYPES


def _is_float(dtype: str) -> bool:
    return dtype in _FLOAT_TYPES or dtype.startswith("DECIMAL")


def _is_string(dtype: str) -> bool:
    return dtype == "VARCHAR"

# -------------------
# Helper Functions
# -------------------

def build_missing_condition(col: str, dtype: str) -> str:
    """Return a DuckDB boolean expression that is TRUE when a value is missing.

    Args:
        col: Column name.
        dtype: The column's DuckDB type name.

    Returns:
        A SQL expression string.
    """
    if _is_float(dtype):
        return f"{col} IS NULL OR NOT isfinite({col})"   # NaN and +/-inf both count as missing
    elif _is_string(dtype):
        return (
            f"{col} IS NULL "
            f"OR trim({col}) = '' "
            f"OR lower(trim({col})) IN ({_MISSING_TOKENS_SQL})"
        )
    return f"{col} IS NULL"


def compute_numeric_overview(con, col, dtype, custom_range=None):
    """Compute negative-value and IQR/custom outlier counts for a numeric column.

    Args:
        con: Open DuckDB connection with the data available as ``data_table``.
        col: Column name.
        dtype: The column's DuckDB type name.
        custom_range: Optional (lower, upper) overriding the IQR bounds.

    Returns:
        A dict {n_negative, outliers:{n_low, n_high, lower, upper}}, or None for
        non-numeric columns.
    """
    if not (_is_integer(dtype) or _is_float(dtype)):
        return None

    n_negative = con.execute(
        f"SELECT SUM(CASE WHEN {col} < 0 THEN 1 ELSE 0 END) FROM data_table"
    ).fetchone()[0]
    n_negative = int(n_negative or 0)

    if custom_range is not None:
        lower_thresh, upper_thresh = custom_range
    else:
        q1, q3 = con.execute(
            f"SELECT quantile({col}, 0.25), quantile({col}, 0.75) "
            f"FROM data_table WHERE {col} IS NOT NULL AND isfinite({col})"
        ).fetchone()
        if q1 is None or q3 is None:                      # all missing: no bounds
            return {"n_negative": n_negative,
                    "outliers": {"n_low": 0, "n_high": 0, "lower": None, "upper": None}}
        iqr = float(q3 - q1)
        lower_thresh = float(q1 - IQR_FACTOR * iqr)
        upper_thresh = float(q3 + IQR_FACTOR * iqr)

    n_low, n_high = con.execute(
        f"SELECT SUM(CASE WHEN {col} < {lower_thresh} THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {col} > {upper_thresh} THEN 1 ELSE 0 END) "
        f"FROM data_table WHERE {col} IS NOT NULL AND isfinite({col})"
    ).fetchone()
    return {
        "n_negative": n_negative,
        "outliers": {
            "n_low": int(n_low or 0),
            "n_high": int(n_high or 0),
            "lower": float(lower_thresh),
            "upper": float(upper_thresh),
        },
    }


def needs_uniqueness_check(col: str, dtype: str) -> bool:
    """Return True if the column should get a uniqueness check.

    Args:
        col: Column name.
        dtype: The column's DuckDB type name.

    Returns:
        True for text columns or names containing the id marker.
    """
    if UNIQUENESS_NAME_SUBSTRING in col.lower():
        return True
    if _is_string(dtype):
        return True
    return False


# -----------------------
# Main engine functions
# -----------------------

def inspect_data_duckdb(path, custom_numeric_ranges=None):
    """Run per-column diagnostics with DuckDB and return a table and a report.

    Pipeline: DuckDB engine's check entry point, called by ``core.inspect_data``
    when ``engine="duckdb"``. The file is queried through a view and never
    loaded into memory as a whole.

    Args:
        path: Path to the .parquet file to inspect.
        custom_numeric_ranges: Optional {column: (lower, upper)} overriding the
            IQR outlier bounds for those columns.

    Returns:
        A (summary_df, report) tuple: the one-row-per-column overview table and
        the per-column report dict.

    Raises:
        TypeError: If ``path`` is not a path to a .parquet file.
    """
    import duckdb

    if not (isinstance(path, (str, Path)) and Path(path).suffix.lower() == ".parquet"):
        raise TypeError(f"Expected a path to a .parquet file, got: {path!r}")

    report: dict = {}
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW data_table AS SELECT * FROM read_parquet('{path}')")
        for col, dtype, *_ in con.execute("DESCRIBE data_table").fetchall():
            missing_cond = build_missing_condition(col, dtype)
            total, missing = con.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN {missing_cond} THEN 1 ELSE 0 END) "
                f"FROM data_table"
            ).fetchone()
            missing = int(missing or 0)
            pct_missing = round((missing / total) * 100, 2) if total else 0.0

            custom_range = None
            if custom_numeric_ranges and col in custom_numeric_ranges:
                custom_range = custom_numeric_ranges[col]
            numeric_overview = compute_numeric_overview(con, col, dtype, custom_range)

            if needs_uniqueness_check(col, dtype):
                total_u, n_unique = con.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT {col}) FROM data_table"
                ).fetchone()
                pct_unique = round((n_unique / total_u) * 100, 2) if total_u else 0.0
                uniqueness = {"n_unique": int(n_unique), "pct": pct_unique}
            else:
                uniqueness = None

            report[col] = {
                "dtype": str(dtype),
                "n_values": int(total),
                "issues": {
                    "missing_values": {"count": missing, "pct": pct_missing},
                    "numeric_overview": numeric_overview,
                    "uniqueness": uniqueness,
                    "wrong_format": None,          # type inference is pandas-only
                },
            }
    finally:
        con.close()

    return summary_from_report(report), report


def clean_data_duckdb(path):
    """Placeholder for future cleaning logic.

    Returns the path unchanged.

    Args:
        path: Path to the .parquet file.

    Returns:
        The same path, unchanged.
    """
    return path
