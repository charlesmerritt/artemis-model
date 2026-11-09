"""Figures and summary tables for the young-plot initial state (ADR 0003).

Reads only a ``pipeline.s1_initial_state.add_back_ids`` output folder -- ``<run>/young_plots/``
-- plus the run's own provenance raster, so every number here comes from the stage's own
output and nothing is recomputed from the sources.

    uv run python weekly-artifact/2026-11-09/make_figure.py \
        --run-dir data/r2_cache/improved-rasters/aoi_5county \
        --out-dir weekly-artifact/2026-11-09

Writes ``figures/*.png`` and ``tables/*.csv``. Control numbers are read as exact strings
(AGENTS.md's FIA gotcha): the VAT extension's ``PLT_CN``/``TM_ID`` columns never become floats.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import ListedColormap, Normalize  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from pipeline.ids import read_id_csv  # noqa: E402
from pipeline.s1_initial_state.add_back_ids import EXTENSION_ID_COLUMNS  # noqa: E402
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance as TP  # noqa: E402
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL  # noqa: E402

# Age source is categorical identity, in a fixed order; the three evidence colours pass the
# CVD/lightness/chroma checks against a near-white surface, and the fallback is a neutral
# because it carries no Landsat evidence at all.
AGE_SOURCE_ORDER = ["obata_hansen", "hansen", "obata", "stratum_median"]
AGE_SOURCE_COLOR = {"obata_hansen": "#1baf7a", "hansen": "#2a78d6",
                    "obata": "#eb6834", "stratum_median": "#8a8a84"}
AGE_SOURCE_LABEL = {"obata_hansen": "Obata + Hansen agree", "hansen": "Hansen only",
                    "obata": "Obata only", "stratum_median": "Undated: stratum median"}
MATCH_COLOR = {"type": "#2a78d6", "group": "#eda100", "unmatched": "#e3342f"}
# Stand age is magnitude, so one hue light->dark. Sampled from a single green ramp.
AGE_RAMP = "YlGn"
SURFACE = "#fcfcfb"
PUBLISHED = "#c9cdc7"      # TreeMap's own forest: context, deliberately recessive
UNMAPPED = "#ecebe6"
WATER = "#dfe8ef"
OUTSIDE = "#ffffff"
INK, INK_MUTED = "#23241f", "#6b6c66"


def load(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray, dict]:
    """The VAT extension, tree extension, the new-ID raster, the run provenance, validation."""
    out = run_dir / "young_plots"
    vat = read_id_csv(out / "treemap2022_improved_vat_extension.csv",
                      id_columns=list(EXTENSION_ID_COLUMNS) + ["Value"])
    for column in ("STAND_AGE_2022", "AGE_GAP", "Count", "FORTYPCD"):
        vat[column] = pd.to_numeric(vat[column])
    vat["CUT_YEAR"] = pd.to_numeric(vat["CUT_YEAR"])
    trees = read_id_csv(out / "treemap2022_improved_tree_extension.csv",
                        id_columns=["TM_ID", "PLT_CN"])
    for column in ("TPA_UNADJ", "DIA", "HT", "SPCD"):
        trees[column] = pd.to_numeric(trees[column])
    with rasterio.open(out / "treemap2022_improved.tif") as src:
        ids, nodata = src.read(1), src.nodata
    with rasterio.open(run_dir / "treemap2022_provenance.tif") as src:
        prov = src.read(1)
    validation = json.loads((out / "validation.json").read_text())
    return vat, trees, np.where(ids == nodata, 0, ids), prov, validation


def age_raster(ids: np.ndarray, vat: pd.DataFrame) -> np.ndarray:
    """Each new-ID pixel's STAND_AGE_2022; -1 where there is no new ID."""
    keys = vat["Value"].astype("int64").to_numpy()
    order = np.argsort(keys)
    keys, vals = keys[order], vat["STAND_AGE_2022"].to_numpy()[order]
    flat = ids.ravel().astype("int64")
    out = np.full(flat.shape, -1, dtype="int16")
    idx = np.clip(np.searchsorted(keys, flat), 0, keys.size - 1)
    hit = keys[idx] == flat
    out[hit] = vals[idx[hit]]
    return out.reshape(ids.shape)


def fig_age_map(ages: np.ndarray, prov: np.ndarray, vat: pd.DataFrame, path: Path) -> None:
    """The AOI, TreeMap's own forest recessive, every added-back pixel under its stand age."""
    step = 2                                    # 30 m -> 60 m for a figure that fits in git
    ages, prov = ages[::step, ::step], prov[::step, ::step]
    base = np.full(prov.shape, 0, dtype="uint8")
    base[prov == TP.UNMAPPED_LAND] = 1
    base[prov == TP.WATER] = 2
    base[prov == TP.PUBLISHED] = 3
    fig, (ax, cax) = plt.subplots(1, 2, figsize=(10.5, 7.4),
                                  gridspec_kw={"width_ratios": [40, 1]})
    fig.patch.set_facecolor(SURFACE)
    ax.imshow(base, cmap=ListedColormap([OUTSIDE, UNMAPPED, WATER, PUBLISHED]),
              vmin=0, vmax=3, interpolation="nearest")
    shown = np.ma.masked_less(ages, 0)
    norm = Normalize(vmin=0, vmax=20)
    # One hue, light -> dark, but starting past the palest steps: age 0 has to stay visible
    # against the near-white "unmapped land" it sits in.
    ramp = ListedColormap(plt.get_cmap(AGE_RAMP)(np.linspace(0.22, 1.0, 256)))
    im = ax.imshow(shown, cmap=ramp, norm=norm, interpolation="nearest")
    ax.set_axis_off()
    acres = vat["Count"].sum() * ACRES_PER_PIXEL
    ax.set_title("Added-back acres carry a dated stand age and a real FIA young plot\n"
                 f"Five-county north Florida AOI · {len(vat):,} new TreeMap IDs over "
                 f"{acres:,.0f} added-back acres",
                 fontsize=12.5, color=INK, loc="left", pad=12)
    bar = fig.colorbar(im, cax=cax)
    bar.set_label("Stand age in 2022 (years since the Landsat cut)", color=INK_MUTED, fontsize=9)
    bar.ax.tick_params(colors=INK_MUTED, labelsize=8)
    bar.outline.set_visible(False)
    ax.legend(handles=[Patch(color=PUBLISHED, label="TreeMap 2022 forest, as published"),
                       Patch(color=UNMAPPED, label="Land TreeMap leaves unmapped"),
                       Patch(color=WATER, label="Water")],
              loc="lower left", frameon=False, fontsize=8.5, labelcolor=INK_MUTED)
    fig.tight_layout()
    fig.savefig(path, dpi=165, facecolor=SURFACE)
    plt.close(fig)


def fig_acres_by_age(by_age: pd.DataFrame, path: Path) -> None:
    """Acres in each stand age, stacked by what dated it."""
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ages = by_age.index.to_numpy()
    bottom = np.zeros(len(by_age))
    for source in AGE_SOURCE_ORDER:
        if source not in by_age.columns:
            continue
        height = by_age[source].to_numpy()
        ax.bar(ages, height, bottom=bottom, width=0.74, color=AGE_SOURCE_COLOR[source],
               label=AGE_SOURCE_LABEL[source], zorder=3, linewidth=0.8, edgecolor=SURFACE)
        bottom = bottom + height
    ax.set_ylim(0, bottom.max() * 1.30)       # headroom for the labels and the legend
    for age, total in zip(ages, bottom):
        if total > 1800:                      # direct-label only the bars worth reading
            ax.text(age, total + bottom.max() * 0.018, f"{total / 1e3:,.1f}k", ha="center",
                    va="bottom", fontsize=8.5, color=INK_MUTED, zorder=4)
    ax.set_xticks(ages)
    ax.set_xlabel("Stand age in 2022 (years)", color=INK_MUTED, fontsize=10)
    ax.set_ylabel("Added-back acres", color=INK_MUTED, fontsize=10)
    ax.set_title("Where the added-back acres sit in the age classes, and what dated them",
                 fontsize=12.5, color=INK, loc="left", pad=26)
    ax.grid(axis="y", color="#e5e4df", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#d6d5d0")
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK_MUTED, ncols=4,
              loc="upper center", bbox_to_anchor=(0.5, 1.045), columnspacing=1.4)
    fig.tight_layout()
    fig.savefig(path, dpi=165, facecolor=SURFACE)
    plt.close(fig)


def fig_match_quality(vat: pd.DataFrame, path: Path) -> None:
    """How the young plot was found: which pool, and how far its FIA age sat from the pixel's."""
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 3.5))
    fig.patch.set_facecolor(SURFACE)
    acres = vat.assign(acres=vat["Count"] * ACRES_PER_PIXEL)

    pools = acres.groupby("MATCH_LEVEL")["acres"].sum()
    labels = {"type": "Neighbour plot's own\nforest type", "group": "Its forest type group",
              "unmatched": "No young plot"}
    order = [p for p in ("type", "group", "unmatched") if p in pools.index]
    left.barh(range(len(order)), [pools[p] for p in order], height=0.56,
              color=[MATCH_COLOR[p] for p in order], zorder=3)
    for i, p in enumerate(order):
        left.text(pools[p] + pools.max() * 0.02, i,
                  f"{pools[p]:,.0f} ac  ({pools[p] / pools.sum():.1%})",
                  va="center", fontsize=9, color=INK_MUTED, zorder=4)
    left.set_yticks(range(len(order)), [labels[p] for p in order])
    left.set_xlim(0, pools.max() * 1.42)
    left.set_title("Which pool the young plot came from", fontsize=11, color=INK, loc="left")

    gaps = acres.groupby("AGE_GAP")["acres"].sum().sort_index()
    right.bar(gaps.index, gaps.to_numpy(), width=0.5, color="#2a78d6", zorder=3)
    for gap, value in gaps.items():
        right.text(gap, value + gaps.max() * 0.02, f"{value:,.0f} ac", ha="center",
                   va="bottom", fontsize=9, color=INK_MUTED, zorder=4)
    right.set_xticks(gaps.index)
    right.set_ylim(0, gaps.max() * 1.18)
    right.set_xlabel("|young plot FIA STDAGE − pixel stand age|, years",
                     color=INK_MUTED, fontsize=9.5)
    right.set_title("How close its FIA age sat", fontsize=11, color=INK, loc="left")

    for ax in (left, right):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#d6d5d0")
        ax.spines["bottom"].set_color("#d6d5d0")
        ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
        ax.grid(axis="x" if ax is left else "y", color="#e5e4df", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, dpi=165, facecolor=SURFACE)
    plt.close(fig)


def tables(vat: pd.DataFrame, trees: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every number the figures draw, as a table, before any chart is drawn."""
    acres = vat.assign(acres=vat["Count"] * ACRES_PER_PIXEL)
    by_age = (acres.groupby(["STAND_AGE_2022", "AGE_SOURCE"])["acres"].sum()
              .unstack("AGE_SOURCE").fillna(0.0))
    tpa = trees.groupby("TM_ID")["TPA_UNADJ"].sum()
    per_id = acres.set_index("TM_ID")
    live = (tpa * per_id["Count"] * ACRES_PER_PIXEL).dropna()
    species = (trees.merge(per_id[["acres"]], left_on="TM_ID", right_index=True)
               .assign(trees=lambda d: d["TPA_UNADJ"] * d["acres"])
               .groupby("SPCD")["trees"].sum().sort_values(ascending=False))
    return {
        "young_plots_by_stand_age": (
            acres.groupby("STAND_AGE_2022")
            .agg(new_ids=("TM_ID", "nunique"), young_plots=("YOUNG_TM_ID", "nunique"),
                 pixels=("Count", "sum"), acres=("acres", "sum"))
            .assign(acres_share=lambda d: d["acres"] / d["acres"].sum())
            .round({"acres": 1, "acres_share": 5}).reset_index()),
        "young_plots_by_age_source": (
            acres.groupby("AGE_SOURCE")
            .agg(new_ids=("TM_ID", "nunique"), pixels=("Count", "sum"), acres=("acres", "sum"),
                 min_cut_year=("CUT_YEAR", "min"), max_cut_year=("CUT_YEAR", "max"))
            .assign(acres_share=lambda d: d["acres"] / d["acres"].sum())
            .round({"acres": 1, "acres_share": 5}).reset_index()),
        "young_plots_by_match_level": (
            acres.groupby(["MATCH_LEVEL", "AGE_GAP"])
            .agg(new_ids=("TM_ID", "nunique"), young_plots=("YOUNG_TM_ID", "nunique"),
                 acres=("acres", "sum"))
            .assign(acres_share=lambda d: d["acres"] / d["acres"].sum())
            .round({"acres": 1, "acres_share": 5}).reset_index()),
        "young_plots_by_forest_type": (
            acres.groupby(["FORTYPCD", "ForTypName"])
            .agg(new_ids=("TM_ID", "nunique"), young_plots=("YOUNG_TM_ID", "nunique"),
                 neighbour_plots=("NEIGHBOUR_TM_ID", "nunique"), acres=("acres", "sum"))
            .assign(acres_share=lambda d: d["acres"] / d["acres"].sum())
            .sort_values("acres", ascending=False)
            .round({"acres": 1, "acres_share": 5}).reset_index()),
        "young_plots_acres_by_stand_age_and_source": by_age.round(1).reset_index(),
        "young_plots_species_mix": (
            species.rename("live_trees").reset_index()
            .assign(share=lambda d: d["live_trees"] / d["live_trees"].sum())
            .round({"live_trees": 0, "share": 5})),
        "young_plots_live_totals": pd.DataFrame([{
            "new_ids": int(len(vat)),
            "young_plots": int(vat["YOUNG_TM_ID"].nunique()),
            "neighbour_plots": int(vat["NEIGHBOUR_TM_ID"].nunique()),
            "pixels": int(vat["Count"].sum()),
            "acres": round(float(acres["acres"].sum()), 1),
            "tree_rows": int(len(trees)),
            "live_trees": round(float(live.sum()), 0),
            "live_tpa_per_acre": round(float(live.sum() / acres["acres"].sum()), 1),
        }]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="the run folder holding young_plots/")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    vat, trees, ids, prov, validation = load(args.run_dir)
    figures, table_dir = args.out_dir / "figures", args.out_dir / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)

    written = tables(vat, trees)
    for name, frame in written.items():
        frame.to_csv(table_dir / f"{name}.csv", index=False)
        print(f"wrote tables/{name}.csv ({len(frame)} rows)")

    by_age = written["young_plots_acres_by_stand_age_and_source"].set_index("STAND_AGE_2022")
    fig_age_map(age_raster(ids, vat), prov, vat, figures / "fig1_young_plot_stand_age_map.png")
    fig_acres_by_age(by_age, figures / "fig2_acres_by_stand_age.png")
    fig_match_quality(vat, figures / "fig3_young_plot_match_quality.png")
    print(f"wrote 3 figures to {figures}")
    print(json.dumps(validation["checks"], indent=2))


if __name__ == "__main__":
    main()
