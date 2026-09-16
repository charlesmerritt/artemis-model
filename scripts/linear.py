#!/usr/bin/env python3
"""Linear CLI — read-only context for agents.

Wraps the Linear GraphQL API using only the standard library.
Auth: LINEAR_API_KEY env var (loaded from .env if present).

Usage:
    linear.py project                          # find the project + summary
    linear.py project --name "Artemis"         # find by (fuzzy) name
    linear.py issues [--project NAME] [--state STATE] [--limit N]
    linear.py issue <identifier>               # e.g. ART-12
    linear.py my-issues [--limit N]            # issues assigned to the key owner
    linear.py cycles [--project NAME]          # current + upcoming cycle
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

API_URL = "https://api.linear.app/graphql"

# Default project for this repo. Override with --name.
DEFAULT_PROJECT = "ARTEMIS"


def load_env():
    """Load .env from repo root (scripts/..) without clobbering real env."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def gql(query: str, variables: dict | None = None) -> dict:
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        sys.exit("Error: LINEAR_API_KEY not set. Add it to .env or export it.")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')}")
    if body.get("errors"):
        sys.exit("GraphQL errors: " + json.dumps(body["errors"], indent=2))
    return body["data"]


def find_project(name: str | None = None) -> dict:
    """Find a project by fuzzy name, or the single active project if only one."""
    data = gql(
        """
        query($filter: ProjectFilter) {
          projects(first: 10, filter: $filter, orderBy: updatedAt) {
            nodes {
              id name description state
              targetDate startDate
              issues { nodes { id } }
              teams { nodes { key name } }
              lead { name displayName }
            }
          }
        }
        """,
        {"filter": {"name": {"containsIgnoreCase": name}}} if name else {},
    )
    nodes = data["projects"]["nodes"]
    if not nodes:
        sys.exit(f"No project found matching {name!r}. Run with no --name to list all.")
    if len(nodes) == 1 or name is None and len(nodes) == 1:
        return nodes[0]
    if name is None:
        print("Multiple projects; pick one with --name:\n")
        for p in nodes:
            print(f"  - {p['name']}  ({len(p["issues"]["nodes"])} issues)")
        sys.exit(0)
    # fuzzy pick
    lowered = name.lower()
    exact = [p for p in nodes if p["name"].lower() == lowered]
    if exact:
        return exact[0]
    contains = [p for p in nodes if lowered in p["name"].lower()]
    if len(contains) == 1:
        return contains[0]
    print(f"Ambiguous match for {name!r}:\n")
    for p in nodes:
        print(f"  - {p['name']}")
    sys.exit(0)


def print_project(p: dict):
    teams = ", ".join(t["key"] for t in p["teams"]["nodes"])
    print(f"Project: {p['name']}")
    print(f"State:   {p['state']}   Teams: {teams}")
    if p.get("lead"):
        print(f"Lead:    {p['lead'].get('displayName') or p['lead'].get('name')}")
    if p.get("startDate") or p.get("targetDate"):
        print(f"Dates:   {p.get('startDate') or '—'} → {p.get('targetDate') or '—'}")
    print(f"Issues:  {len(p["issues"]["nodes"])}")
    if p.get("description"):
        print(f"\n{p['description']}")


def cmd_project(args):
    name = DEFAULT_PROJECT
    if "--name" in args:
        name = args[args.index("--name") + 1]
    p = find_project(name)
    pid = p["id"]
    print_project(p)
    # top-level open issues as a summary
    data = gql(
        """
        query($id: String!) {
          project(id: $id) {
            issues(first: 20, filter: { state: { type: { neq: "completed" } } },
                   orderBy: updatedAt) {
              nodes { identifier title state { name type } assignee { displayName } }
            }
          }
        }
        """,
        {"id": pid},
    )
    issues = data["project"]["issues"]["nodes"]
    if issues:
        print("\nOpen issues (most recently updated):")
        for i in issues:
            who = i["assignee"]["displayName"] if i["assignee"] else "unassigned"
            print(f"  {i['identifier']}  [{i['state']['name']}] {i['title']}  ({who})")


def cmd_issues(args):
    def opt(flag):
        return args[args.index(flag) + 1] if flag in args else None

    name, state, limit = opt("--project"), opt("--state"), opt("--limit") or "20"
    filt: dict = {}
    if state:
        if state.lower() in ("done", "completed"):
            filt["state"] = {"type": {"eq": "completed"}}
        elif state.lower() in ("todo", "backlog", "unstarted"):
            filt["state"] = {"type": {"eq": "unstarted"}}
        elif state.lower() in ("in progress", "started"):
            filt["state"] = {"type": {"eq": "started"}}
        elif state.lower() == "canceled":
            filt["state"] = {"type": {"eq": "canceled"}}
        else:
            filt["state"] = {"name": {"eq": state}}
    if name:
        p = find_project(name)
        filt["project"] = {"id": {"eq": p["id"]}}
    data = gql(
        """
        query($filter: IssueFilter, $first: Int!) {
          issues(first: $first, filter: $filter, orderBy: updatedAt) {
            nodes {
              identifier title priority
              state { name }
              assignee { displayName }
              labels { nodes { name } }
              updatedAt
            }
          }
        }
        """,
        {"filter": filt, "first": int(limit)},
    )
    for i in data["issues"]["nodes"]:
        who = i["assignee"]["displayName"] if i["assignee"] else "—"
        labels = ",".join(lbl["name"] for lbl in i["labels"]["nodes"])
        print(f"{i['identifier']}  [{i['state']['name']}] P{i['priority']} {i['title']}"
              f"  ({who}{'  #' + labels if labels else ''})")


def cmd_issue(args):
    ident = next((a for a in args if not a.startswith("-")), None)
    if not ident:
        sys.exit("Usage: linear.py issue ART-12")
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) {
            identifier title description priority
            state { name }
            assignee { displayName }
            project { name }
            labels { nodes { name } }
            comments(first: 20, orderBy: createdAt) {
              nodes { body user { displayName } createdAt }
            }
          }
        }
        """,
        {"id": ident},
    )
    i = data["issue"]
    if not i:
        sys.exit(f"Issue {ident} not found.")
    who = i["assignee"]["displayName"] if i["assignee"] else "unassigned"
    print(f"{i['identifier']}: {i['title']}")
    print(f"State: {i['state']['name']}  Priority: P{i['priority']}  Assignee: {who}"
          + (f"  Project: {i['project']['name']}" if i["project"] else ""))
    if i["labels"]["nodes"]:
        print("Labels: " + ", ".join(lbl["name"] for lbl in i["labels"]["nodes"]))
    if i.get("description"):
        print(f"\n## Description\n\n{i['description']}")
    if i["comments"]["nodes"]:
        print("\n## Comments")
        for c in i["comments"]["nodes"]:
            print(f"\n--- {c['user']['displayName']} ({c['createdAt']}):\n{c['body']}")


def cmd_my_issues(args):
    limit = args[args.index("--limit") + 1] if "--limit" in args else "15"
    data = gql(
        """
        query($first: Int!) {
          viewer { displayName }
          issues(first: $first, filter: { assignee: { isMe: { eq: true } },
                          state: { type: { neq: "completed" } } },
                 orderBy: updatedAt) {
            nodes {
              identifier title state { name }
              project { name }
            }
          }
        }
        """,
        {"first": int(limit)},
    )
    print(f"Open issues assigned to {data['viewer']['displayName']}:\n")
    for i in data["issues"]["nodes"]:
        proj = f"  ({i['project']['name']})" if i["project"] else ""
        print(f"  {i['identifier']}  [{i['state']['name']}] {i['title']}{proj}")


def cmd_cycles(args):
    name = args[args.index("--project") + 1] if "--project" in args else None
    p = find_project(name)
    team = p["teams"]["nodes"][0]["key"] if p["teams"]["nodes"] else None
    if not team:
        sys.exit("Project has no teams; cannot resolve cycles.")
    data = gql(
        """
        query($team: String!) {
          team(id: $team) {
            cycles(first: 3, filter: { isActive: { eq: true } }) {
              nodes { number startsAt endsAt scope issueCount }
            }
          }
        }
        """,
        {"team": team},
    )
    for c in data["team"]["cycles"]["nodes"]:
        print(f"Cycle {c['number']}: {c['startsAt']} → {c['endsAt']}  "
              f"({c['issueCount']} issues)")


COMMANDS = {
    "project": cmd_project,
    "issues": cmd_issues,
    "issue": cmd_issue,
    "my-issues": cmd_my_issues,
    "cycles": cmd_cycles,
}

def main():
    load_env()
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        sys.exit(f"Unknown command {cmd!r}. Commands: {', '.join(COMMANDS)}")
    COMMANDS[cmd](sys.argv[2:])

if __name__ == "__main__":
    main()
