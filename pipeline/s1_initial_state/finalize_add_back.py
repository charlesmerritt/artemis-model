"""Turn the scored rasters into the final TreeMap 2022 add-back mask.

Last stage. Combines three local rasters into one decision per hole pixel:

- ``treemap_hole_strata.tif``    which stratum the hole belongs to (S1-S5)
- ``hole_prob_similarity.tif``   band 1 = classifier probability x100,
                                 band 2 = (cosine similarity + 1) x100,
                                 downloaded by ``embed_holes.py apply``
- the thresholds recorded in ``hole_model.json``

Decision, matching the two-stage funnel in ``classify_holes.py``:

- **S1 / S2** are added back unconditionally. Their label does not come from the
  model — LANDFIRE says forest in 2016 and forest again in 2024, so the regrowth
  already proves the land was never converted. Re-scoring them would only let
  the model overrule ground evidence.
- **S3 / S4** are added back only if they clear *both* stages: inside the
  similarity mask **and** above the classifier decision threshold.
- **S5** always stays a hole.

A minimum mapping unit is applied to the accepted mask, because a single
accepted pixel is not a stand. This runs *after* the decision so that the MMU
removes speckle rather than biasing the classifier's input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data/interim/treemap_holes"
ACRES_PER_PIXEL = 0.2224

UNCONDITIONAL_STRATA = (1, 2)  # S1, S2 - proven by LANDFIRE regrowth, not by the model
CONDITIONAL_STRATA = (3, 4)  # S3, S4 - must clear both stages
STRATUM_NAMES = {
    1: "S1_cut_pre2016_regrown",
    2: "S2_cut_2016_2022_regrown",
    3: "S3_cut_2016_2022_open",
    4: "S4_regrown_only",
    5: "S5_no_evidence",
}


def decide(strata: np.ndarray, prob: np.ndarray, similarity: np.ndarray,
           sim_threshold: float, decision_threshold: float) -> np.ndarray:
    """Boolean add-back mask before the minimum-mapping-unit filter."""
    unconditional = np.isin(strata, UNCONDITIONAL_STRATA)
    conditional = (
        np.isin(strata, CONDITIONAL_STRATA)
        & (similarity >= sim_threshold)
        & (prob >= decision_threshold)
    )
    return unconditional | conditional


def apply_mmu(mask: np.ndarray, min_acres: float) -> np.ndarray:
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    keep = np.nonzero(sizes * ACRES_PER_PIXEL >= min_acres)[0]
    return np.isin(labels, keep[keep != 0])


def summarize(strata: np.ndarray, raw: np.ndarray, final: np.ndarray) -> pd.DataFrame:
    rows = []
    for code, name in STRATUM_NAMES.items():
        in_stratum = strata == code
        rows.append({
            "stratum": name,
            "hole_acres": in_stratum.sum() * ACRES_PER_PIXEL,
            "accepted_acres": (in_stratum & raw).sum() * ACRES_PER_PIXEL,
            "after_mmu_acres": (in_stratum & final).sum() * ACRES_PER_PIXEL,
            "frac_added_back": (in_stratum & final).sum() / max(in_stratum.sum(), 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strata-tif", type=Path, default=DATA_DIR / "treemap_hole_strata.tif")
    parser.add_argument("--scored-tif", type=Path, default=DATA_DIR / "hole_prob_similarity.tif")
    parser.add_argument("--model-json", type=Path, default=DATA_DIR / "hole_model.json")
    parser.add_argument("--min-acres", type=float, default=5.0)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    model = json.loads(args.model_json.read_text())
    with rasterio.open(args.strata_tif) as src:
        strata = src.read(1)
        profile = src.profile
    with rasterio.open(args.scored_tif) as src:
        scored = src.read()
    if scored.shape[1:] != strata.shape:
        raise ValueError(f"scored raster {scored.shape[1:]} != strata {strata.shape}")

    prob = scored[0].astype(float) / 100.0
    similarity = scored[1].astype(float) / 100.0 - 1.0

    raw = decide(strata, prob, similarity, model["similarity_threshold"], model["decision_threshold"])
    raw &= strata > 0
    final = apply_mmu(raw, args.min_acres) & (strata > 0)

    table = summarize(strata, raw, final)
    print(table.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    print(f"\ntotal added back: {final.sum() * ACRES_PER_PIXEL:,.0f} ac "
          f"of {(strata > 0).sum() * ACRES_PER_PIXEL:,.0f} ac of holes "
          f"({final.sum() / (strata > 0).sum():.1%})")
    print(f"MMU removed {(raw.sum() - final.sum()) * ACRES_PER_PIXEL:,.0f} ac of speckle")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    profile.update(dtype="uint8", nodata=0, count=1, compress="lzw")
    out_tif = args.out_dir / "treemap_add_back_mask.tif"
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(final.astype(np.uint8), 1)
    table.to_csv(args.out_dir / "treemap_add_back_summary.csv", index=False)
    print(f"wrote {out_tif} and treemap_add_back_summary.csv")


if __name__ == "__main__":
    main()
