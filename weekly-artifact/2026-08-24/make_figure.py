"""
Render the riparian-overlay summary figure from the committed CSVs alone.

No R2 access and no staged data: every number here comes from the four CSVs in this
directory, which is what makes the figure reproducible by a reader who only has the repo.

    uv run python weekly-artifact/2026-08-24/make_figure.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_figure")

OUT_DIR = Path(__file__).resolve().parent

# Fixed categorical order, validated CVD-safe (see the artifact README).
BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
AMBER = "#E69F00"
GREY = "#c8ccc4"
INK = "#22252a"
MUTED = "#6b7280"
SURFACE = "#fcfcfb"
GRID = "#e6e8e3"


def style(ax, title: str, subtitle: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    head = title if not subtitle else f"{title}\n{subtitle}"
    ax.set_title(head, loc="left", fontsize=11, color=INK, pad=8)


def hbar(ax, labels, values, value_labels, color, xlabel: str) -> None:
    y = range(len(labels))
    ax.barh(list(y), list(values), color=color, height=0.62, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, color=INK, fontsize=9.5)
    ax.invert_yaxis()
    span = max(values) if len(values) else 1
    for i, (v, text) in enumerate(zip(values, value_labels)):
        ax.text(v + span * 0.02, i, text, va="center", ha="left", fontsize=9, color=INK)
    ax.set_xlim(0, span * 1.34)
    ax.set_xlabel(xlabel, fontsize=9, color=MUTED)


def main() -> None:
    county = pd.read_csv(OUT_DIR / "smz_by_county.csv")
    owner = pd.read_csv(OUT_DIR / "smz_by_owner.csv")
    cut = pd.read_csv(OUT_DIR / "corridor_units_cut.csv")
    size = pd.read_csv(OUT_DIR / "corridor_size_distribution.csv")
    corridors = pd.read_csv(OUT_DIR / "riparian_corridors.csv")
    delta = pd.read_csv(OUT_DIR / "library_riparian_delta.csv")

    fig, axes = plt.subplots(2, 2, figsize=(14.5, 9.2), facecolor=SURFACE)
    fig.subplots_adjust(left=0.135, right=0.975, top=0.855, bottom=0.075, hspace=0.42, wspace=0.42)

    # ---- 1. SMZ share by county -------------------------------------------------------
    ax = axes[0, 0]
    c = county.sort_values("smz_pct", ascending=False)
    hbar(ax, c["county"].tolist(), c["smz_pct"].tolist(),
         [f"{p:.2f}%  ({a:,.0f} ac)" for p, a in zip(c["smz_pct"], c["smz_acres"])],
         BLUE, "share of the county's attributed forest inside an SMZ (%)")
    style(ax, "1 · Riparian share by county",
          "Union's share is 3.2× Suwannee's, on a quarter of the acreage")

    # ---- 2. SMZ share by ownership class ----------------------------------------------
    ax = axes[0, 1]
    o = owner.sort_values("smz_pct", ascending=False)
    hbar(ax, o["owner_name"].tolist(), o["smz_pct"].tolist(),
         [f"{p:.2f}%  ({a:,.0f} ac)" for p, a in zip(o["smz_pct"], o["smz_acres"])],
         GREEN, "share of the class's attributed forest inside an SMZ (%)")
    style(ax, "2 · Riparian share by Harris ownership class",
          "Family forest carries 67% of the pilot's riparian acres")

    # ---- 3. What the corridors cut ----------------------------------------------------
    ax = axes[1, 0]
    bands = cut["units_cut"].tolist()
    n = cut["corridors"].tolist()
    ax.barh(range(len(bands)), n, color=AMBER, height=0.62, zorder=3)
    ax.set_yticks(range(len(bands)))
    ax.set_yticklabels(bands, color=INK, fontsize=9.5)
    ax.invert_yaxis()
    for i, (v, a) in enumerate(zip(n, cut["acres"])):
        ax.text(v + max(n) * 0.02, i, f"{v:,} corridors  ({a:,.0f} ac)",
                va="center", ha="left", fontsize=9, color=INK)
    ax.set_xlim(0, max(n) * 1.45)
    ax.set_ylabel("scheduling units the corridor cuts", fontsize=9, color=MUTED)
    ax.set_xlabel("riparian corridors (contiguous runs of buffered forest)",
                  fontsize=9, color=MUTED)
    style(ax, "3 · The buffer cuts stands — it is not an attribute of them",
          f"{size['corridors'].sum():,} corridors, median {corridors['acres'].median():.2f} ac; "
          f"{int((corridors['units_cut'] > 10).sum())} of them cut more than ten units")

    # ---- 4. What the override costs the decision space ---------------------------------
    ax = axes[1, 1]
    order = ["no_riparian", "riparian_stands"]
    labels = {
        "no_riparian": "before the carve\n(2026-08-17 baseline)",
        "riparian_stands": "after the carve\n(riparian corridors are stands)",
    }
    d = delta.set_index("scenario").loc[order]
    y = range(len(order))
    ax.barh(list(y), d["acres_with_a_cutting_option"] / 1000, color=BLUE, height=0.6,
            zorder=3, label="acres with at least one cutting option")
    ax.barh(list(y), d["riparian_acres"] / 1000, left=d["acres_with_a_cutting_option"] / 1000,
            color=AMBER, height=0.6, zorder=3, label="no-entry riparian acres")
    ax.set_yticks(list(y))
    ax.set_yticklabels(
        [f"{labels[s]}\n{int(d.loc[s, 'units']):,} stands · "
         f"{int(d.loc[s, 'fvs_runs']):,} FVS runs" for s in order],
        color=INK, fontsize=8.5)
    ax.invert_yaxis()
    for i, s in enumerate(order):
        ax.text(d.loc[s, "acres"] / 1000 * 1.03, i,
                f"{d.loc[s, 'riparian_acres']:,.0f} ac no-entry",
                va="center", ha="left", fontsize=9, color=INK)
    ax.set_xlim(0, d["acres"].max() / 1000 * 1.34)
    ax.set_xlabel("thousand acres of the attributed pilot landscape", fontsize=9, color=MUTED)
    style(ax, "4 · What the carve costs the decision space",
          "11,155 ac leave the harvestable base; the FVS batch barely moves")
    seg_handles = [
        mpatches.Patch(facecolor=BLUE, label="acres with at least one cutting option"),
        mpatches.Patch(facecolor=AMBER, label="no-entry riparian acres"),
    ]

    fig.suptitle("ARTEMIS pilot — joining the Florida BMP riparian layer to the scheduling landscape",
                 x=0.02, y=0.965, ha="left", fontsize=14.5, color=INK, weight="semibold")
    fig.text(0.02, 0.905,
             "NHD flowlines × config/bmp_rules.yaml buffer widths → riparian corridors carved "
             "out as stands in their own right → regime_assignment's absolute no-entry override, "
             "on real geometry for the first time.",
             ha="left", fontsize=10, color=MUTED)
    fig.legend(handles=seg_handles, loc="upper right", bbox_to_anchor=(0.975, 0.945),
               ncol=2, frameon=False, fontsize=9.5, labelcolor=INK, handlelength=1.6)
    fig.text(0.975, 0.015,
             "Source: weekly-artifact/2026-08-24/*.csv · produced by make_riparian_overlay.py",
             ha="right", fontsize=8, color=MUTED)

    out = OUT_DIR / "riparian_overlay.png"
    fig.savefig(out, dpi=185, facecolor=SURFACE)
    log.info("Wrote %s", out)


if __name__ == "__main__":
    main()
