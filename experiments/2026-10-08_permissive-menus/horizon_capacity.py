"""Step 3 — is the residual miss a menu limit or a timber limit?

A sound upper bound on what a dimension key can harvest over the whole horizon: every stand
takes the option with its largest 50-year total, independently. If that falls short of ten
cycles of target, no plan from this library can meet the key's even flow, whatever the
search does. Unlike 2026-09-14's per-cycle envelope, which lets each stand pick per cycle and
so never binds here, this one cannot be met by re-timing.

    uv run python experiments/2026-10-08_permissive-menus/horizon_capacity.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("schedule_step", EXP / "02_schedule.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
AP = S.AP

cfg = AP.load_config()
caps = AP.tpo_caps(cfg["target_period"])
stands = pd.read_csv(S.STANDS, dtype=S.ID_COLS)
cycles = pd.read_csv(S.CYCLES, dtype={"PLT_CN": str})
plan_dev = pd.read_csv(EXP / "outputs/deviation_by_cycle.csv")
rows = []
for v in S.menus.VARIANTS:
    lib = pd.read_csv(S.WORK / f"library_{v.name}.csv", dtype={"PLT_CN": str, "unit_id": str})
    runs = lib.drop_duplicates(["PLT_CN", "prescription"])[["PLT_CN", "prescription", "run_hash"]]
    land = AP.Landscape(stands, lib[["unit_id", "prescription"]],
                        runs.merge(cycles, on=["run_hash", "PLT_CN"]), cfg["n_cycles"])
    best = [max(sum(o) for o in opts) for opts in land.volumes]
    for dim, keys, ix, targets in (("county", land.counties, land.county_ix, caps[AP.hs.COUNTY]),
                                   ("owner_group", land.owners, land.owner_ix, caps[AP.hs.OWNER])):
        for k, key in enumerate(keys):
            cap = sum(b for b, i in zip(best, ix) if i == k)
            need = targets[key] * cfg["n_cycles"]
            got = plan_dev[(plan_dev.variant == v.name) & (plan_dev.key == key)]["volume_cuft"].sum()
            rows.append({"variant": v.name, "dimension": dim, "key": key,
                         "horizon_target_cuft": need, "horizon_max_cuft": cap,
                         "max_pct_of_target": 100 * cap / need,
                         "plan_pct_of_target": 100 * got / need})
out = pd.DataFrame(rows)
out.to_csv(EXP / "outputs/horizon_capacity.csv", index=False)
print(out.pivot_table(index=["dimension", "key"], columns="variant",
                      values="max_pct_of_target").round(0).to_string())
print(out.pivot_table(index=["dimension", "key"], columns="variant",
                      values="plan_pct_of_target").round(0).to_string())
