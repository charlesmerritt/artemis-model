#!/usr/bin/env bash
set -euo pipefail

# Reject staged Git blobs larger than GitHub's practical limit.
# Override for tests only, e.g. MAX_GIT_BLOB_BYTES=1024 ./scripts/check-staged-large-files.sh
DEFAULT_MAX_BYTES=$((99 * 1024 * 1024))
MAX_BYTES="${MAX_GIT_BLOB_BYTES:-$DEFAULT_MAX_BYTES}"

if [[ -z "$MAX_BYTES" || "$MAX_BYTES" =~ [^0-9] ]]; then
  echo "ERROR: MAX_GIT_BLOB_BYTES must be an integer byte count; got '$MAX_BYTES'." >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: large-file check must run inside a Git repository." >&2
  exit 2
}
cd "$repo_root"

human_bytes() {
  local bytes="$1"
  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec --suffix=B "$bytes"
    return
  fi
  printf '%s bytes\n' "$bytes"
}

bad_paths=()
bad_sizes=()

while IFS= read -r -d '' path; do
  # Use the staged object, not the working-tree file, so partial commits are checked correctly.
  entry="$(git ls-files -s -- "$path" | awk '$3 == 0 { print $1 " " $2; exit }')"
  [[ -n "$entry" ]] || continue

  mode="${entry%% *}"
  object_id="${entry#* }"

  # Submodules are commits, not file blobs.
  [[ "$mode" == "160000" ]] && continue

  object_type="$(git cat-file -t "$object_id" 2>/dev/null || true)"
  [[ "$object_type" == "blob" ]] || continue

  size="$(git cat-file -s "$object_id")"
  if (( size > MAX_BYTES )); then
    bad_paths+=("$path")
    bad_sizes+=("$size")
  fi
done < <(git diff --cached --name-only -z --diff-filter=ACMR)

if (( ${#bad_paths[@]} == 0 )); then
  exit 0
fi

echo "ERROR: staged file(s) exceed the Git blob limit ($(human_bytes "$MAX_BYTES"))." >&2
echo >&2
for i in "${!bad_paths[@]}"; do
  printf '  - %s (%s)\n' "${bad_paths[$i]}" "$(human_bytes "${bad_sizes[$i]}")" >&2
done
echo >&2
echo "These files would be written into Git history." >&2
echo "Move generated/data outputs under ignored paths, unstage them with:" >&2
echo "  git restore --staged <path>" >&2
echo "If the file must be versioned, use Git LFS deliberately instead of bypassing this hook." >&2
exit 1
