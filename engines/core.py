"""Base of the data-quality tool: one entry point that dispatches to an engine.

Each engine is imported only when it is used, so only its own dependencies are loaded.
"""


def inspect_data(data, engine="duckdb", *, columns=None, custom_numeric_ranges=None):
    """Run the per-column checks with the chosen engine.

    Pipeline: public entry point; dispatches to ``inspect_data_pandas`` or
    ``inspect_data_duckdb``.

    Args:
        data: For ``engine="pandas"`` a DataFrame or a path to a .csv file; for
            ``engine="duckdb"`` a path to a .parquet file.
        engine: ``"duckdb"`` or ``"pandas"``.
        columns: Optional subset of columns to inspect; all columns if None.
        custom_numeric_ranges: Optional {column: (lower, upper)} overriding the
            IQR outlier bounds for those columns.

    Returns:
        A (summary_table, report) tuple as produced by the engine.

    Raises:
        ValueError: If ``engine`` is not a known engine.
    """
    if engine == "pandas":
        from .pandas_engine import inspect_data_pandas
        return inspect_data_pandas(
            data, columns=columns, custom_numeric_ranges=custom_numeric_ranges
        )
    elif engine == "duckdb":
        from .duckdb_engine import inspect_data_duckdb
        return inspect_data_duckdb(
            data, columns=columns, custom_numeric_ranges=custom_numeric_ranges
        )
    else:
        raise ValueError(f"Unknown engine: {engine!r}. Use 'pandas' or 'duckdb'.")