#!/usr/bin/env bash
# SessionStart: show unread message-board posts and ask for one background `tools/board wait`. Always exits 0.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
if ! out="$(tools/board unread 2>&1)"; then
  echo "Message board unavailable ($(printf '%s' "$out" | head -n 1)). Run tools/board unread later."
  exit 0
fi
echo "Message board (GitHub issue #${BOARD_ISSUE:-154}, rules in its body): you post as $(tools/board whoami). See tools/board help."
if [ -n "$out" ]; then printf 'Unread posts:\n%s\n' "$out"; else echo "No unread posts."; fi
[ "${BOARD_WAIT:-}" = off ] && exit 0
cat <<'MSG'
Board loop: start ONE background Bash command now with run_in_background: `tools/board wait`. When it completes, its output is new posts: handle them (a MENTION means act and reply on the board; otherwise reassess your own work only if affected, no acknowledgement-only replies), then re-arm by starting `tools/board wait` in the background again. Do not run two waits at once.
MSG
exit 0
