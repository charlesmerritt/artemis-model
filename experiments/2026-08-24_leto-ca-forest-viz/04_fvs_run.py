"""Build per-stand FVS inputs, assign owner-class regimes, run FVSsn (stages 4-5).

LETO stage 4/5 methodology in the repo's geopandas/pandas stack:

  - Each management unit's tree list is the area-weighted union of its TreeMap
    donor plots' FVS-ready tree records (`FVS_TREEINIT_PLOT`, keyed by
    STAND_CN = PLT_CN), with TREE_COUNT scaled by donor pixel share; donors
    below a 5% share are dropped and weights renormalised
    (`pipeline.s4_fvs.build_fvs_inputs`, LETO MIN_PLT_WEIGHT).
  - Each unit's StandInit row is its dominant donor's row with the identifiers
    replaced and INV_YEAR set to 2022 — the TreeMap 2022 imputation anchor
    (notes/treemap-fvs-workflow.md).
  - The management regime is the deterministic owner-class default from
    config/management_regimes.yaml, resolved by
    `pipeline.s3_management.regime_assignment.assign_prescription` (riparian
    absolute override, pine/hardwood/other branch, age-based entry years
    snapped to FVS cycles). These are the ESTIMATED harvest activities per
    owner — the simulated-annealing trajectory scheduler that will select
    among eligible prescriptions is not built yet (README "Known
    constraints"), so every unit runs its default trajectory.
  - Keyfiles are rendered by `pipeline.s4_fvs.regime_templates` (verified
    ThinDBH / Estab keyword layouts; natural regeneration apportioned across
    the stand's own species by SDI share, Diaz et al. 2015 rule), and run
    through the FVS Southern (SN) variant compiled from the USDA
    ForestVegetationSimulator sources in this container.

Outputs (work/):
    fvs_inputs.db      FVS_STANDINIT_PLOT / FVS_TREEINIT_PLOT for every runnable MU
    mu_regimes.csv     per-MU owner class, prescription, resolved entry years
    fvs_runs/          per-shard keyfiles and FVSOut databases
    fvs_summary2.csv   FVS_Summary2 rows for every run (the BA trajectories)
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import FIA_DB, INV_YEAR, NUM_CYCLES, CYCLE_YEARS, WORK  # noqa: E402
from pipeline.s3_management.regime_assignment import assign_prescription  # noqa: E402
from pipeline.s4_fvs.build_fvs_inputs import build_tree_init  # noqa: E402
from pipeline.s4_fvs.regime_templates import render_keyfile  # noqa: E402

FVS_BIN = os.environ.get(
    "FVSSN_BIN",
    "/tmp/claude-0/-home-user-artemis-model/a51bbb3c-0856-58b5-9966-8b2afceecf89/"
    "scratchpad/ForestVegetationSimulator/bin/FVSsn_CmakeDir/FVSsn",
)
N_WORKERS = max(1, (os.cpu_count() or 2) - 0)

# FIA SPCD -> FVS SN alpha species codes, from the compiled variant's own
# tables (sn/blkdat.f DATA JSP / FIAJSP, positionally aligned).
_SN_JSP = ("FR JU PI PU SP SA SR LL TM PP PD WP LP VP BY PC HM FM BE RM SV SM BU BB SB AH "
           "HI CA HB RD DW PS AB AS WA BA GA HL LB HA HY BN WN SU YP MG CT MS MV ML AP MB "
           "WT BG TS HH SD RA SY CW BT BC WO SO SK CB TO LK OV BJ SN CK WK CO RO QS PO BO "
           "LO BK WI SS BD EL WE AE RL OS OH OT").split()
_SN_FIAJSP = ("010 057 090 107 110 111 115 121 123 126 128 129 131 132 221 222 260 311 313 "
              "316 317 318 330 370 372 391 400 450 460 471 491 521 531 540 541 543 544 552 "
              "555 580 591 601 602 611 621 650 651 652 653 654 660 680 691 693 694 701 711 "
              "721 731 740 743 762 802 806 812 813 819 820 822 824 825 826 827 832 833 834 "
              "835 837 838 901 920 931 950 970 971 972 975 299 998 999").split()
FIA_TO_SN = dict(zip(_SN_FIAJSP, _SN_JSP))


def load_units() -> pd.DataFrame:
    mu = pd.read_csv(WORK / "mu_summary.csv")
    mu["MU_ID"] = mu["MU_ID"].astype(str)
    return mu


def assign_regimes(mu: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in mu.itertuples(index=False):
        unit = {
            "OWN_CODE": int(r.OWN_CODE),
            "SMZ_Pct": 100.0 if int(r.MGMT_CLASS) == 1 else 0.0,
            "FORTYPCD": int(r.FORTYPCD_DOM),
            "stand_age": float(r.STDAGE_MEAN),
        }
        p = assign_prescription(unit, inv_year=INV_YEAR)
        rows.append({
            "MU_ID": r.MU_ID, "owner_class": p.owner_class,
            "prescription": p.prescription_id, "template": p.template,
            "forest_branch": p.forest_type_branch,
            "params": json.dumps(p.params), "notes": ";".join(p.notes),
        })
    return pd.DataFrame(rows)


def build_inputs(mu: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stand-level StandInit/TreeInit via the repo's weighted-union builder."""
    weights = pd.read_csv(WORK / "mu_donor_weights.csv",
                          dtype={"MU_ID": str, "PLT_CN": str})
    con = sqlite3.connect(FIA_DB)
    tree_init = pd.read_sql(
        "SELECT CAST(STAND_CN AS TEXT) AS STAND_CN, PLOT_ID, TREE_ID, TREE_COUNT,"
        " HISTORY, SPECIES, DIAMETER, HT, CRRATIO, AGE FROM FVS_TREEINIT_PLOT", con)
    stand_init = pd.read_sql("SELECT * FROM FVS_STANDINIT_PLOT", con)
    con.close()
    stand_init["STAND_CN"] = stand_init["STAND_CN"].astype(str)

    tree_final, runnable = build_tree_init(weights, tree_init)
    tree_final = tree_final.drop(columns=["VALUE", "CELLS", "WEIGHT", "TREE_SOURCE",
                                          "DONOR_STAND_ID", "NEAR_DIST", "FALLBACK_SLOT"],
                                 errors="ignore")
    # unique tree ids within each stand (LETO stage 4 QA requirement)
    tree_final["TREE_ID"] = tree_final.groupby("STAND_ID").cumcount() + 1
    tree_final["STAND_CN"] = tree_final["STAND_ID"]

    # StandInit: dominant donor's row, re-keyed, anchored at 2022
    dom = (weights.sort_values(["MU_ID", "WEIGHT"], ascending=[True, False])
                  .drop_duplicates("MU_ID"))
    dom = dom[dom["MU_ID"].isin(runnable)]
    stands = dom.merge(stand_init, left_on="PLT_CN", right_on="STAND_CN",
                       how="left", suffixes=("", "_donor"))
    stands["DONOR_PLT_CN"] = stands["PLT_CN"]
    stands["STAND_ID"] = "MU_" + stands["MU_ID"]
    stands["STAND_CN"] = stands["STAND_ID"]
    stands["INV_YEAR"] = float(INV_YEAR)
    stands["GROUPS"] = "leto_ca_viz"
    stands = stands.drop(columns=["VALUE", "CELLS", "WEIGHT", "PLT_CN"], errors="ignore")
    return stands, tree_final


def stand_sdi_tables(tree_final: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-stand species SDI shares (SN alpha codes) for natural regeneration."""
    t = tree_final.copy()
    t["SN_SP"] = t["SPECIES"].astype(str).str.split(".").str[0].str.zfill(3).map(FIA_TO_SN)
    t = t.dropna(subset=["SN_SP"])
    live = t[(t["HISTORY"].fillna(1) <= 5) & (t["TREE_COUNT"] > 0)]
    live = live.assign(SDI=live["TREE_COUNT"] * (live["DIAMETER"].fillna(1.0)
                                                 .clip(lower=0.5) / 10.0) ** 1.6)
    out: dict[str, dict[str, float]] = {}
    for stand_id, grp in live.groupby("STAND_ID"):
        by_sp = grp.groupby("SN_SP")["SDI"].sum()
        out[str(stand_id)] = {sp: float(v) for sp, v in by_sp.items() if v > 0}
    return out


def write_input_db(stands: pd.DataFrame, trees: pd.DataFrame, path: Path) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    stands.to_sql("FVS_STANDINIT_PLOT", con, index=False)
    trees.to_sql("FVS_TREEINIT_PLOT", con, index=False)
    con.execute("CREATE INDEX idx_stand ON FVS_STANDINIT_PLOT(STAND_CN)")
    con.execute("CREATE INDEX idx_tree ON FVS_TREEINIT_PLOT(STAND_CN)")
    con.commit()
    con.close()


def run_shard(args) -> tuple[int, int, int]:
    shard_id, run_dir, keyfiles = args
    shard_dir = Path(run_dir) / f"shard_{shard_id:02d}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    out_db = shard_dir / "FVSOut.db"
    if out_db.exists():
        out_db.unlink()
    ok = failed = 0
    for stand_id, key in keyfiles:
        keyfile = shard_dir / f"{stand_id}.key"
        keyfile.write_text(key)
        proc = subprocess.run(
            [FVS_BIN, f"--keywordfile={keyfile.name}"],
            cwd=shard_dir, capture_output=True, text=True, timeout=300)
        # FVS exits 10 ("STOP 10") on a normal completed run
        if proc.returncode in (0, 10):
            ok += 1
        else:
            failed += 1
            (shard_dir / f"{stand_id}.err").write_text(
                f"rc={proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return shard_id, ok, failed


def run_batch(run_dir: Path, keyfiles: dict[str, str]) -> pd.DataFrame:
    """Run one batch of keyfiles across the worker pool; return FVS_Summary2."""
    items = sorted(keyfiles.items())
    shards = [(i, str(run_dir), items[i::N_WORKERS]) for i in range(N_WORKERS)]
    with mp.Pool(N_WORKERS) as pool:
        for shard_id, ok, failed in pool.imap_unordered(run_shard, shards):
            print(f"  shard {shard_id}: {ok} ok, {failed} failed")
    frames = []
    for out_db in sorted(Path(run_dir).glob("shard_*/FVSOut.db")):
        con = sqlite3.connect(out_db)
        frames.append(pd.read_sql(
            "SELECT s.StandID, s.Year, s.Age, s.Tpa, s.BA, s.QMD, s.TCuFt, s.MCuFt,"
            " s.RTpa, s.RTCuFt, s.TPrdTpa, s.TPrdTCuFt, s.RmvCode"
            " FROM FVS_Summary2 s", con))
        con.close()
    summary = pd.concat(frames, ignore_index=True)
    summary["MU_ID"] = summary["StandID"].str.removeprefix("MU_")
    return summary


def post_treatment_ba(summary: pd.DataFrame) -> pd.DataFrame:
    """Post-removal BA per (StandID, Year): the max-RmvCode row of each year."""
    s = summary.sort_values("RmvCode").drop_duplicates(["StandID", "Year"], keep="last")
    return s


def default_keyfiles(regimes: pd.DataFrame, sdi_tables: dict,
                     input_db: Path, stand_ids: list[str]) -> dict[str, str]:
    reg_by_id = {f"MU_{r.MU_ID}": {"template": r.template, "params": r.params,
                                   "owner_class": r.owner_class}
                 for r in regimes.itertuples(index=False)}
    out = {}
    for stand_id in stand_ids:
        reg = reg_by_id[stand_id]
        params = json.loads(reg["params"])
        sdi = sdi_tables.get(stand_id)
        if sdi:
            params["stand_sdi"] = sdi
        out[stand_id] = render_keyfile(
            stand_id=stand_id, stand_cn=stand_id,
            regime=reg["template"], params=params,
            mgmt_id=(reg["owner_class"][:4].upper() or "A001").ljust(4, "_"),
            inv_year=INV_YEAR, cycle_years=CYCLE_YEARS, num_cycle=NUM_CYCLES,
            out_db="FVSOut.db", in_db=str(input_db),
            stand_table="FVS_STANDINIT_PLOT", tree_table="FVS_TREEINIT_PLOT",
        )
    return out


def schedule_keyfile(stand_id: str, sched, label: str, input_db: Path) -> str:
    """Render a keyfile straight from a policy Schedule's ThinDBH/Regeneration
    lists (render_keyfile's thins=/regen= override)."""
    return render_keyfile(
        stand_id=stand_id, stand_cn=stand_id, regime=label,
        thins=sorted(sched.thins, key=lambda t: t.year),
        regen=sorted(sched.regen, key=lambda r: r.year),
        mgmt_id=label[:4].upper().ljust(4, "_"),
        inv_year=INV_YEAR, cycle_years=CYCLE_YEARS, num_cycle=NUM_CYCLES,
        out_db="FVSOut.db", in_db=str(input_db),
        stand_table="FVS_STANDINIT_PLOT", tree_table="FVS_TREEINIT_PLOT",
    )


def schedules_log(schedules: dict[str, "Schedule"]) -> pd.DataFrame:
    rows = []
    for stand_id, sched in schedules.items():
        for t in sorted(sched.thins, key=lambda t: t.year):
            rows.append({"MU_ID": stand_id.removeprefix("MU_"), "year": t.year,
                         "kind": "clearcut" if t.proportion >= 1.0 else "thin",
                         "proportion": t.proportion})
    return pd.DataFrame(rows, columns=["MU_ID", "year", "kind", "proportion"])


def build_policy_schedules(policy: str, mu: pd.DataFrame,
                           sdi_tables: dict) -> dict[str, "Schedule"]:
    """Pass-1 schedules for the random/heuristic policies (riparian: no entry)."""
    from policies import (
        Schedule,
        heuristic_group,
        industrial_initial_schedule,
        random_schedule,
    )

    schedules = {}
    for r in mu.itertuples(index=False):
        stand_id = f"MU_{r.MU_ID}"
        sdi = sdi_tables.get(stand_id)
        if policy == "random":
            if int(r.MGMT_CLASS) == 1:
                schedules[stand_id] = Schedule()
            else:
                schedules[stand_id] = random_schedule(
                    str(r.MU_ID), int(r.FORTYPCD_DOM), sdi, policy_tag="random")
        else:  # heuristic
            group = heuristic_group(r.OWNER_CLASS, int(r.MGMT_CLASS))
            if group == "noop":
                schedules[stand_id] = Schedule()
            elif group == "industrial":
                schedules[stand_id] = industrial_initial_schedule(float(r.STDAGE_MEAN))
            else:
                schedules[stand_id] = random_schedule(
                    str(r.MU_ID), int(r.FORTYPCD_DOM), sdi, policy_tag="heuristic")
    return schedules


def run_heuristic_industrial_loop(schedules: dict, summary_by_stand: dict,
                                  mu: pd.DataFrame, sdi_tables: dict,
                                  input_db: Path, run_root: Path,
                                  max_passes: int = 5) -> None:
    """Iterate the industrial BA>100 clearcut trigger against real FVS output.

    Each pass: read every industrial stand's latest post-treatment BA
    trajectory, extend schedules whose trajectory crosses the trigger, rerun
    only the extended stands, fold their new summaries in. Stops when a pass
    triggers nothing (every industrial trajectory stays under 100 sq ft/ac
    between entries) or at `max_passes` — regrowth to the trigger takes
    15-20 years here, so two rotations is the practical ceiling.
    """
    from policies import heuristic_group, industrial_next_clearcut

    industrial = [f"MU_{r.MU_ID}" for r in mu.itertuples(index=False)
                  if heuristic_group(r.OWNER_CLASS, int(r.MGMT_CLASS)) == "industrial"]
    fortypcd = {f"MU_{r.MU_ID}": int(r.FORTYPCD_DOM) for r in mu.itertuples(index=False)}

    for pass_no in range(2, max_passes + 1):
        changed = {}
        for stand_id in industrial:
            rows = summary_by_stand.get(stand_id)
            if rows is None:
                continue
            post = post_treatment_ba(rows)
            ba_by_year = dict(zip(post["Year"].astype(int), post["BA"]))
            if industrial_next_clearcut(schedules[stand_id], ba_by_year,
                                        fortypcd[stand_id],
                                        sdi_tables.get(stand_id)):
                changed[stand_id] = schedule_keyfile(
                    stand_id, schedules[stand_id], "heuristic", input_db)
        if not changed:
            print(f"industrial BA-trigger loop converged before pass {pass_no}")
            return
        print(f"industrial BA-trigger pass {pass_no}: {len(changed):,} stands "
              f"crossed 100 sq ft/ac — rerunning")
        summary = run_batch(run_root / f"pass{pass_no}", changed)
        for stand_id, rows in summary.groupby("StandID"):
            summary_by_stand[stand_id] = rows
    print(f"industrial BA-trigger loop stopped at max_passes={max_passes}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=("default", "random", "heuristic"),
                        default="default")
    args = parser.parse_args()
    policy = args.policy
    suffix = "" if policy == "default" else f"_{policy}"

    mu = load_units()
    stands, trees = build_inputs(mu)
    print(f"runnable stands: {len(stands):,}; tree rows: {len(trees):,}; "
          f"not runnable: {mu['MU_ID'].nunique() - len(stands):,}")
    input_db = (WORK / "fvs_inputs.db").resolve()
    write_input_db(stands, trees, input_db)
    sdi_tables = stand_sdi_tables(trees)
    stand_ids = sorted(stands["STAND_ID"])
    run_root = WORK / f"fvs_runs{suffix}"

    if policy == "default":
        regimes = assign_regimes(mu)
        regimes.to_csv(WORK / "mu_regimes.csv", index=False)
        print(regimes.groupby(["owner_class", "prescription"]).size().to_string())
        keyfiles = default_keyfiles(regimes, sdi_tables, input_db, stand_ids)
        print(f"running {len(keyfiles):,} FVSsn projections on {N_WORKERS} workers…")
        summary = run_batch(run_root, keyfiles)
    else:
        schedules = build_policy_schedules(policy, mu, sdi_tables)
        keyfiles = {sid: schedule_keyfile(sid, schedules[sid], policy, input_db)
                    for sid in stand_ids}
        print(f"[{policy}] running {len(keyfiles):,} FVSsn projections "
              f"on {N_WORKERS} workers…")
        first = run_batch(run_root / "pass1", keyfiles)
        summary_by_stand = {sid: rows for sid, rows in first.groupby("StandID")}
        if policy == "heuristic":
            run_heuristic_industrial_loop(schedules, summary_by_stand, mu,
                                          sdi_tables, input_db, run_root)
        summary = pd.concat(summary_by_stand.values(), ignore_index=True)
        schedules_log(schedules).to_csv(WORK / f"mu_schedules{suffix}.csv", index=False)

    summary.to_csv(WORK / f"fvs_summary2{suffix}.csv", index=False)
    n_ran = summary["StandID"].nunique()
    print(f"collected FVS_Summary2 for {n_ran:,} stands "
          f"({len(summary):,} rows) -> fvs_summary2{suffix}.csv")


if __name__ == "__main__":
    main()
