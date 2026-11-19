"""`scripts/leto_upstream_changes.py` reports what upstream LETO changed since our pin."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import leto_upstream_changes as changes  # noqa: E402

# A test file inside the fake upstream clone, relative to LETO_v1_project/. Built with Path so
# scripts/check_docs.py does not read it as a reference into this repository.
UPSTREAM_TEST = Path("tests", "test_weights.py").as_posix()

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "upstream", "GIT_AUTHOR_EMAIL": "upstream@example.com",
    "GIT_COMMITTER_NAME": "upstream", "GIT_COMMITTER_EMAIL": "upstream@example.com",
    "GIT_AUTHOR_DATE": "2026-10-27T12:00:00", "GIT_COMMITTER_DATE": "2026-10-27T12:00:00",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env={**os.environ, **GIT_ENV}).stdout.strip()


def _commit(repo: Path, subject: str, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path):
    """A LETO-shaped repo: the pin, then three commits, one touching only a legacy script."""
    repo = tmp_path / "Leto"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    pin = _commit(repo, "Pinned state", {
        "LETO_v1_project/leto/stage2.py": "a = 1\n",
        "LETO_v1_project/leto/pipeline.py": "b = 1\n",
        f"LETO_v1_project/{UPSTREAM_TEST}": "def test(): pass\n",
        "LETO_v1_project/README.md": "LETO\n",
        "legacy/segment.py": "old = 1\n",
    })
    _commit(repo, "Change stage 2", {"LETO_v1_project/leto/stage2.py": "a = 1\nb = 2\nc = 3\n"})
    _commit(repo, "Move the donor filter", {
        "LETO_v1_project/leto/pipeline.py": None,
        "LETO_v1_project/leto/donor_weights.py": "def filter_weights(): pass\n",
        f"LETO_v1_project/{UPSTREAM_TEST}": "def test_moved(): pass\n",
        "LETO_v1_project/README.md": "LETO v1\n",
    })
    _commit(repo, "Tidy legacy script", {"legacy/segment.py": "old = 2\n"})
    return repo, pin


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = changes.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def _section(out: str, heading: str) -> list[str]:
    """The indented lines under a report heading such as 'Changed modules'."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    body = []
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        body.append(line.split())
    return body


def test_report_lists_every_upstream_commit_since_the_pin_oldest_first(upstream, capsys):
    repo, pin = upstream
    code, out, _ = _run(capsys, "--repo", str(repo), "--since", pin, "--ref", "main", "--fetch", "skip")
    assert code == 0
    subjects = [" ".join(row[2:]) for row in _section(out, "Commits")]
    assert subjects == ["Change stage 2", "Move the donor filter", "Tidy legacy script"]
    assert _section(out, "Commits")[0][1] == "2026-10-27"


def test_report_groups_changed_leto_files_and_ignores_paths_outside_the_v1_project(
        upstream, capsys):
    repo, pin = upstream
    _, out, _ = _run(capsys, "--repo", str(repo), "--since", pin, "--ref", "main", "--fetch", "skip")
    assert _section(out, "Changed modules") == [
        ["A", "leto/donor_weights.py", "(+1", "-0)"],
        ["D", "leto/pipeline.py", "(+0", "-1)"],
        ["M", "leto/stage2.py", "(+2", "-0)"],
    ]
    assert _section(out, "Changed tests") == [["M", UPSTREAM_TEST, "(+1", "-1)"]]
    assert _section(out, "Other changed files") == [["M", "README.md", "(+1", "-1)"]]
    assert "legacy/segment.py" not in out          # its commit is listed, its file is not


def test_changed_files_classify_modules_tests_and_configs(upstream):
    repo, pin = upstream
    _commit(repo, "Config and template", {
        "LETO_v1_project/configs/maine_test.py": "CONFIG = {}\n",
        "LETO_v1_project/data/templates/FVS_Data_Blank.db": "\0binary\0",
        "LETO_v1_project/run_leto.py": "run = 1\n",
    })
    files = {f.path: f for f in changes.changed_files(changes.Comparison(repo, pin, "main"))}
    assert files["configs/maine_test.py"].area is changes.Area.CONFIG
    assert files["run_leto.py"].area is changes.Area.MODULE
    assert files[UPSTREAM_TEST].area is changes.Area.TEST
    blank = files["data/templates/FVS_Data_Blank.db"]
    assert (blank.area, blank.status, blank.added, blank.deleted) == (changes.Area.OTHER, "A", None, None)


def test_report_says_so_when_upstream_has_not_moved_and_reads_the_clone_from_leto_repo(
        upstream, capsys, monkeypatch):
    repo, _ = upstream
    monkeypatch.setenv("LETO_REPO", str(repo))
    head = _git(repo, "rev-parse", "HEAD")
    code, out, _ = _run(capsys, "--since", head, "--ref", "main", "--fetch", "skip")
    assert code == 0
    assert "Upstream has not moved since the pin." in out
    assert "Changed modules" not in out


def test_missing_clone_fails_with_clone_instructions(tmp_path, capsys):
    code, _, err = _run(capsys, "--repo", str(tmp_path / "absent"), "--fetch", "skip")
    assert code != 0
    assert "git clone https://github.com/aauslander480/Leto" in err


def test_unknown_pin_fails_and_says_to_fetch(upstream, capsys):
    repo, _ = upstream
    code, _, err = _run(capsys, "--repo", str(repo), "--since", "0" * 40, "--fetch", "skip")
    assert code != 0
    assert "fetch" in err


def test_fetch_origin_brings_in_upstream_commits_made_after_the_clone(upstream, tmp_path, capsys):
    repo, pin = upstream
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))
    _commit(repo, "Upstream moves on", {"LETO_v1_project/leto/stage2.py": "a = 2\n"})
    _, stale, _ = _run(capsys, "--repo", str(clone), "--since", pin, "--fetch", "skip")
    _, fresh, _ = _run(capsys, "--repo", str(clone), "--since", pin, "--fetch", "origin")
    assert "Upstream moves on" not in stale
    assert "Upstream moves on" in fresh


def test_help_says_to_run_it_at_the_start_of_every_leto_ticket(capsys):
    with pytest.raises(SystemExit) as exit_:
        changes.main(["--help"])
    assert exit_.value.code == 0
    assert "start of every LETO ticket" in " ".join(capsys.readouterr().out.split())


def test_the_parity_record_pins_the_same_upstream_commit_as_the_script():
    record = (ROOT / "research/leto_parity/README.md").read_text()
    pinned = re.search(r"Ground truth is .*? at `([0-9a-f]{7,40})`", record)
    assert pinned is not None
    assert changes.PIN.startswith(pinned.group(1))
    assert len(changes.PIN) == 40
