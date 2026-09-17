"""Repository conventions, as checks rather than prose.

Each check below is the rule. Its docstring says why the rule exists; its body is what
enforces it. Run before committing (the pre-commit hook and CI both do):

    uv run python scripts/check_conventions.py

Every check also runs a tripwire against a synthetic offender first, so a check that has
silently stopped detecting anything fails loudly instead of passing forever.

Scope is live code. Dated records — ``weekly-artifact/``, ``weekly-summary/``,
``experiments/`` — reproduce what ran on the day and are not rewritten after the fact.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LIVE_CODE = ("pipeline", "scripts", "gee", "research", "notebooks", "viewer", "main.py")


def tracked_python(roots: Iterable[str] = LIVE_CODE) -> list[Path]:
    """Tracked or new (not ignored), still-present ``.py`` files under the live-code roots."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *roots],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [REPO_ROOT / p for p in out if p.endswith(".py") and (REPO_ROOT / p).exists()]


def _docstring_nodes(tree: ast.AST) -> set[ast.AST]:
    return {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }


# ---- 1. one CRS, declared once -------------------------------------------------------------

def hardcoded_crs(source: str) -> list[int]:
    """The project CRS appears as a string *value* outside ``pipeline/spatial_ref.py``.

    EPSG:5070 is declared once, in ``config/projection.yaml``, and read through
    ``pipeline.spatial_ref.project_crs()``. A second copy agrees with the config right up
    until one of them changes, and then produces a landscape 15 m — or 400 km — off with no
    error anywhere. Mentioning the CRS in a docstring is fine; ``crs="EPSG:5070"`` is not.
    """
    from pipeline.spatial_ref import project_crs

    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip() == project_crs()
        and node not in docstrings
    ]


# ---- 2. identifiers stay exact strings -----------------------------------------------------

# FIA control numbers and the IDs derived from them: PLT_CN, STAND_CN, CN, TM_ID, MU_ID, ...
_ID_NAME = re.compile(r"(?i)^(\w+_)?(cn|tm_?id|mu_?id|stand_?id)$")
# Variables that conventionally hold the name of an ID column.
_ID_VARIABLE = re.compile(r"(?i)^(id_field|id_col(umn)?|\w*_key)$")


def _is_id_expr(node: ast.AST) -> bool:
    """Whether an expression reads an identifier column or attribute."""
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return bool(_ID_NAME.match(key.value))
        if isinstance(key, ast.Name):
            return bool(_ID_VARIABLE.match(key.id))
    if isinstance(node, ast.Attribute):
        return bool(_ID_NAME.match(node.attr))
    return False


def lossy_id_casts(source: str) -> list[int]:
    """An ID column goes through ``.astype(str)`` or ``str(int(...))``.

    FIA control numbers are integers up to 19 digits; a float64 carries 15–17. Once one has
    passed through a float, ``.astype(str)`` renders ``"236048879010661.0"`` and
    ``str(int(x))`` returns the rounded value — neither raises, and the join downstream just
    quietly matches fewer rows or the wrong plot (PR #40). Use
    ``pipeline.ids.as_id_series`` (or ``normalize_id`` for a scalar): it repairs values that
    provably survived the float and raises ``IdPrecisionError`` on ones that did not. See
    the doctests in ``pipeline/ids.py``.
    """
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # <id>.astype(str) / <id>.astype("str")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "astype"
            and len(node.args) == 1
            and (
                (isinstance(node.args[0], ast.Name) and node.args[0].id == "str")
                or (isinstance(node.args[0], ast.Constant) and node.args[0].value == "str")
            )
            and _is_id_expr(func.value)
        ):
            lines.append(node.lineno)
        # str(int(<id>))
        if (
            isinstance(func, ast.Name)
            and func.id == "str"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "int"
            and node.args[0].args
            and _is_id_expr(node.args[0].args[0])
        ):
            lines.append(node.lineno)
    return sorted(lines)


# ---- runner --------------------------------------------------------------------------------

CHECKS: list[tuple[Callable[[str], list[int]], str, set[str], str]] = [
    # (check, tripwire source that must be caught, files exempt, remedy)
    (hardcoded_crs, 'CRS = "EPSG:5070"\n', {"pipeline/spatial_ref.py"},
     "import it: from pipeline.spatial_ref import project_crs"),
    (lossy_id_casts, 'df["PLT_CN"] = df["PLT_CN"].astype(str)\nkey = str(int(rec.PLT_CN))\n',
     {"pipeline/ids.py"}, "use pipeline.ids.as_id_series / normalize_id"),
]


def main() -> int:
    files = tracked_python()
    failed = False
    for check, tripwire, exempt, remedy in CHECKS:
        if not check(tripwire):
            print(f"FAIL {check.__name__}: tripwire not detected — the check itself is broken")
            failed = True
            continue
        offenders = []
        for path in files:
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in exempt:
                continue
            offenders += [f"{rel}:{line}" for line in check(path.read_text())]
        if offenders:
            failed = True
            rule = (check.__doc__ or "").strip().splitlines()[0]
            print(f"FAIL {check.__name__}: {rule}\n  remedy: {remedy}")
            print("\n".join(f"  {o}" for o in offenders))
        else:
            print(f"ok   {check.__name__} ({len(files)} files)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
