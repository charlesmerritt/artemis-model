#!/usr/bin/env bash
# Time FVSsn over the fixture: serial per regime, then 16 parallel shards over all 600 runs.
#   research/fvsjl_engine_differential/run_fortran.sh [WORK] [FVSSN_BIN]
# Exit codes: 0 or 10 = normal FVS completion ("STOP 10"), as in the production wrapper;
# 136 or 8 = SIGFPE, a stand FVSsn cannot finish (see the design spec, section "stands FVSsn
# cannot finish"). Anything else (127 for a missing binary, another signal) is a failed run:
# it is named on stderr and makes the script exit 1, so it never counts as "ok".
set -uo pipefail
WORK=${1:-$(dirname "$0")/work}; BIN=${2:-${FVSSN_BIN:-/usr/local/bin/FVSsn}}

run_dir() {   # run every keyfile in $1; echo "<ok> <sigfpe> <failed>"
  local ok=0 crash=0 failed=0 k rc
  cd "$1"
  for k in *.key; do
    "$BIN" --keywordfile="$k" >/dev/null 2>&1; rc=$?
    case "$rc" in
      0|10) ok=$((ok + 1)) ;;
      136|8) crash=$((crash + 1)) ;;
      *) failed=$((failed + 1)); echo "FAILED $1/$k: exit $rc" >&3 ;;
    esac
  done
  echo "$ok $crash $failed"
}

[ -x "$BIN" ] || { echo "FVSsn binary not executable: $BIN" >&2; exit 1; }
exec 3>&2
status=0

for t in none thin plant; do
  rm -f "$WORK/${t}_f/out.db" "$WORK/${t}_f"/*.out
  s=$(date +%s.%N)
  read -r ok crash failed < <(run_dir "$WORK/${t}_f" 2>/dev/null)
  echo "$t fortran serial: $(echo "$(date +%s.%N) - $s" | bc) s  ok=$ok sigfpe=$crash failed=$failed"
  [ "$failed" = 0 ] || status=1
done

rm -rf "$WORK/fpar"; i=0
for t in none thin plant; do for k in "$WORK/${t}_f"/*.key; do
  d="$WORK/fpar/s$((i % 16))"; mkdir -p "$d"; cp "$k" "$d/${t}_$(basename "$k")"; i=$((i + 1)); done; done
s=$(date +%s.%N)
for d in "$WORK"/fpar/s*; do ( run_dir "$d" >/dev/null 2>/dev/null 3>/dev/null ) & done
wait
echo "fortran 16-proc $i runs: $(echo "$(date +%s.%N) - $s" | bc) s"
exit "$status"
