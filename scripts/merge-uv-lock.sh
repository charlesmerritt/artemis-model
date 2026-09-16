#!/usr/bin/env bash
set -euo pipefail

# Git merge driver for uv.lock. Registered by .gitattributes (`uv.lock
# merge=uv-lock`) plus a per-clone `git config merge.uv-lock.driver` — see
# .claude/hooks/session-start.sh and the README quickstart.
#
# A lockfile is a *derived* artifact: the answer to "what does pyproject.toml
# resolve to?". Two branches that both ran `uv lock` did not disagree about
# anything — each recorded a resolution of its own dependency set. Merging those
# two answers line by line yields a third resolution that neither branch ever
# produced and that uv would never generate: interleaved `[[package]]` stanzas,
# mismatched hashes, a file that may still parse as TOML while describing an
# environment that cannot be installed. That is the failure this driver exists
# to prevent.
#
# What this driver does NOT do is re-resolve the merge itself, and the reason is
# worth writing down because it looks like an obvious improvement:
#
#   At the moment a merge driver runs, git has not yet written the merged files
#   to the working tree, and MERGE_HEAD does not yet exist. Both were verified
#   directly. So the driver cannot see the merged pyproject.toml, and it cannot
#   reconstruct it either — the only inputs it receives are the three versions
#   of uv.lock. Running `uv lock` here resolves against the *pre-merge* manifest
#   and produces a lockfile that is confidently wrong: valid TOML, clean merge,
#   silently missing whatever the other branch added. That was measured too —
#   `uv lock --check` reported the result stale by four packages.
#
# So the driver does the one thing it can do correctly: take our side verbatim,
# which is always a real lockfile some branch actually produced, and leave the
# re-resolution to .githooks/post-merge, which runs after the working tree is
# updated and can see the merged pyproject.toml.
#
# Git passes: %O ancestor, %A ours (this file is the result — git reads the
# merged content back out of it), %B theirs.
ours="$2"
theirs="$3"

log() { printf 'merge-uv-lock: %s\n' "$*" >&2; }

# Identical sides. Git normally resolves this before reaching a driver, but the
# driver must not depend on that.
if cmp -s "$ours" "$theirs"; then
  exit 0
fi

# Taking `ours` unexamined would defeat the point: the whole reason for this
# driver is to guarantee uv.lock is never left in a state no `uv lock` would
# produce. If our own side does not parse, something upstream already broke it,
# and exiting non-zero hands it back as an ordinary conflict rather than
# laundering it through a clean merge.
if command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1],"rb"))' "$ours" 2>/dev/null; then
    log "our side of uv.lock is not valid TOML — refusing to auto-resolve"
    log "resolve by hand with: uv lock && git add uv.lock"
    exit 1
  fi
fi

log "kept HEAD's uv.lock; the lockfile still needs re-resolving against the merged pyproject.toml"
log "run: uv lock && git add uv.lock   (.githooks/post-merge does this automatically on a clean merge)"
exit 0
