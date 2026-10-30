set -euo pipefail
cd /home/chazm/Projects/work/artemis-model-maine
export PYTHONUNBUFFERED=1
E=experiments/2026-10-19_maine-v4-leto
for MMU in 5 1; do   # 0 ac is the default build (run_rebased.sh)
  export ARTEMIS_MAINE_MIN_ACRES=$MMU
  W=$E/work/mmu$MMU
  mkdir -p $W
  uv run python $E/03_improve.py --stage counties --workers 3 > $W/03_improve.log 2>&1
  echo "MMU${MMU}_STEP3_DONE"
  uv run python $E/03_improve.py --stage young > $W/03_young.log 2>&1
  echo "MMU${MMU}_STEP3Y_DONE"
  uv run python $E/04_fia.py > $W/04_fia.log 2>&1
  echo "MMU${MMU}_STEP4_DONE"
  uv run python -W ignore $E/05_leto.py --workers 3 > $W/05_leto.log 2>&1
  echo "MMU${MMU}_STEP5_DONE"
  uv run python -W ignore $E/06_merge.py > $W/06_merge.log 2>&1
  echo "MMU${MMU}_STEP6_DONE"
done
