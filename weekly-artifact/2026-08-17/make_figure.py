"""
Render the trajectory-library figure from the CSVs `make_trajectory_library.py` wrote.

Kept separate from the driver on purpose: this reads only the committed CSVs, so the
figure regenerates from the artifact folder alone, with no R2 access and no FVS inputs.

    uv run python weekly-artifact/2026-08-17/make_figure.py

Palette: the dataviz reference palette (light mode), the same one
`weekly-artifact/2026-07-26/make_figures.py` established for this repository.
Validated with the skill's own checker before use:

    validate_palette.js "#2a78d6,#eb6834" --mode light --pairs all   -> all checks pass

Panel C is the only categorical encoding (two states), so two hues is the whole
requirement; the sequential panels use one hue's ramp and carry direct labels.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent

BLUE, ORANGE = "#2a78d6", "#eb6834"
SEQ = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
INK, INK2, INK3 = "#0b0b0b", "#52514e", "#8a8880"
SURFACE, GRID = "#fcfcfb", "#e7e6e2"

SI_BINS = 3
DECLARED_BOUND = 16632          # config/management_regimes.yaml: 693 x 8 x 3

PRESCRIPTION_ORDER = [
    "no_management", "family_light_thin", "family_uneven_aged_selection",
    "public_selection_light", "public_thin_restore",
    "pine_plantation_long_rotation", "pine_plantation_short_rotation",
    "hardwood_clearcut_regen",
]


def style(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=3, color=GRID)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=INK, fontsize=10.5, loc="left", pad=10)
    if xlabel:
        ax.set_xlabel(xlabel, color=INK2, fontsize=8.5)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=8.5)
    return ax


def short(name: str) -> str:
    return name.replace("_", " ").replace("pine plantation", "pine plant.")


# --------------------------------------------------------------------------------------

def panel_funnel(ax, runs: pd.DataFrame) -> None:
    """A: what the declared bound assumed vs what the landscape actually requires."""
    distinct_keys = runs["keyfile_sha256_16"].nunique()
    steps = [
        ("Declared upper bound\n693 x 8 prescriptions x 3 SI bins", DECLARED_BOUND, SEQ[1]),
        ("Eligible pairs\nafter owner + branch screening", len(runs) * SI_BINS, SEQ[3]),
        ("Distinct keyfiles\nafter dropping identical renders", distinct_keys * SI_BINS, SEQ[5]),
    ]
    xmax = DECLARED_BOUND * 1.42
    y = np.arange(len(steps))[::-1]
    for yi, (label, value, color) in zip(y, steps):
        ax.barh(yi, value, color=color, height=0.52, zorder=3)
        ax.text(value + 260, yi, f"{value:,}", va="center", ha="left",
                color=INK, fontsize=9.5, fontweight="bold", zorder=4)
        pct = 100 * value / DECLARED_BOUND
        if value != DECLARED_BOUND:
            # right-aligned at the axis edge, so it can never collide with the value label
            ax.text(xmax * 0.995, yi, f"{pct:.0f}% of the bound", va="center", ha="right",
                    color=INK3, fontsize=7.8, zorder=4)
    ax.set_yticks(y)
    ax.set_yticklabels([s[0] for s in steps], fontsize=8, color=INK2)
    ax.set_xlim(0, xmax)
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    style(ax, "A. The FVS batch the pilot actually requires", xlabel="FVS runs")


def panel_menu_matrix(ax, menu: pd.DataFrame, lib: pd.DataFrame) -> None:
    """B: which prescriptions each part of the landscape may choose among, by acres."""
    grid = (lib.drop_duplicates(["unit_id", "prescription"])
               .pivot_table(index=["owner_class", "forest_branch"], columns="prescription",
                            values="acres", aggfunc="sum"))
    order = (menu.set_index(["owner_class", "forest_branch"])["acres"]
                 .sort_values(ascending=False).index)
    grid = grid.reindex(index=order, columns=PRESCRIPTION_ORDER) / 1e3

    masked = np.ma.masked_invalid(grid.to_numpy())
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("seq", SEQ)
    cmap.set_bad(SURFACE)
    im = ax.imshow(masked, cmap=cmap, aspect="auto",
                   norm=matplotlib.colors.LogNorm(vmin=0.5, vmax=np.nanmax(grid.to_numpy())))

    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            v = grid.to_numpy()[i, j]
            if np.isnan(v):
                continue
            frac = np.log10(max(v, 0.5)) / np.log10(np.nanmax(grid.to_numpy()))
            ax.text(j, i, f"{v:,.0f}", ha="center", va="center", fontsize=6.9,
                    color="#ffffff" if frac > 0.62 else INK)

    ax.set_xticks(range(grid.shape[1]))
    ax.set_xticklabels([short(c) for c in grid.columns], rotation=35, ha="right",
                       fontsize=7.4, color=INK2)
    ax.set_yticks(range(grid.shape[0]))
    ax.set_yticklabels([f"{o.replace('_', ' ')} · {b}" for o, b in grid.index],
                       fontsize=7.4, color=INK2)
    ax.set_xticks(np.arange(-0.5, grid.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-0.5, grid.shape[0], 1), minor=True)
    ax.grid(which="minor", color=SURFACE, lw=2)
    ax.tick_params(which="minor", length=0)
    ax.tick_params(colors=INK2, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("B. The eligible menu, realised on the landscape\n"
                 "thousand acres eligible  ·  blank = not eligible",
                 color=INK, fontsize=10.5, loc="left", pad=10)


def panel_degradation(ax, runs: pd.DataFrame) -> None:
    """C: how many runs resolve as declared, and how many degrade to a bare clearcut."""
    runs = runs.copy()
    declared_template = {
        "no_management": "no_management", "family_light_thin": "thin_from_below",
        "family_uneven_aged_selection": "selection_harvest",
        "public_selection_light": "selection_harvest",
        "public_thin_restore": "thin_from_below_repeated",
        "pine_plantation_long_rotation": "plantation_rotation",
        "pine_plantation_short_rotation": "plantation_rotation",
        "hardwood_clearcut_regen": "clearcut",
    }
    runs["degraded"] = runs.apply(
        lambda r: r["template"] != declared_template[r["prescription"]], axis=1)
    tab = (runs.groupby(["prescription", "degraded"]).size().unstack(fill_value=0)
              .reindex(PRESCRIPTION_ORDER, fill_value=0))
    as_declared = tab.get(False, pd.Series(0, index=tab.index))
    degraded = tab.get(True, pd.Series(0, index=tab.index))

    y = np.arange(len(tab))[::-1]
    ax.barh(y, as_declared, color=BLUE, height=0.6, zorder=3, label="resolves as declared")
    ax.barh(y, degraded, left=as_declared + 6, color=ORANGE, height=0.6, zorder=3,
            label="thin dropped — stand past rotation age, so a bare clearcut")
    for yi, a, d in zip(y, as_declared, degraded):
        total = a + d
        ax.text(total + 14, yi, f"{total:,}", va="center", ha="left", color=INK,
                fontsize=8.4, zorder=4)
        if d:
            ax.text(a + d / 2 + 6, yi, f"{d}", va="center", ha="center", color="#ffffff",
                    fontsize=7.6, fontweight="bold", zorder=4)
    ax.set_yticks(y)
    ax.set_yticklabels([short(p) for p in tab.index], fontsize=7.8, color=INK2)
    ax.set_xlim(0, max(as_declared + degraded) * 1.22)
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    style(ax, "C. FVS runs per prescription, and where the prescription degrades",
          xlabel="distinct (stand x prescription) runs")
    # Clear above the title: every in-plot corner is occupied by a bar or its value label.
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.105), frameon=False, fontsize=7.4,
              labelcolor=INK2, handlelength=1.1, borderpad=0.2, ncols=2, columnspacing=1.2)


def panel_entries(ax, runs: pd.DataFrame) -> None:
    """D: the intensity ladder — how many entries each prescription places in 50 years."""
    stats = (runs.groupby("prescription")["n_entries"].agg(["min", "max", "mean"])
                 .reindex(PRESCRIPTION_ORDER))
    y = np.arange(len(stats))[::-1]
    for yi, (lo, hi, mean) in zip(y, stats.itertuples(index=False)):
        if hi > lo:
            ax.plot([lo, hi], [yi, yi], color=SEQ[1], lw=3.4, solid_capstyle="round", zorder=2)
        ax.plot([mean], [yi], "o", ms=8.5, color=BLUE, mec=SURFACE, mew=2, zorder=4)
        label = f"{mean:.2f}" if hi > lo else f"{int(mean)}"
        ax.text(hi + 0.16, yi, label, va="center", ha="left", color=INK, fontsize=8.4)
    ax.set_yticks(y)
    ax.set_yticklabels([short(p) for p in stats.index], fontsize=7.8, color=INK2)
    ax.set_xlim(-0.28, 4.9)
    ax.set_xticks(range(5))
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    style(ax, "D. Harvest entries scheduled inside the 50-year horizon",
          xlabel="entries per trajectory  ·  dot = mean, bar = range across stands")
    ax.xaxis.set_label_coords(0.5, -0.075)


def main() -> None:
    lib = pd.read_csv(HERE / "trajectory_library.csv", dtype={"PLT_CN": "string"})
    runs = pd.read_csv(HERE / "fvs_run_manifest.csv", dtype={"PLT_CN": "string"})
    menu = pd.read_csv(HERE / "library_menu_realized.csv")

    fig = plt.figure(figsize=(15.2, 11.4), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.30,
                          left=0.135, right=0.985, top=0.885, bottom=0.105)
    panel_funnel(fig.add_subplot(gs[0, 0]), runs)
    panel_menu_matrix(fig.add_subplot(gs[0, 1]), menu, lib)
    panel_degradation(fig.add_subplot(gs[1, 0]), runs)
    panel_entries(fig.add_subplot(gs[1, 1]), runs)

    fig.suptitle("ARTEMIS trajectory library — the decision space the annealer will search",
                 color=INK, fontsize=14.5, x=0.028, ha="left", y=0.972)
    fig.text(0.028, 0.930,
             f"Five-county north-Florida pilot · {lib['unit_id'].nunique():,} units · "
             f"{lib['PLT_CN'].nunique()} FIA stands · "
             f"{lib.drop_duplicates('unit_id')['acres'].sum():,.0f} acres · "
             f"{len(lib):,} (unit x prescription) options · {len(runs):,} distinct FVS runs",
             color=INK2, fontsize=9.6, ha="left")
    fig.text(0.028, 0.010,
             "Enumerated by weekly-artifact/2026-08-17/make_trajectory_library.py from "
             "pipeline.s3_management.regime_assignment (eligible menu, schedule resolution) and "
             "pipeline.s4_fvs.regime_templates (keyfile rendering).\n"
             "Landscape attribution reused from weekly-artifact/2026-08-10/make_schedule.py: "
             "TreeMap 2022 five-county raster x county polygons x Harris et al. 2025 ownership. "
             "Riparian units are absent because no BMP layer is joined yet (SMZ_Pct = 0).",
             color=INK3, fontsize=7.4, va="bottom", ha="left")

    out = HERE / "trajectory_library.png"
    fig.savefig(out, dpi=170, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
