"""Step 0 — the scaled_young establishment tree lists for every raster set.

Provenance-4 ("added back young") pixels carry a donor TM_ID for forest type and species mix
only; their trees are the `scaled_young` rows of `establishment_tree_lists.csv` (ADR 0002).
gate-v3 and gate-v4 ship that file; `gated` and `gate-v2` predate it. This regenerates it for
every set from the set's own `establishment_patches.csv`, with the generator that wrote v3's
and v4's (`county_improvement.stitch_establishment`'s inputs and `scaled_establishment_lists`),
and checks the regenerated v3 and v4 lists equal the shipped ones before trusting gated/v2.

The generator lives on branch `add-back-stand-age` (worktree `worktrees/improved-rasters`,
commit b26dd1c), not on this branch, so run this step with that worktree first on the path:

    PYTHONPATH=../improved-rasters uv run python experiments/2026-10-12_raster-set-schedules/00_establishment_lists.py

Writes `work/<raster set>/establishment_tree_lists.csv`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
sys.path.insert(1, str(EXP))

import raster_sets as RS  # noqa: E402
from pipeline.s1_initial_state import county_improvement as CI  # noqa: E402
from pipeline.s1_initial_state.impute_establishment import (  # noqa: E402
    load_establishment_policy,
    scaled_establishment_lists,
)

if not hasattr(CI, "stitch_establishment"):
    raise SystemExit("run with the add-back-stand-age worktree first on PYTHONPATH (see docstring)")


def regenerate(raster_set: RS.RasterSet, tree_table: pd.DataFrame, vat: Path) -> pd.DataFrame:
    patches = pd.read_csv(raster_set.aoi / "establishment_patches.csv",
                          dtype={"donor_tm_id": "string", "establishment_mode": "string"})
    resolved = patches.dropna(subset=["donor_tm_id"]).rename(columns={"donor_tm_id": "TM_ID"})
    donor_modes = resolved[["TM_ID", "establishment_mode"]].drop_duplicates()
    donor_rows = tree_table[tree_table["TM_ID"].isin(set(donor_modes["TM_ID"]))]
    return scaled_establishment_lists(donor_modes, donor_rows,
                                      CI.donor_forest_types(vat, donor_modes["TM_ID"]),
                                      load_establishment_policy())


def same_lists(ours: pd.DataFrame, shipped: pd.DataFrame) -> bool:
    keys = ["TM_ID", "establishment_mode", "SPCD", "TPA_UNADJ", "DIA"]
    a = ours.astype({"TM_ID": str, "PLT_CN": str}).sort_values(keys).reset_index(drop=True)
    b = shipped.astype({"TM_ID": str, "PLT_CN": str}).sort_values(keys).reset_index(drop=True)
    cols = ["TM_ID", "PLT_CN", "establishment_mode", "SPCD", "STATUSCD"]
    if len(a) != len(b) or not a[cols].equals(b[cols]):
        return False
    num = ["TPA_UNADJ", "DIA", "HT"]
    return bool(((a[num] - b[num]).abs().max() < 1e-6).all())


def main() -> None:
    inputs = CI.Inputs()
    tree_table = CI.read_id_csv(inputs.tree_table, usecols=CI.TREE_COLUMNS)
    for rs in RS.IMPROVED:
        lists = regenerate(rs, tree_table, inputs.vat)
        shipped = rs.aoi / "establishment_tree_lists.csv"
        if shipped.exists():
            ok = same_lists(lists, pd.read_csv(shipped, dtype={"TM_ID": str, "PLT_CN": str}))
            print(f"{rs.name}: regenerated {len(lists)} rows; equals shipped list: {ok}")
            if not ok:
                raise AssertionError(f"{rs.name}: regenerated lists differ from the shipped file")
        else:
            print(f"{rs.name}: regenerated {len(lists)} rows (no shipped list to compare)")
        out = RS.work_dir(rs) / "establishment_tree_lists.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        lists.to_csv(out, index=False)


if __name__ == "__main__":
    main()
