"""Gradio web interface for the Data Quality Toolkit.

Lets a user pick one of the bundled example datasets and an engine, optionally narrow
the check to a defined selection of columns and set their own outlier limits, and see the overview table and
the detailed report in the browser.

Run locally:
    python app.py
"""

import os
import sys
import signal
import logging
from pathlib import Path

import duckdb
import gradio as gr
import pandas as pd

from engines.core import inspect_data
from utils.general_utils import convert_json_to_python

logger = logging.getLogger(__name__)

# === EXAMPLE DATA ===

# Paths are built from this file's location, so the app runs from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parent
DATASETS = {
    "Sales data (CSV)": PROJECT_ROOT / "example_data" / "example-sales_data.csv",
    "Sales data (Parquet)": PROJECT_ROOT / "example_data" / "example-sales_data.parquet",
}

# (label shown in the UI, value passed to inspect_data)
ENGINES = [
    ("Pandas", "pandas"),
    ("DuckDB", "duckdb"),
]

# Shared width in pixels of the dataset, engine and limit blocks, so they line up as one row.
CHOICE_WIDTH = 240


# === STATIC UI CONTENT ===

INSTRUCTIONS_MD = """
1. **Dataset:** select the example sales data as CSV or as Parquet. Both files hold the same data.
2. **Engine:** select how the file is read.
    - pandas reads CSV, DuckDB reads Parquet.
    - A mismatched pair shows an error on purpose.
3. **Columns** (optional): click the columns to inspect, click again to remove one. Nothing selected means all columns.
4. **Lower and upper limit** (optional): your own outlier limits instead of the automatic ones.
    - They apply to every selected number column, or to all number columns if nothing is selected.
    - Fill in both fields or neither.
5. Click **Run inspect data**.

**What you get**

- **Overview table:** one row per column with counts of missing values, wrong types (pandas only), negative values, outliers and unique values.
- **Detailed report:** the same per column, plus the outlier limits used and, for pandas, the values with the wrong type.

**How outliers are found**

A value is an outlier if it lies below Q1 − 1.5 × IQR or above Q3 + 1.5 × IQR of its column. Your own limits replace this rule.

**Why two engines**

- **pandas** checks every single value and guesses its type. Good for messy CSV files, slow on big data.
- **DuckDB** trusts the column types stored in a Parquet file and runs every check as SQL, without loading the file into memory.

*The example data is synthetic and messy on purpose.*
"""

# Shown in the status line before the first run.
STATUS_HINT = "Pick a dataset and an engine, then click **Run inspect data**."


# === UI HELPERS ===

def read_column_names(dataset_name: str) -> list[str]:
    """Return the column names of an example dataset without running any check.

    Pipeline: called by on_dataset_change when a dataset is picked, and by run_check
    to apply the limits to all columns. Reads only the CSV header or the Parquet schema,
    never the data itself.

    Args:
        dataset_name: Display name of the dataset, a key of DATASETS.

    Returns:
        The column names in file order.
    """
    path = DATASETS[dataset_name]
    if path.suffix == ".csv":
        return pd.read_csv(path, nrows=0).columns.tolist()
    with duckdb.connect() as con:
        schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()
    return [row[0] for row in schema]


def on_dataset_change(dataset_name: str):
    """Fill the column buttons with the columns of the chosen dataset.

    Pipeline: wired to the dataset choice and to page load. Clears any earlier selection,
    because the other dataset may have different columns.

    Args:
        dataset_name: Display name of the chosen dataset.

    Returns:
        A gr.update for the column selection with the new choices and nothing selected.
    """
    try:
        return gr.update(choices=read_column_names(dataset_name), value=[])
    except Exception as e:
        logger.warning("on_dataset_change: could not read columns of %s (%s)", dataset_name, e)
        return gr.update(choices=[], value=[])


def parse_limit(limit_text: str, field_name: str):
    """Read one limit field as a number, or None when it is empty.

    Pipeline: called by build_ranges for the lower and the upper limit field.

    Args:
        limit_text: Text typed into the field.
        field_name: Name of the field, used in the error message.

    Returns:
        The number as float, or None when the field is empty.

    Raises:
        ValueError: If the field holds something that is not a number.
    """
    cleaned_text = (limit_text or "").strip()
    if cleaned_text == "":
        return None
    try:
        return float(cleaned_text)
    except ValueError:
        raise ValueError(f"The {field_name} must be a number, got: {cleaned_text!r}.")


def build_ranges(selected_columns, all_columns, lower_text, upper_text):
    """Turn the two limit fields into the per-column ranges the engines expect.

    Pipeline: called by run_check before inspect_data. One range applies to every
    selected column, or to every column when nothing is selected; the engines skip it
    on columns that hold no numbers.

    Args:
        selected_columns: Column names the user clicked, possibly empty.
        all_columns: All column names of the dataset.
        lower_text: Text of the lower limit field, possibly empty.
        upper_text: Text of the upper limit field, possibly empty.

    Returns:
        {column: (range_lower, range_upper)}, or None when both fields are empty.

    Raises:
        ValueError: If a field is not a number, only one field is filled, or the lower
            limit is above the upper.
    """
    range_lower = parse_limit(lower_text, "lower limit")
    range_upper = parse_limit(upper_text, "upper limit")
    if range_lower is None and range_upper is None:
        return None
    if range_lower is None or range_upper is None:
        raise ValueError("Please fill in both the lower and the upper limit, or neither.")
    if range_lower > range_upper:
        raise ValueError("The lower limit must not be above the upper limit.")
    target_columns = selected_columns or all_columns
    return {column: (range_lower, range_upper) for column in target_columns}


# === RUN HANDLER ===

def run_check(dataset_name, engine, selected_columns, lower_text, upper_text):
    """Run inspect_data on the chosen dataset and return what the UI shows.

    Pipeline: bound to the Run button. Builds the inputs, calls inspect_data, and turns
    any expected error (wrong engine for the file type, unfilled limit) into a readable
    message instead of a crash.

    Args:
        dataset_name: Display name of the chosen dataset.
        engine: "pandas" or "duckdb".
        selected_columns: Column names the user clicked, possibly empty.
        lower_text: Text of the lower limit field, possibly empty.
        upper_text: Text of the upper limit field, possibly empty.

    Returns:
        (status message, overview table, report as plain values, visibility update for
        the results area). On an error the table and report are None, the results area
        is hidden and the message says what went wrong.
    """
    try:
        all_columns = read_column_names(dataset_name)
        custom_ranges = build_ranges(selected_columns, all_columns, lower_text, upper_text)
        summary_table, report = inspect_data(
            str(DATASETS[dataset_name]),
            engine=engine,
            columns=selected_columns or None,   # an empty selection means all columns
            custom_numeric_ranges=custom_ranges,
        )
    except TypeError as e:
        # From the UI this only happens when engine and file type do not match.
        logger.info("run_check: engine/file type mismatch (%s)", e)
        if engine == "pandas":
            message = "Pandas reads CSV only. Select DuckDB for the Parquet file."
        else:
            message = "DuckDB reads Parquet only. Select Pandas for the CSV file."
        return f"⛔ {message}", None, None, gr.update(visible=False)
    except (KeyError, ValueError) as e:
        # Expected user errors (e.g. only one limit filled in): show the message as it is.
        return f"⛔ {e}", None, None, gr.update(visible=False)

    if custom_ranges:
        range_lower, range_upper = next(iter(custom_ranges.values()))
        limits_note = f" with your limits {range_lower:g} to {range_upper:g}"
    else:
        limits_note = " with automatic limits"
    status_message = (
        f"✅ Checked {len(summary_table)} column(s) of **{dataset_name}** "
        f"with **{engine}**{limits_note}."
    )
    return status_message, summary_table, convert_json_to_python(report), gr.update(visible=True)


# === STYLING ===

custom_css = """
/* Centered header block. */
.header-container {
    text-align: center;
    padding: 1.5rem 0 1rem 0;
}
.header-container h1 {
    font-size: 2.5rem !important;
    margin-bottom: 0.5rem;
}
.tech-stack {
    font-size: 1.15rem !important;
    opacity: 0.75;
}
.author-line {
    font-size: 1rem !important;
    opacity: 0.6;
    margin-top: 0.5rem;
}

/* Bold accordion titles, so the collapsed sections stand out. */
.bold-accordion .label-wrap span:first-child {
    font-weight: 700;
}

/* Larger text inside the instructions; Gradio fixes Markdown text at 14px otherwise. */
.bold-accordion .md,
.bold-accordion .md p,
.bold-accordion .md li {
    font-size: 1rem !important;
}

/* Dataset and engine options as buttons of one fixed width, stacked on top of each other. */
.stacked-choice .wrap {
    flex-direction: column;
}
.stacked-choice label {
    width: 100%;
}

/* Options look like buttons: the radio dot and tick box are hidden, the colour shows the state. */
.stacked-choice input[type="radio"],
.toggle-choice input[type="checkbox"] {
    display: none;
}
.stacked-choice label.selected,
.toggle-choice label.selected {
    background: var(--color-accent) !important;
    border-color: var(--color-accent) !important;
    color: white !important;
}

/* Extra space above each output block, so button, status, table and report read as separate parts. */
/* Gradio puts elem_classes on two nested elements, so ".block" limits these rules to the outer one. */
.block.output-block {
    margin-top: 1rem !important;
}

/* Status line in a framed box with larger text, so a warning like a missing limit is not overlooked. */
.block.status-line {
    padding: 0.75rem 1rem !important;
    border: 1px solid var(--border-color-primary);
    border-radius: var(--radius-lg);
}
.status-line .md,
.status-line .md p {
    font-size: 1.15rem !important;   /* Gradio's own text style fixes Markdown at 14px otherwise */
}

/* Section titles above the table and the report. */
.section-title h3 {
    font-size: 1.2rem;
    margin: 0;
}

/* Hide the default Gradio footer. */
footer {
    display: none !important;
}
"""


# === UI ASSEMBLY ===

with gr.Blocks(title="Data Quality Toolkit") as demo:

    gr.HTML("""
        <div class="header-container">
            <h1>📊 Data Quality Toolkit</h1>
            <p class="tech-stack">
                Pandas for small CSV · DuckDB for larger Parquet · One report for both
            </p>
            <p class="author-line">
                Built by Dennis Feyerabend ·
                <a href="https://github.com/dfeyerabend/data-quality-toolkit" target="_blank">GitHub ↗</a>
            </p>
        </div>
    """)

    with gr.Accordion("How it works", open=False, elem_classes="bold-accordion"):
        gr.Markdown(INSTRUCTIONS_MD)

    # Fixed-width blocks side by side; the empty column on the right takes up the remaining width.
    # Each block gets its own column, otherwise Gradio merges neighbouring inputs into one box.
    with gr.Row(equal_height=True):
        with gr.Column(scale=0, min_width=CHOICE_WIDTH):
            dataset_choice = gr.Radio(
                choices=list(DATASETS),
                value=next(iter(DATASETS)),
                label="Dataset",
                elem_classes="stacked-choice",
            )
        with gr.Column(scale=0, min_width=CHOICE_WIDTH):
            engine_choice = gr.Radio(
                choices=ENGINES,
                value=ENGINES[0][1],
                label="Engine",
                elem_classes="stacked-choice",
            )
        with gr.Column(scale=0, min_width=CHOICE_WIDTH):
            # Text fields, not gr.Number: an empty gr.Number shows 0, which would read as a real limit.
            lower_limit_input = gr.Textbox(label="Lower limit", placeholder="empty = automatic")
            upper_limit_input = gr.Textbox(label="Upper limit", placeholder="empty = automatic")
        gr.Column(scale=1, min_width=0)

    with gr.Accordion("Columns (nothing selected = all columns)", open=False):
        column_choice = gr.CheckboxGroup(
            choices=[],
            show_label=False,
            elem_classes="toggle-choice",
        )

    run_button = gr.Button("▶ Run inspect data", variant="primary")

    status_text = gr.Markdown(STATUS_HINT, elem_classes=["output-block", "status-line"])

    # Hidden until the first successful run, so Gradio's empty placeholders never show.
    with gr.Column(visible=False) as results_area:
        # Own headings instead of the built-in labels, which Gradio draws in a very small font.
        gr.Markdown("### Overview table", elem_classes=["output-block", "section-title"])
        summary_view = gr.Dataframe(value=pd.DataFrame(), show_label=False, interactive=False)

        gr.Markdown("### Detailed report", elem_classes=["output-block", "section-title"])
        report_view = gr.JSON(show_label=False)

    # A new dataset may have different columns, so the column buttons are rebuilt each time.
    dataset_choice.change(fn=on_dataset_change, inputs=dataset_choice, outputs=column_choice)
    demo.load(fn=on_dataset_change, inputs=dataset_choice, outputs=column_choice)

    run_button.click(
        fn=run_check,
        inputs=[dataset_choice, engine_choice, column_choice, lower_limit_input, upper_limit_input],
        outputs=[status_text, summary_view, report_view, results_area],
    )


# === GRACEFUL SHUTDOWN ===

def graceful_shutdown(signum, frame) -> None:
    """Log the received signal and exit cleanly.

    Pipeline: registered for SIGTERM and SIGINT in the entry point, so the host platform
    or a local Ctrl+C can stop the server without an error trace.

    Args:
        signum: The received signal number.
        frame: The current stack frame (unused).
    """
    logger.info("Received %s, shutting down.", signal.Signals(signum).name)
    sys.exit(0)


# === ENTRY POINT ===

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    demo.queue()

    # Railway passes the public port in $PORT; 0.0.0.0 makes the server reachable from outside the container.
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, css=custom_css)
