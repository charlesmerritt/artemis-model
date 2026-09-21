---
name: linear
description: Pull project context (project summary, issues, comments) from the Linear tracker via scripts/linear.py. Use when the user mentions Linear, an issue ID like ART-12, asks what's on the board, wants current sprint/issue context, or asks to check project status.
---

# Linear project context

This repo is tracked in Linear. Use the read-only CLI at `scripts/linear.py`
(stdlib-only Python) to fetch live context. Auth is automatic via
`LINEAR_API_KEY` in `.env` — never print or commit that key.

## Commands

```bash
python scripts/linear.py project                 # project summary + open issues
python scripts/linear.py project --name Artemis  # disambiguate by name
python scripts/linear.py issues --project Artemis --state "in progress"
python scripts/linear.py issue ART-12            # full issue: description + comments
python scripts/linear.py my-issues               # open issues assigned to the API key owner
python scripts/linear.py cycles --project Artemis
```

## When to use

- Starting work on a story/bug: fetch the issue (`issue <ID>`) and read its
  description and comments before coding.
- Planning a sprint or prioritizing: `issues --project Artemis` and
  `cycles --project Artemis`.
- Summarizing status for the user: `project`.

## Notes

- Read-only by design: this CLI cannot create or modify issues. If the user
  asks to update Linear, tell them to do it in the Linear app.
- Issue identifiers are `<TEAM_KEY>-<number>`, e.g. `ART-12`.
- If a call fails with HTTP 401, the API key is missing/revoked — ask the user
  to create a new one at Linear → Settings → Security & access → Personal API keys.
