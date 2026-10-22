
# Logic for loading check data functions from engines
def inspect_data(df, engine="duckdb", custom_numeric_ranges=None):
    if engine == "pandas":
        from .pandas_engine import inspect_data_pandas
        return inspect_data_pandas(df, custom_numeric_ranges)
    elif engine == "duckdb":
        from .duckdb_engine import inspect_data_duckdb
        return inspect_data_duckdb(df, custom_numeric_ranges)
    else:
        raise ValueError("Unknown engine")

# Logic for loading fix data functions from engines
def clean_data(df, engine="duckdb"):
    if engine == "pandas":
        from .pandas_engine import clean_data_pandas
        return clean_data_pandas(df)
    elif engine == "duckdb":
        from .duckdb_engine import clean_data_duckdb
        return clean_data_duckdb(df)
    else:
        raise ValueError(f"Unknown engine: {engine}")