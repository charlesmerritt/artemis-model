"""Regenerate data/index.md from the R2 bucket's object metadata.

The catalog decays: the bucket mirrors a working drive, so folders appear, grow,
and get renamed between snapshots. This rebuilds every measured fact — object
counts, sizes, date ranges, contents, which folders the repo actually references
— and carries the prose forward.

The split matters. Sizes are facts the bucket answers for; the "what it is"
descriptions are knowledge nobody can rederive from a listing, so they are read
back out of the existing data/index.md and re-emitted against the same folder. A
folder with no description yet is written as TODO rather than guessed at, and a
folder that disappears takes its description with it.

Classification is derived, not hand-maintained: a folder is a pipeline input if
config/data_paths.yaml declares a path inside it (the config key is printed
alongside), and otherwise it is grouped by whether anything in the repo mentions
it at all.

Usage:
    uv run python scripts/r2_index.py            # rewrite data/index.md
    uv run python scripts/r2_index.py --check    # exit 1 if it would change
"""

import argparse
import collections
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / "data" / "index.md"
CONFIG_PATH = REPO_ROOT / "config" / "data_paths.yaml"

# One listing per top-level folder, in parallel. --fast-list is not optional:
# without it rclone walks prefix by prefix and a folder takes minutes, not seconds.
LIST_ARGS = ["lsf", "-R", "--fast-list", "--format", "pst"]
TIMEOUT_S = 600
WORKERS = 8

TODO = "**TODO** — describe this folder"

# The drive is a Windows volume, so its own bookkeeping rode along in the mirror.
WINDOWS_ARTEFACTS = {"$RECYCLE.BIN", "System Volume Information"}


def rclone(args: list[str]) -> str:
    proc = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"rclone {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout


def load_config() -> dict:
    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def bucket_root(cfg: dict) -> str:
    r2 = cfg["r2"]
    return f"{r2['remote']}:{r2['bucket']}/{r2['prefix']}"


class Folder:
    """Measured state of one top-level bucket folder."""

    def __init__(self, name: str, listing: str):
        self.name = name
        self.files: list[tuple[str, int, str]] = []
        for line in listing.splitlines():
            parts = line.split(";")
            if len(parts) < 3:
                continue
            path, size, modified = ";".join(parts[:-2]), parts[-2], parts[-1]
            if path.endswith("/"):
                continue
            try:
                self.files.append((path, int(size), modified[:10]))
            except ValueError:
                continue

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def total(self) -> int:
        return sum(size for _, size, _ in self.files)

    @property
    def dates(self) -> tuple[str, str]:
        stamps = sorted(when for _, _, when in self.files)
        return (stamps[0], stamps[-1]) if stamps else ("-", "-")

    @property
    def largest(self) -> list[tuple[str, int]]:
        return [(p, s) for p, s, _ in sorted(self.files, key=lambda f: -f[1])[:3]]

    @property
    def empty_files(self) -> list[str]:
        """Zero-byte objects that are not meant to be zero.

        ArcGIS writes .lock sentinels with no content by design, and Windows drops
        its own bookkeeping into the drive root; listing those would bury the one
        case that matters — a real dataset that uploaded as nothing.
        """
        if self.name in WINDOWS_ARTEFACTS:
            return []
        return [p for p, size, _ in self.files if size == 0 and not p.endswith(".lock")]

    def extensions(self, n: int = 4) -> list[tuple[str, int]]:
        counter = collections.Counter(
            (Path(p).suffix.lower() or "(none)") for p, _, _ in self.files
        )
        return counter.most_common(n)


def scan(root: str, names: list[str]) -> list[Folder]:
    def one(name: str) -> Folder:
        return Folder(name, rclone([*LIST_ARGS, f"{root}/{name}"]))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(one, names))


def top_level(root: str) -> tuple[list[str], list[tuple[str, int]]]:
    """Folder names, and the loose files sitting at the bucket root."""
    dirs = [line.rstrip("/") for line in rclone(["lsf", "--dirs-only", root]).splitlines() if line]
    files = []
    for line in rclone(["lsf", "--files-only", "--format", "ps", root]).splitlines():
        if not line:
            continue
        path, _, size = line.rpartition(";")
        files.append((path, int(size)))
    return sorted(dirs), sorted(files, key=lambda f: -f[1])


def config_keys(cfg: dict) -> dict[str, str]:
    """Bucket folder (or root file) -> the data_paths key that declares it."""
    drive = cfg["drive"].rstrip("/")
    renames = cfg["r2"].get("renames", {})
    found: dict[str, str] = {}

    def walk(node, trail: list[str]):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, [*trail, key])
        elif isinstance(node, str) and node.startswith(drive + "/"):
            head = node[len(drive) + 1:].split("/")[0]
            # Keep the shallowest key, so a folder reads as "raw.landfire", not a leaf.
            found.setdefault(renames.get(head, head), ".".join(trail[:-1]) or ".".join(trail))

    walk(cfg.get("raw", {}), ["raw"])
    return found


def referenced_in_repo(names: list[str]) -> dict[str, bool]:
    """Whether anything tracked mentions each folder, ignoring the index itself."""
    hits = {}
    for name in names:
        proc = subprocess.run(
            ["git", "grep", "-l", "-F", "--", name],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        files = {line for line in proc.stdout.splitlines() if line != "data/index.md"}
        hits[name] = bool(files)
    return hits


def existing_descriptions() -> dict[str, str]:
    """Folder -> prose, recovered from the committed index so it survives a rebuild."""
    if not INDEX_PATH.exists():
        return {}
    descriptions = {}
    row = re.compile(r"^\|\s*`([^`]+)`\s*\|[^|]*\|[^|]*\|\s*(.+?)\s*\|")
    for line in INDEX_PATH.read_text().splitlines():
        match = row.match(line)
        if match:
            name, text = match.group(1).rstrip("/"), match.group(2)
            # A trailing config-key column is regenerated, never carried over.
            text = re.sub(r"\s*\|\s*`[\w.*]+`\s*$", "", text).strip()
            if text and not text.startswith("**TODO**"):
                descriptions[name] = text
    return descriptions


def gb(size: int) -> str:
    if size >= 1e9:
        return f"{size / 1e9:.2f} GB"
    if size >= 1e6:
        return f"{size / 1e6:.1f} MB"
    return f"{size / 1e3:.0f} KB"


def render(folders: list[Folder], root_files: list[tuple[str, int]],
           cfg: dict) -> tuple[str, list[str]]:
    keys = config_keys(cfg)
    descriptions = existing_descriptions()
    referenced = referenced_in_repo([f.name for f in folders])
    by_size = sorted(folders, key=lambda f: -f.total)
    todos: list[str] = []

    total_objects = sum(f.count for f in folders) + len(root_files)
    total_bytes = sum(f.total for f in folders) + sum(s for _, s in root_files)
    repo_prefix = cfg["r2"]["repo_data_prefix"]

    def describe(name: str) -> str:
        if name in descriptions:
            return descriptions[name]
        todos.append(name)
        return TODO

    def rows(group: list[Folder], with_key: bool) -> list[str]:
        out = []
        for f in group:
            cells = [f"`{f.name}/`", gb(f.total), f"{f.count:,}", describe(f.name)]
            if with_key:
                cells.append(f"`{keys[f.name]}`" if f.name in keys else "—")
            out.append("| " + " | ".join(cells) + " |")
        return out

    declared = [f for f in by_size if f.name in keys]
    mirror = [f for f in by_size if f.name == repo_prefix]
    seen = {f.name for f in declared + mirror}
    known = [f for f in by_size if f.name not in seen and referenced[f.name]]
    unknown = [f for f in by_size if f.name not in seen and not referenced[f.name]]

    empties = [(f.name, p) for f in by_size for p in f.empty_files]

    lines = [
        f"# R2 bucket index — `{bucket_root(cfg)}`",
        "",
        "Catalog of the object-storage mirror of the `/mnt/d` workstation drive:",
        f"**{total_objects:,} objects, {total_bytes / 1e9:,.0f} GB**. Everything here is reachable",
        f"without the drive — `{cfg['drive']}/<path>` is `{bucket_root(cfg)}/<path>` — and",
        "`pipeline/data_access.py` resolves declared paths against whichever source answers.",
        "See the header of [`config/data_paths.yaml`](../config/data_paths.yaml) for access commands.",
        "",
        f"Generated by [`scripts/r2_index.py`](../scripts/r2_index.py) on {date.today().isoformat()};",
        "every number below is measured. Descriptions are written by hand and carried",
        "across regenerations, so fill in any that say TODO rather than leaving them.",
        "",
        "## Pipeline inputs",
        "",
        "Declared in `config/data_paths.yaml`; the config key is the last column.",
        "",
        "| Folder | Size | Objects | What it is | Key |",
        "|---|--:|--:|---|---|",
        *rows(declared, with_key=True),
        "",
        "## Repository data mirror",
        "",
        "| Folder | Size | Objects | What it is |",
        "|---|--:|--:|---|",
        *rows(mirror, with_key=False),
        "",
        "## Referenced by the repo, not by `data_paths.yaml`",
        "",
        "Named in a note, a research brief, or a notebook, but not declared as a path.",
        "",
        "| Folder | Size | Objects | What it is |",
        "|---|--:|--:|---|",
        *rows(known, with_key=False),
        "",
        "## Not referenced anywhere in the repo",
        "",
        "Adjacent or prior work, kept for provenance. Nothing tracked in git mentions these.",
        "",
        "| Folder | Size | Objects | What it is |",
        "|---|--:|--:|---|",
        *rows(unknown, with_key=False),
        "",
        "## Loose files at the bucket root",
        "",
        "| File | Size |",
        "|---|--:|",
        *[f"| `{name}` | {gb(size)} |" for name, size in root_files],
        "",
    ]

    if empties:
        lines += [
            "## Zero-byte objects",
            "",
            "Listed but carrying no data. Some are legitimately empty — an `__init__.py`,",
            "editor scratch, an ArcGIS index sentinel. A *dataset* here is a failed upload:",
            "",
            *[f"- `{folder}/{path}`" for folder, path in sorted(empties)],
            "",
        ]

    lines += [
        "## Refreshing this index",
        "",
        "```bash",
        "uv run python scripts/r2_index.py            # rewrite this file",
        "uv run python scripts/r2_index.py --check    # exit 1 if the bucket has drifted",
        "```",
        "",
    ]
    return "\n".join(lines), todos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report drift and exit 1 instead of writing")
    args = parser.parse_args()

    cfg = load_config()
    root = bucket_root(cfg)
    names, root_files = top_level(root)
    print(f"scanning {len(names)} folders under {root} ...", file=sys.stderr)
    folders = scan(root, names)
    rendered, todos = render(folders, root_files, cfg)

    current = INDEX_PATH.read_text() if INDEX_PATH.exists() else ""
    if args.check:
        if rendered == current:
            print("data/index.md is current", file=sys.stderr)
            return 0
        print("data/index.md is stale — re-run without --check", file=sys.stderr)
        return 1

    INDEX_PATH.write_text(rendered)
    print(f"wrote {INDEX_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
    if todos:
        print(f"{len(todos)} folder(s) need a description: {', '.join(sorted(todos))}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
