"""What upstream LETO has changed since our pin.

Our LETO port (`pipeline/leto_*.py`) follows aauslander480/Leto's `LETO_v1_project/` at
``PIN``. This lists the upstream commits since then and the files they changed under
`LETO_v1_project/`, grouped into modules, tests, configs and everything else, against a local
clone of upstream. Run it at the start of every LETO ticket:

    uv run python scripts/leto_upstream_changes.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_URL = "https://github.com/aauslander480/Leto"
PROJECT = "LETO_v1_project"
# Also named in research/leto_parity/README.md; tests/test_leto_upstream_changes.py keeps the two equal.
PIN = "13bcf13338f287a0354de082fe3a824d25c9d571"
DEFAULT_REF = "origin/main"

EPILOG = f"""\
Run this at the start of every LETO ticket, before changing a pipeline/leto_*.py module.
For each change it reports, read the upstream diff and either port it or record the
divergence in research/leto_parity/README.md. Re-copy changed upstream tests into
tests/test_leto_upstream.py. Once our port matches, move PIN in this script and the pin in
the parity record together.

The clone is --repo, else $LETO_REPO, else ../Leto beside this repository. Make one with
  git clone {UPSTREAM_URL}
"""


class Fetch(StrEnum):
    """Whether to update the clone from upstream before comparing."""

    ORIGIN = "origin"  # git fetch origin, so origin/main is upstream's current head
    SKIP = "skip"      # compare the clone as it is


class Area(Enum):
    """Where a changed file sits in `LETO_v1_project/`, in report order."""

    MODULE = "Changed modules"
    TEST = "Changed tests"
    CONFIG = "Changed configs"
    OTHER = "Other changed files"


@dataclass(frozen=True)
class Comparison:
    """A local upstream clone, compared from ``since`` to ``ref``."""

    repo: Path
    since: str
    ref: str


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str


@dataclass(frozen=True)
class ChangedFile:
    status: str          # git's A, M or D (renames show as a D and an A)
    path: str            # relative to LETO_v1_project/
    added: int | None    # line counts; None for a binary file
    deleted: int | None

    @property
    def area(self) -> Area:
        if self.path.startswith("tests/"):
            return Area.TEST
        if self.path.startswith("configs/"):
            return Area.CONFIG
        if self.path.endswith(".py"):
            return Area.MODULE
        return Area.OTHER


class UpstreamError(RuntimeError):
    """The clone or the pin cannot be read; the message says what to do."""


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise UpstreamError(f"git {' '.join(args)} failed in {repo}:\n{exc.stderr.strip()}") from exc


def require_clone(repo: Path) -> None:
    if not (repo / ".git").exists():
        raise UpstreamError(f"No LETO clone at {repo}. Make one with\n  git clone {UPSTREAM_URL}\n"
                            f"or pass --repo / set LETO_REPO.")


def resolve(comparison: Comparison) -> str:
    """``ref``'s short hash, after checking the clone holds both ends of the comparison."""
    for rev in (comparison.since, comparison.ref):
        try:
            _git(comparison.repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        except UpstreamError:
            raise UpstreamError(f"{rev} is not in {comparison.repo}. Run with --fetch origin, or "
                                f"git -C {comparison.repo} fetch origin.") from None
    return _git(comparison.repo, "rev-parse", "--short", comparison.ref).strip()


def upstream_commits(comparison: Comparison) -> list[Commit]:
    """Every commit reachable from ``ref`` but not ``since``, oldest first."""
    out = _git(comparison.repo, "log", "--reverse", "--date=short", "--format=%h%x09%ad%x09%s",
               f"{comparison.since}..{comparison.ref}")
    return [Commit(*line.split("\t", 2)) for line in out.splitlines()]


def changed_files(comparison: Comparison) -> list[ChangedFile]:
    """Files under `LETO_v1_project/` that differ between ``since`` and ``ref``, by path."""
    def diff(fmt: str) -> list[str]:
        return _git(comparison.repo, "diff", "--no-renames", fmt, comparison.since, comparison.ref,
                    "--", PROJECT).splitlines()

    statuses = {path: status for status, path in (line.split("\t", 1) for line in diff("--name-status"))}
    files = []
    for line in diff("--numstat"):
        added, deleted, path = line.split("\t", 2)
        counts = (None, None) if added == "-" else (int(added), int(deleted))
        files.append(ChangedFile(statuses[path], path.removeprefix(f"{PROJECT}/"), *counts))
    return sorted(files, key=lambda f: f.path)


def _lines(f: ChangedFile) -> str:
    return "binary" if f.added is None else f"(+{f.added} -{f.deleted})"


def render(comparison: Comparison, resolved: str, commits: list[Commit],
           files: list[ChangedFile]) -> str:
    out = [f"Upstream LETO ({UPSTREAM_URL}, {PROJECT}/) in {comparison.repo}",
           f"  pin {comparison.since[:7]}, compared with {comparison.ref} at {resolved}", ""]
    if not commits:
        return "\n".join([*out, "Upstream has not moved since the pin."])
    out.append(f"Commits since the pin ({len(commits)}), oldest first:")
    out += [f"  {c.sha}  {c.date}  {c.subject}" for c in commits]
    for area in Area:
        group = [f for f in files if f.area is area]
        if group:
            out += ["", f"{area.value} ({len(group)}):"]
            out += [f"  {f.status}  {f.path}  {_lines(f)}" for f in group]
    return "\n".join(out)


def default_repo() -> Path:
    return Path(os.environ.get("LETO_REPO", REPO_ROOT.parent / "Leto"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, help="local clone of upstream LETO")
    parser.add_argument("--since", default=PIN, help="upstream commit to compare from (default: the pin)")
    parser.add_argument("--ref", default=DEFAULT_REF, help=f"upstream ref to compare to (default: {DEFAULT_REF})")
    parser.add_argument("--fetch", type=Fetch, choices=list(Fetch), default=Fetch.ORIGIN,
                        help="origin: git fetch origin first (default); skip: use the clone as it is")
    args = parser.parse_args(argv)
    comparison = Comparison(args.repo or default_repo(), args.since, args.ref)
    try:
        require_clone(comparison.repo)
        if args.fetch is Fetch.ORIGIN:
            _git(comparison.repo, "fetch", "--quiet", "origin")
        resolved = resolve(comparison)
        commits, files = upstream_commits(comparison), changed_files(comparison)
    except UpstreamError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(render(comparison, resolved, commits, files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
