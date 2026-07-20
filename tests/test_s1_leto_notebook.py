"""Structural checks for the LETO initial-state walkthrough notebook."""

import ast
from pathlib import Path
import warnings

import nbformat
from nbformat.warnings import MissingIDFieldWarning

NOTEBOOK = (
    Path(__file__).resolve().parents[1]
    / "notebooks"
    / "LETO_Initial_State_Walkthrough.ipynb"
)
REQUIRED_SECTIONS = [
    "Inputs and preflight",
    "Management units and TreeMap alignment",
    "MU x PLT_CN weights",
    "FIA join coverage",
    "Species and live-tree preparation",
    "Nearest-runnable-unit imputation",
    "Initial-state map",
    "Write LETO-compatible outputs",
]


def test_walkthrough_notebook_is_valid_and_covers_leto_stages():
    with warnings.catch_warnings():
        warnings.simplefilter("error", MissingIDFieldWarning)
        notebook = nbformat.read(NOTEBOOK, as_version=4)

        nbformat.validate(notebook)
    markdown = "\n".join(
        cell.source for cell in notebook.cells if cell.cell_type == "markdown"
    )
    for section in REQUIRED_SECTIONS:
        assert section in markdown

    code = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "code")
    assert "build_plot_weights" in code
    assert "prepare_direct_tree_rows" in code
    assert "build_initial_state" in code
    assert "write_initial_state" in code


def test_walkthrough_notebook_code_parses_and_has_no_saved_outputs():
    notebook = nbformat.read(NOTEBOOK, as_version=4)

    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        ast.parse(cell.source)
        assert cell.outputs == []
        assert cell.execution_count is None
    assert "widgets" not in notebook.metadata
