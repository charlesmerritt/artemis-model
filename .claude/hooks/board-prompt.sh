#!/usr/bin/env bash
# UserPromptSubmit: surface message-board posts that arrived since the last check. Silent when none; never fails.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
out="$(tools/board unread 2>/dev/null)" || exit 0
[ -n "$out" ] && printf 'New message board posts (issue #%s):\n%s\n' "${BOARD_ISSUE:-154}" "$out"
exit 0
