"""Code-quality reports for pull requests and the retroactive sweep: CRAP scores and mutation testing.

Both measure ``pipeline/`` against the pytest suite, which is ``tests/`` plus the doctests in
``pipeline/`` (``[tool.pytest.ini_options]`` in pyproject.toml). Coverage comes first:

    uv run pytest --cov=pipeline --cov-branch --cov-report=lcov:lcov.info

**CRAP** (Change Risk Anti-Patterns, via crap4py) is ``CC² × (1 − coverage)³ + CC`` per
function. At or below 5 is low risk, above 30 is high: complex code that the tests do not
reach. Adding tests lowers the score, and so does splitting the function.

    uv run python scripts/quality.py crap --changed-since origin/main   # what a PR touched
    uv run python scripts/quality.py crap pipeline                      # the whole backlog

**Mutation testing** (mutmut) makes small edits to one file — ``<`` to ``<=``, ``+1`` — and
checks that some test fails for each. A surviving mutant is behaviour no test pins down. It
runs **one file at a time**: finish a file, killing its survivors or accepting them on
purpose, before moving to the next.

    uv run python scripts/quality.py mutate pipeline/ids.py
    uv run python scripts/quality.py mutate --changed-since origin/main
    uv run python scripts/quality.py mutate --rotation      # today's file in the retroactive sweep
    uv run mutmut show <mutant-name>                          # the diff a survivor made

Both commands report and, by default, do not fail. ``--max-crap N`` and ``--min-score PCT``
turn each into a gate. ``--summary PATH`` appends the markdown report to PATH, which is how
CI publishes it (``$GITHUB_STEP_SUMMARY``, and a PR comment).
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = "pipeline"
LOW_RISK, HIGH_RISK = 5.0, 30.0


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, **kwargs)


def source_files() -> list[str]:
    """Every tracked Python file under ``pipeline/``, sorted: the sweep's fixed order."""
    out = _run(["git", "ls-files", "--", f"{SOURCE_ROOT}/*.py"], check=True).stdout.split()
    return sorted(p for p in out if (REPO_ROOT / p).is_file())


def changed_files(base: str) -> list[str]:
    """Python files under ``pipeline/`` added, modified or renamed since the merge base with ``base``."""
    out = _run(
        ["git", "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD", "--", f"{SOURCE_ROOT}/*.py"],
        check=True,
    ).stdout.split()
    return sorted(p for p in out if (REPO_ROOT / p).is_file())


def rotation_file(today: datetime.date | None = None) -> str:
    """The file due in the retroactive sweep: one per day, cycling through every source file."""
    files = source_files()
    return files[(today or datetime.date.today()).toordinal() % len(files)]


def _emit(markdown: str, summary: Path | None) -> None:
    print(markdown)
    if summary:
        with summary.open("a") as f:
            f.write(markdown + "\n")


# ---- CRAP ---------------------------------------------------------------------------------

def parse_crap_table(text: str) -> list[dict]:
    """Rows of crap4py's text report.

    >>> parse_crap_table('''CRAP Report
    ... ===========
    ... Function   Module              CC  Cov%    CRAP
    ... ------------------------------------------------
    ... main       src/a.py       52  0.0%    2756.0
    ... helper     src/b.py       2   100.0%  2.0
    ... ''')
    [{'function': 'main', 'module': 'src/a.py', 'cc': 52, 'coverage': 0.0, 'crap': 2756.0}, {'function': 'helper', 'module': 'src/b.py', 'cc': 2, 'coverage': 100.0, 'crap': 2.0}]
    """
    rows = []
    for line in text.splitlines():
        match = re.match(r"^(\S+)\s+(\S+\.py)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)\s*$", line)
        if match:
            name, module, cc, cov, crap = match.groups()
            rows.append({"function": name, "module": module, "cc": int(cc),
                         "coverage": float(cov), "crap": float(crap)})
    return rows


def crap_markdown(rows: list[dict], scope: str, limit: int = 40) -> str:
    if not rows:
        return f"### CRAP scores — {scope}\n\nNo functions in scope."
    bands = Counter("high" if r["crap"] > HIGH_RISK else "moderate" if r["crap"] > LOW_RISK else "low" for r in rows)
    worst = sorted(rows, key=lambda r: -r["crap"])[:limit]
    lines = [
        f"### CRAP scores — {scope}",
        "",
        f"**{len(rows)} functions:** {bands['high']} high risk (> {HIGH_RISK:g}), "
        f"{bands['moderate']} moderate, {bands['low']} low (≤ {LOW_RISK:g}). "
        "CRAP = CC² × (1 − coverage)³ + CC; lower it with tests or by splitting the function.",
        "",
        "| CRAP | Function | Module | CC | Coverage |",
        "|---:|---|---|---:|---:|",
    ]
    lines += [f"| {r['crap']:.1f} | `{r['function']}` | `{r['module']}` | {r['cc']} | {r['coverage']:.0f}% |"
              for r in worst]
    if len(rows) > limit:
        lines.append(f"\n{len(rows) - limit} lower-scoring functions not shown.")
    return "\n".join(lines)


def cmd_crap(args: argparse.Namespace) -> int:
    paths = changed_files(args.changed_since) if args.changed_since else args.paths
    scope = f"files changed since `{args.changed_since}`" if args.changed_since else ", ".join(paths)
    if not paths:
        _emit(f"### CRAP scores — {scope}\n\nNo Python files under `{SOURCE_ROOT}/` changed.", args.summary)
        return 0
    if not (REPO_ROOT / args.lcov).exists():
        sys.exit(f"{args.lcov} not found; run: uv run pytest --cov={SOURCE_ROOT} --cov-branch "
                 f"--cov-report=lcov:{args.lcov}")
    result = _run(["crap4py", *paths, "--lcov", str(args.lcov)])
    if result.returncode not in (0, 1):
        sys.exit(f"crap4py failed:\n{result.stdout}{result.stderr}")
    rows = parse_crap_table(result.stdout)
    _emit(crap_markdown(rows, scope), args.summary)
    if args.max_crap is not None:
        over = [r for r in rows if r["crap"] > args.max_crap]
        if over:
            print(f"FAIL {len(over)} function(s) over --max-crap {args.max_crap:g}")
            return 1
    return 0


# ---- mutation testing ---------------------------------------------------------------------

def module_name(path: str) -> str:
    """
    >>> module_name("pipeline/s3_management/regime_assignment.py")
    'pipeline.s3_management.regime_assignment'
    """
    return path.removesuffix(".py").replace("/", ".")


def mutation_statuses(module: str) -> dict[str, str]:
    """``mutant name -> status`` for one module, from mutmut's stored results."""
    out = _run(["mutmut", "results", "--all", "true"], check=True).stdout
    statuses = {}
    for line in out.splitlines():
        name, _, status = line.strip().partition(": ")
        if name.startswith(module + "."):
            statuses[name] = status
    return statuses


def mutation_score(counts: Counter) -> float | None:
    """Killed (timeouts count as killed) over every mutant a test actually ran against.

    >>> mutation_score(Counter(killed=78, survived=91))
    46.2
    >>> mutation_score(Counter({"no tests": 5})) is None
    True
    """
    caught = counts["killed"] + counts["timeout"]
    tested = caught + counts["survived"] + counts["suspicious"]
    return round(100 * caught / tested, 1) if tested else None


def cmd_mutate(args: argparse.Namespace) -> int:
    if args.rotation:
        files, scope = [rotation_file()], "retroactive sweep"
    elif args.changed_since:
        files, scope = changed_files(args.changed_since), f"files changed since `{args.changed_since}`"
    else:
        files, scope = args.paths, "requested files"
    if not files:
        _emit(f"### Mutation testing — {scope}\n\nNo Python files under `{SOURCE_ROOT}/` to mutate.", args.summary)
        return 0

    lines = [f"### Mutation testing — {scope}", "",
             "| File | Score | Killed | Survived | No tests | Timeout | Other |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    survivors, failing = [], []
    for path in files:
        module = module_name(path)
        print(f"mutating {path} ...", file=sys.stderr, flush=True)
        run = subprocess.run(["mutmut", "run", f"{module}.*"], cwd=REPO_ROOT)
        if run.returncode not in (0, 1):
            sys.exit(f"mutmut failed on {path} (exit {run.returncode})")
        statuses = mutation_statuses(module)
        counts = Counter(statuses.values())
        score = mutation_score(counts)
        other = sum(counts.values()) - sum(counts[k] for k in ("killed", "survived", "no tests", "timeout"))
        lines.append(f"| `{path}` | {'—' if score is None else f'{score:.1f}%'} | {counts['killed']} | "
                     f"{counts['survived']} | {counts['no tests']} | {counts['timeout']} | {other} |")
        survivors += [name for name, status in statuses.items() if status == "survived"]
        if args.min_score is not None and score is not None and score < args.min_score:
            failing.append(path)

    if survivors:
        shown = survivors[: args.show_survivors]
        lines += ["", f"**{len(survivors)} surviving mutants** — behaviour no test pins down. "
                  "See each diff with `uv run mutmut show <name>`:", ""]
        lines += [f"- `{name}`" for name in shown]
        if len(survivors) > len(shown):
            lines.append(f"- … and {len(survivors) - len(shown)} more")
    _emit("\n".join(lines), args.summary)
    if failing:
        print(f"FAIL mutation score below --min-score {args.min_score:g}% in: {', '.join(failing)}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    crap = sub.add_parser("crap", help="CRAP score per function (crap4py)")
    crap.add_argument("paths", nargs="*", default=[SOURCE_ROOT])
    crap.add_argument("--changed-since", metavar="REF", help="only files changed since REF's merge base")
    crap.add_argument("--lcov", type=Path, default=Path("lcov.info"))
    crap.add_argument("--max-crap", type=float, help="fail if any function in scope scores above this")
    crap.add_argument("--summary", type=Path, help="append the markdown report to this file")
    crap.set_defaults(func=cmd_crap)

    mutate = sub.add_parser("mutate", help="mutation testing, one file at a time (mutmut)")
    mutate.add_argument("paths", nargs="*")
    which = mutate.add_mutually_exclusive_group()
    which.add_argument("--changed-since", metavar="REF", help="mutate files changed since REF's merge base")
    which.add_argument("--rotation", action="store_true", help="mutate today's file in the retroactive sweep")
    mutate.add_argument("--min-score", type=float, help="fail if a file's mutation score (%%) is below this")
    mutate.add_argument("--show-survivors", type=int, default=30)
    mutate.add_argument("--summary", type=Path, help="append the markdown report to this file")
    mutate.set_defaults(func=cmd_mutate)

    args = parser.parse_args()
    if args.command == "mutate" and not (args.paths or args.changed_since or args.rotation):
        parser.error("mutate needs file paths, --changed-since REF, or --rotation")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
