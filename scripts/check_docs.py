"""Documentation that can be checked, checked.

    uv run python scripts/check_docs.py

Three checks, each a rule about where knowledge lives in this repository:

1. **Doctests run.** Every module under ``pipeline/`` whose docstrings carry ``>>>``
   examples has them executed. A usage example that no longer matches the code fails here
   instead of misleading the next reader.
2. **References resolve.** A repo path cited from live code, config or docs — ``notes/<name>.md``,
   ``config/projection.yaml``, ``docs/architecture/presentation.html#schema`` — must exist,
   and a ``.html#id`` citation must name a real slide id. Dated records (``weekly-artifact/``,
   ``weekly-summary/``, ``experiments/``, ``docs/meeting-*``) are exempt: they describe the
   tree as it was. To cite something that has since been deleted, write it as
   ``git show <commit>:<path>``.
3. **Markdown is budgeted.** New ``.md`` files are refused and existing ones may not grow.
   Findings and designs go in a deck (``docs/_template/presentation.html``), rules in
   ``scripts/check_conventions.py``, usage in doctests, flags in ``--help``. When a file
   shrinks, lower its ceiling; when it is removed, delete its entry.
"""

from __future__ import annotations

import doctest
import importlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ---- 3. the markdown budget ---------------------------------------------------------------
# path -> (maximum line count, disposition). Set by the 2026-09-14 documentation audit.
# Ceilings only go down. The disposition is the audit's verdict and the work queue:
#   keep      short operational entry point; references checked
#   generated written by a script; references checked
#   record    dated record of what ran; left as history
#             (five were raised 2026-09-14/15 by an appended ownership-vocabulary note)
#   convert   durable finding or design owed a deck; delete once the deck lands
#   code      owed as docstrings, doctests, --help or a check; delete once that lands
MARKDOWN_BUDGET = {
    ".agents/skills/linear/SKILL.md": (37, "keep"),            # agent skill; linear.py --help is the usage
    "AGENTS.md": (63, "keep"),                                # the architect's agent brief
    "CLAUDE.md": (1, "keep"),                                 # @AGENTS.md
    "CONTEXT.md": (64, "keep"),                               # the project vocabulary
    "README.md": (119, "keep"),
    "data/index.md": (122, "generated"),                      # scripts/r2_index.py; regrew 2026-09-14 (+6 rows)
    "docs/adr/0001-ruderal-grassland-clean-slate.md": (36, "record"),  # accepted decision, 2026-09-13
    "docs/config-policy.md": (429, "convert"),                # -> policy deck
    "docs/ownership-regimes.md": (183, "convert"),            # -> policy deck (per-class regime tables)
    "docs/research/leto-vs-boundary-overlay.md": (120, "record"),      # dated comparison record
    "docs/superpowers/plans/2026-07-20-leto-initial-state.md": (649, "record"),
    "docs/superpowers/plans/2026-07-20-s1-segmentation-strategies.md": (570, "record"),
    "docs/superpowers/specs/2026-07-19-leto-initial-state-design.md": (232, "record"),
    "docs/superpowers/specs/2026-07-20-s1-segmentation-synthesis-design.md": (433, "record"),
    "docs/references/README.md": (145, "convert"),            # -> docs/architecture#references
    "docs/treemap_holes/README.md": (712, "convert"),         # -> docs/treemap-raster-correction
    "experiments/2026-07-28_16-33-pr9-validation-audit/report.md": (106, "record"),
    "experiments/2026-08-24_leto-ca-forest-viz/README.md": (211, "record"),
    "gee/README.md": (44, "keep"),
    "notes/2026-09-14_statewide_repair_blockers.md": (111, "record"),  # diagnosis, not prose docs
    "notes/2026-09-16_timing-offsets-evidence.md": (509, "record"),    # research note, issue #51
    "notes/ruderal-grassland-agent-feedback-loops.md": (69, "record"), # ADR companion
    "notes/todo.md": (5, "keep"),                             # the untracked-on-Linear backlog
    "notebooks/README.md": (69, "keep"),
    "pipeline/README.md": (36, "keep"),
    "pipeline/notes.md": (1, "keep"),                         # pointer line
    "pipeline/s1_initial_state/README.md": (243, "keep"),     # operational entry point
    "pipeline/s6_outputs/README.md": (166, "keep"),           # operational entry point
    "pipeline/s5_imagery/README.md": (180, "code"),                    # -> module docstrings, --help
    "research/fia_treemap_fortype/README.md": (115, "convert"),        # -> treemap deck
    "research/restart_fidelity/README.md": (204, "convert"),           # -> restart-fidelity deck
    "viewer/README.md": (126, "code"),                                 # -> serve_viewer.py --help
    "scripts/notes/README.md": (226, "keep"),                 # orientation for scripts/
    "scripts/notes/southeast-fvs-artemis-export-package.md": (375, "record"),  # 2026-06-09 handoff
    "weekly-artifact/2026-09-07/README.md": (421, "record"),
    "weekly-artifact/2026-09-14/README.md": (511, "record"),
    "weekly-artifact/2026-07-19/README.md": (111, "record"),
    "weekly-artifact/2026-07-26/README.md": (156, "record"),
    "weekly-artifact/2026-08-03/README.md": (83, "record"),
    "weekly-artifact/2026-08-10/README.md": (182, "record"),
    "weekly-artifact/2026-08-17/README.md": (210, "record"),
    "weekly-artifact/2026-08-24/README.md": (268, "record"),
    "weekly-artifact/2026-08-31/README.md": (420, "record"),
}
CHECK_REFERENCES_IN = {"keep", "generated"}

# Paths code legitimately names before they exist: outputs a command writes.
GENERATED_PATHS = {"config/fallback_treelists.lock.yaml"}

DATED_RECORDS = ("weekly-artifact/", "weekly-summary/", "experiments/", "docs/meeting-")
NOT_IN_GIT = {"data", "fvs", "output", "outputs", ".venv"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".sh", ".txt", ".toml", ".html", ".ipynb", ".R", ".json"}
TEXT_NAMES = {"Dockerfile", ".gitattributes"}


def repo_files() -> list[str]:
    """Tracked and new (not ignored) files that exist on disk."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return sorted({p for p in out if (REPO_ROOT / p).is_file()})


# ---- 1. doctests --------------------------------------------------------------------------

def check_doctests(files: list[str]) -> list[str]:
    failures = []
    modules = [
        p for p in files
        if p.startswith("pipeline/") and p.endswith(".py") and ">>>" in (REPO_ROOT / p).read_text()
    ]
    for path in modules:
        name = path[:-3].replace("/", ".").removesuffix(".__init__")
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # an import failure is a documentation failure too
            failures.append(f"{path}: import failed: {exc!r}")
            continue
        result = doctest.testmod(module, optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
        if result.failed:
            failures.append(f"{path}: {result.failed} of {result.attempted} doctest examples failed (output above)")
        else:
            print(f"ok   doctest {path} ({result.attempted} examples)")
    return failures


# ---- 2. references ------------------------------------------------------------------------

_REF = re.compile(
    r"(?<![\w/.:@$<{*-])"                                    # not mid-path, not a URL, sha:, or template
    r"((?:\.\.?/)*[\w.-]+(?:/[\w.-]+)*\.(?:md|html|py|ya?ml|sh|R|ipynb|txt|toml))"
    r"(?:#([\w-]+))?"
)


def _top_level_names(files: list[str]) -> set[str]:
    """Every first path segment the repository has ever had, so a deleted dir still counts."""
    names = {p.split("/", 1)[0] for p in files}
    head = subprocess.run(["git", "ls-tree", "--name-only", "HEAD"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.split()
    return (names | set(head)) - NOT_IN_GIT


def _slide_ids(html: Path, cache: dict[Path, set[str]]) -> set[str]:
    if html not in cache:
        cache[html] = set(re.findall(r'\bid="([\w-]+)"', html.read_text(errors="replace")))
    return cache[html]


def check_references(files: list[str]) -> list[str]:
    top = _top_level_names(files)
    ids: dict[Path, set[str]] = {}
    failures = []
    scanned = 0
    for rel in files:
        path = REPO_ROOT / rel
        if rel.startswith(DATED_RECORDS) or rel.split("/", 1)[0] in NOT_IN_GIT:
            continue
        if rel.endswith(".md") and MARKDOWN_BUDGET.get(rel, (0, "keep"))[1] not in CHECK_REFERENCES_IN:
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            continue
        scanned += 1
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if len(line) > 2000:  # an embedded data: URI, not prose
                continue
            for match in _REF.finditer(line):
                ref, fragment = match.group(1), match.group(2)
                first = ref.removeprefix("./").split("/", 1)[0]
                if not ref.startswith("../") and (first not in top or ref in GENERATED_PATHS):
                    continue  # module-local, external (/mnt/d, R2) or not yet generated
                candidates = [(path.parent / ref).resolve(), (REPO_ROOT / ref).resolve()]
                target = next((c for c in candidates if c.exists()), candidates[0])
                if not target.exists():
                    failures.append(f"{rel}:{lineno}: {ref} does not exist")
                elif fragment and target.suffix == ".html" and fragment not in _slide_ids(target, ids):
                    failures.append(f"{rel}:{lineno}: {ref}#{fragment} names no id in that deck")
    print(f"ok   references scanned in {scanned} files" if not failures else
          f"FAIL references: {len(failures)} dangling in {scanned} files")
    return failures


def check_markdown_budget(files: list[str]) -> list[str]:
    failures = []
    present = {p for p in files if p.endswith(".md")}
    for path in sorted(present):
        lines = len((REPO_ROOT / path).read_text(errors="replace").splitlines())
        ceiling = MARKDOWN_BUDGET.get(path, (None,))[0]
        if ceiling is None:
            failures.append(f"{path}: new markdown ({lines} lines). Write a deck from "
                            f"docs/_template/presentation.html, a doctest, or --help text instead.")
        elif lines > ceiling:
            failures.append(f"{path}: {lines} lines, over its ceiling of {ceiling}. Move content "
                            f"into code or a deck rather than raising the ceiling.")
    for path in sorted(set(MARKDOWN_BUDGET) - present):
        print(f"note {path} is gone — delete its MARKDOWN_BUDGET entry")
    if not failures:
        queue = {}
        for p in present:
            queue.setdefault(MARKDOWN_BUDGET[p][1], []).append(p)
        summary = ", ".join(f"{len(v)} {k}" for k, v in sorted(queue.items()))
        print(f"ok   markdown budget ({len(present)} files: {summary})")
    return failures


def main() -> int:
    files = repo_files()
    failures = check_doctests(files) + check_references(files) + check_markdown_budget(files)
    failures = list(dict.fromkeys(failures))  # a markdown link repeats its path in text and href
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
