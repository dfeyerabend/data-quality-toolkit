# 📊 Data Quality Toolkit

> 🚧 Work in progress: this repository is not final yet.

Inspect the quality of tabular datasets. The same API runs on two engines:
pandas for small, messy CSV files and DuckDB for larger, typed Parquet. Both produce the
same report, so you switch engine by data size without changing how you work.

## 🚀 What it does

Two operations behind one API:

- `inspect_data(df, engine=...)` returns an overview table (one row per column) and a detailed per column report: missing values, outliers, uniqueness, and for pandas also  type issues.

Two engines, one output:

- `engine="pandas"` for small, messy CSV. It infers each column type, so it also flags  values that do not fit (a numeric column with a stray text value).
- `engine="duckdb"` for larger, typed data from Parquet. It runs the checks as SQL.

Everything that must match across engines (what counts as missing, the IQR factor, the  uniqueness rule) lives in `engines/engines_config.py`, so both engines stay in sync.

Note: the report's `dtype` field means the declared type for DuckDB and the inferred type  for pandas.

## ⚙️ Work in progress
- Column checks work on both engines and produce the same report.

## 💾 Installation
```bash
python -m venv .venv
# activate it, then:
pip install -r requirements.txt
```

## 🚀 Usage

```python
import pandas as pd
from engines.core import inspect_data
from utils.notebook_utils import show_struct

df = pd.read_csv("example_data/example-sales_data.csv")

summary_table, report = inspect_data(df, engine="pandas") # or engine=duckdb for parquet

summary_table          # one row per column, quick overview
show_struct(report)    # collapsible per column detail in a notebook
```

- Run this snippet from the project root.
- For DuckDB, pass a Parquet file path instead of a DataFrame: `inspect_data("example_data/example-sales_data.parquet", engine="duckdb")`.
- Alternatively run the notebooks in `notebooks/`, they set up the import
path themselves, so they run as-is after cloning.
  - Run test_pandas_engine.ipynb to try the pandas example
  - Run test_duckdb_engine.ipynb to try the duckdb example

## 📁 Example data
- example_data/example-sales_data.csv: a small, synthetic, intentionally messy dataset.
- example_data/example-sales_data.parquet: the same data as typed Parquet for the DuckDB example.

## 🌳 Project structure


```
├─ engines/
│  ├─ core.py              ← engine dispatch (inspect_data)
│  ├─ pandas_engine.py     ← pandas check
│  ├─ duckdb_engine.py     ← DuckDB check
│  └─ engines_config.py    ← shared cross-engine definitions
├─ utils/
│  ├─ general_utils.py     ← builds the overview table from a report
│  └─ notebook_utils.py    ← collapsible report viewer for notebooks
├─ notebooks/              ← runnable examples (one per engine)
├─ example_data/           ← sample datasets
├─ README.md
└─ requirements.txt
```
