"""Shared, engine-agnostic definitions for the data-quality tool.

Every value that must behave identically across engines (DuckDB, pandas) is
defined here and nowhere else, so the engines cannot drift apart. Engines import
these definitions and apply them in their own idiom (SQL for DuckDB, pandas
operations for the pandas engine).
"""

# Tokens counted as missing after a cell's text is stripped and lowercased;
# empty or whitespace-only strings are also missing (handled by the strip check).
MISSING_VALUE_TOKENS = frozenset({
    "nan", "null", "none", "n/a", "na", "missing",
})

# Multiplier for the IQR outlier bounds: lower = Q1 - k*IQR, upper = Q3 + k*IQR.
IQR_FACTOR = 1.5

# A column gets a uniqueness check if it is text-typed or its name contains this
# substring (case-insensitive), to flag key-like columns such as IDs.
UNIQUENESS_NAME_SUBSTRING = "id"
