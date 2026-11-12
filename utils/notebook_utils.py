"""
Helper for pretty-printing nested Python objects as collapsible YAML/JSON in Jupyter.
"""

from __future__ import annotations

from pprint import pformat
from typing import Any
import html as _html  # for HTML escaping

import numpy as np
import yaml
from IPython.display import HTML, display

from utils.general_utils import convert_json_to_python


class _SafeDumper(yaml.SafeDumper):
    """Custom YAML dumper that understands numpy scalar types."""


_SafeDumper.add_multi_representer(np.integer, lambda d, v: d.represent_int(int(v)))
_SafeDumper.add_multi_representer(np.floating, lambda d, v: d.represent_float(float(v)))
_SafeDumper.add_representer(np.bool_, lambda d, v: d.represent_bool(bool(v)))


def show_struct(obj: Any, kind: str = "yaml", width: int = 140) -> None:
    """
    Pretty-print a Python object as collapsible YAML or JSON in a Jupyter notebook.

    Top-level keys (e.g. column names) become clickable sections that
    expand/collapse.

    Parameters
    ----------
    obj : Any
        The object to display (ideally a dict: {column_name: info, ...}).
    kind : {"yaml", "json"}, default "yaml"
        Output format for each section.
    width : int, default 140
        Wrap width for the printed output.
    """
    data = convert_json_to_python(obj)

    # If it's not a dict, just fall back to a single block.
    if not isinstance(data, dict):
        if kind == "yaml":
            text = yaml.dump(
                data,
                Dumper=_SafeDumper,
                sort_keys=False,
                allow_unicode=True,
                width=width,
                indent=2,
            )
        else:
            text = pformat(data, width=width, compact=True)

        display(HTML(f"<pre>{_html.escape(text)}</pre>"))
        return

    # Build collapsible HTML for each top-level key
    sections = []
    for key, value in data.items():
        if kind == "yaml":
            body = yaml.dump(
                value,
                Dumper=_SafeDumper,
                sort_keys=False,
                allow_unicode=True,
                width=width,
                indent=2,
            )
        else:
            body = pformat(value, width=width, compact=True)

        key_html = _html.escape(str(key))
        body_html = _html.escape(body)

        sections.append(
            f"""
<details>
  <summary><code>{key_html}</code></summary>
  <pre>{body_html}</pre>
</details>
"""
        )

    html = "<div style='font-family: monospace'>" + "\n".join(sections) + "</div>"
    display(HTML(html))