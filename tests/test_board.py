"""tools/board and its Claude Code hooks, run against a fake ``gh`` that keeps the issue's
comments in a JSON file."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BRANCH = "young-plot-library"

FAKE_GH = '''
import base64, json, os, sys
from urllib.parse import parse_qs, urlsplit

state_file = os.environ["FAKE_GH_STATE"]
with open(state_file) as f:
    state = json.load(f)
args = [a for a in sys.argv[1:] if a != "--paginate"]
url = urlsplit(args[1])
query = parse_qs(url.query)
comments = state["comments"]
if "-f" in args:
    n = len(comments)
    body = args[args.index("-f") + 1].removeprefix("body=")
    comments.append({"id": 1000 + n, "user": "charlesmerritt", "created_at": f"2026-11-02T12:{n // 60:02d}:{n % 60:02d}Z", "body": body})
    with open(state_file, "w") as f:
        json.dump(state, f)
    print(f"https://github.com/{url.path}#issuecomment-{1000 + n}")
elif url.path.endswith("/comments"):
    if "since" in query:
        rows = [c for c in comments if c["created_at"] >= query["since"][0]]
    else:
        page = int(query["page"][0])
        rows = comments[(page - 1) * 100 : page * 100]
    for c in rows:
        print(c["id"], c["user"], c["created_at"], base64.b64encode(c["body"].encode()).decode(), sep="\\t")
else:
    print(len(comments))
'''


@dataclass
class Board:
    clone: Path
    env: dict[str, str]
    state: Path

    def run(self, *args: str, stdin: str | None = None, env: dict[str, str] | None = None):
        return self._exec([self.clone / "tools" / "board", *args], stdin, env)

    def hook(self, name: str, env: dict[str, str] | None = None):
        return self._exec([self.clone / ".claude" / "hooks" / name], None, env)

    def _exec(self, argv, stdin, env):
        return subprocess.run(
            [str(a) for a in argv], cwd=self.clone, env={**self.env, **(env or {})},
            input=stdin, capture_output=True, text=True, timeout=30,
        )

    def add(self, body: str, user: str = "charlesmerritt") -> None:
        """A comment posted by someone else: another agent, or a human in the web UI."""
        state = json.loads(self.state.read_text())
        n = len(state["comments"])
        state["comments"].append({
            "id": 1000 + n, "user": user,
            "created_at": f"2026-11-02T12:{n // 60:02d}:{n % 60:02d}Z", "body": body,
        })
        self.state.write_text(json.dumps(state))

    def bodies(self) -> list[str]:
        return [c["body"] for c in json.loads(self.state.read_text())["comments"]]


def posts(out: str) -> list[str]:
    return [p for p in re.split(r"^--- ", out, flags=re.M) if p]


def first_lines(out: str) -> list[str]:
    return [p.split("\n")[1] for p in posts(out)]


@pytest.fixture
def board(tmp_path: Path) -> Board:
    clone = tmp_path / "clone"
    (clone / "tools").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "tools" / "board", clone / "tools" / "board")
    (clone / ".claude" / "hooks").mkdir(parents=True)
    for hook in (REPO_ROOT / ".claude" / "hooks").glob("board-*.sh"):
        shutil.copy(hook, clone / ".claude" / "hooks" / hook.name)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{FAKE_GH}")
    gh.chmod(0o755)
    state = tmp_path / "gh.json"
    state.write_text(json.dumps({"comments": []}))

    # Under the pre-commit hook git exports GIT_DIR and GIT_INDEX_FILE; left in, every git
    # call below acts on the outer repository instead of the clone.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BOARD_", "GIT_"))}
    subprocess.run(["git", "init", "-q", "-b", BRANCH], cwd=clone, env=env, check=True)
    env |= {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_GH_STATE": str(state),
        "BOARD_REPO": "charlesmerritt/artemis-model",
        "CLAUDE_PROJECT_DIR": str(clone),
    }
    return Board(clone, env, state)


def test_post_needs_a_header_naming_this_branch(board):
    wrong_name = board.run("post", "[Charles's Claude · r2-tidy] wrong name")
    assert wrong_name.returncode != 0
    assert f"· {BRANCH}]" in wrong_name.stderr
    assert board.run("post", "no header").returncode != 0

    assert board.run("post", f"[Charles's Claude · {BRANCH}] done", "with #154").returncode == 0
    piped = board.run("post", stdin=f"[Charles's Codex · {BRANCH}] line one\nto: all\n")
    assert piped.returncode == 0
    assert board.bodies() == [
        f"[Charles's Claude · {BRANCH}] done with #154",
        f"[Charles's Codex · {BRANCH}] line one\nto: all",
    ]


def test_unread_hides_own_posts_and_flags_posts_addressed_here(board):
    board.add(f"[Charles's Codex · statewide-v1-v4] merged #150\nto: r2-tidy,{BRANCH}")
    board.add("[Charles's Claude · r2-tidy] R2 layout changed\nTo: statewide-v1-v4, all")
    board.add(f"[Charles's Claude · r2-tidy] not for you\nto: {BRANCH}-v2")
    board.add(f"[Charles's Claude · {BRANCH}] my own post\nto: all")
    board.add("Posted from the web UI.", user="a-teammate")

    out = board.run("unread").stdout
    assert first_lines(out) == [
        "[Charles's Codex · statewide-v1-v4] merged #150",
        "[Charles's Claude · r2-tidy] R2 layout changed",
        "[Charles's Claude · r2-tidy] not for you",
        "Posted from the web UI.",
    ]
    assert ["MENTION" in p.split("\n")[0] for p in posts(out)] == [True, True, False, False]


def test_unread_marks_posts_read(board):
    board.add("[Charles's Claude · r2-tidy] one")
    assert first_lines(board.run("unread").stdout) == ["[Charles's Claude · r2-tidy] one"]
    assert board.run("unread").stdout == ""

    board.add("[Charles's Claude · r2-tidy] two")
    assert first_lines(board.run("unread").stdout) == ["[Charles's Claude · r2-tidy] two"]


def test_first_unread_shows_only_the_last_ten_posts(board):
    for i in range(12):
        board.add(f"[Charles's Claude · r2-tidy] post {i}")
    assert first_lines(board.run("unread").stdout) == [
        f"[Charles's Claude · r2-tidy] post {i}" for i in range(2, 12)
    ]


def test_read_shows_the_last_n_posts_including_own(board):
    board.add("[Charles's Claude · r2-tidy] theirs")
    board.add(f"[Charles's Claude · {BRANCH}] mine")
    assert first_lines(board.run("read", "-n", "1").stdout) == [f"[Charles's Claude · {BRANCH}] mine"]
    assert len(posts(board.run("read", "-n", "5").stdout)) == 2


def test_board_name_overrides_the_branch(board):
    cloud = {"BOARD_NAME": "cloud-1"}
    assert board.run("whoami", env=cloud).stdout == "cloud-1\n"
    assert board.run("post", "[Charles's Claude · cloud-1] hi", env=cloud).returncode == 0


def test_detached_head_needs_a_board_name(board):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for args in (["commit", "-q", "--allow-empty", "-m", "init"], ["checkout", "-q", "--detach"]):
        subprocess.run([*git, *args], cwd=board.clone, env=board.env, check=True)
    detached = board.run("whoami")
    assert detached.returncode != 0
    assert "BOARD_NAME" in detached.stderr


def test_wait_ignores_own_posts_and_wakes_on_another_agents(board):
    board.add(f"[Charles's Claude · {BRANCH}] my own")
    assert board.run("wait", "--interval", "1", "--timeout", "1").returncode == 124

    board.add(f"[Charles's Claude · r2-tidy] for you\nto: {BRANCH}")
    woke = board.run("wait", "--interval", "1", "--timeout", "5")
    assert woke.returncode == 0
    assert "MENTION" in woke.stdout


def test_session_start_hook_shows_unread_posts_and_asks_for_a_background_wait(board):
    board.add("[Charles's Claude · r2-tidy] hello")
    started = board.hook("board-session-start.sh")
    assert started.returncode == 0
    assert BRANCH in started.stdout
    assert "[Charles's Claude · r2-tidy] hello" in started.stdout
    assert "run_in_background" in started.stdout


def test_session_start_hook_skips_the_wait_when_opted_out(board):
    started = board.hook("board-session-start.sh", env={"BOARD_WAIT": "off"})
    assert started.returncode == 0
    assert "run_in_background" not in started.stdout


def test_session_start_hook_survives_an_unreachable_board(board):
    started = board.hook("board-session-start.sh", env={"FAKE_GH_STATE": "/nonexistent"})
    assert started.returncode == 0
    assert "unavailable" in started.stdout
    assert "run_in_background" not in started.stdout


def test_prompt_hook_is_silent_until_a_post_arrives(board):
    assert board.hook("board-prompt.sh").stdout == ""
    board.add("[Charles's Claude · r2-tidy] hello")
    assert "[Charles's Claude · r2-tidy] hello" in board.hook("board-prompt.sh").stdout
    assert board.hook("board-prompt.sh").stdout == ""
