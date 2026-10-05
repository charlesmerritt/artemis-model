#!/usr/bin/env bash
# Time FVSsn over the fixture: serial per regime, then 16 parallel shards over all 600 runs.
#   research/fvsjl_engine_differential/run_fortran.sh [WORK] [FVSSN_BIN]
# Exit codes: 10 = normal FVS completion ("STOP 10"); 136 = SIGFPE, the stand FVSsn cannot
# run at all (see the design spec, section "stands FVSsn cannot finish"). Both are counted,
# never fatal, and the shell's own "Floating point exception" chatter is silenced.
set -uo pipefail
WORK=${1:-$(dirname "$0")/work}; BIN=${2:-${FVSSN_BIN:-/usr/local/bin/FVSsn}}

run_dir() {   # run every keyfile in $1; echo "<ok> <crashed>"
  local ok=0 crash=0 k rc
  cd "$1"
  for k in *.key; do
    "$BIN" --keywordfile="$k" >/dev/null 2>&1; rc=$?
    if [ "$rc" = 136 ] || [ "$rc" = 8 ]; then crash=$((crash + 1)); else ok=$((ok + 1)); fi
  done
  echo "$ok $crash"
}

for t in none thin plant; do
  rm -f "$WORK/${t}_f/out.db" "$WORK/${t}_f"/*.out
  s=$(date +%s.%N)
  read -r ok crash < <(run_dir "$WORK/${t}_f" 2>/dev/null)
  echo "$t fortran serial: $(echo "$(date +%s.%N) - $s" | bc) s  ok=$ok sigfpe=$crash"
done

rm -rf "$WORK/fpar"; i=0
for t in none thin plant; do for k in "$WORK/${t}_f"/*.key; do
  d="$WORK/fpar/s$((i % 16))"; mkdir -p "$d"; cp "$k" "$d/${t}_$(basename "$k")"; i=$((i + 1)); done; done
s=$(date +%s.%N)
for d in "$WORK"/fpar/s*; do ( run_dir "$d" >/dev/null 2>&1 ) & done
wait
echo "fortran 16-proc $i runs: $(echo "$(date +%s.%N) - $s" | bc) s"
