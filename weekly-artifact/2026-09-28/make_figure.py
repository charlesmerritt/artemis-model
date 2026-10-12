"""Stage 4 — render what more searching bought, and what it cost.

Reads only committed CSVs — this artifact's and 2026-09-14's — so the figure regenerates
without re-running FVS or the annealer, and every plotted number is also in a committed table.

Four panels, in the order the argument runs:

  (a) **Where the restarts stop paying**: best-of-first-R against R, one line per cooling
      factor, with 2026-09-14's published best marked. This is the panel that answers the
      question the previous artifact asked.
  (b) **How wide the seeds are**: every search in the sweep as a point, so the spread the
      previous artifact flagged is shown as a distribution rather than as a range statistic.
  (c) **What the plan then delivered**: harvest per cycle against the TPO target, this
      plan beside 2026-09-14's. The library is identical, so any difference here is search.
  (d) **What it cost**: objective reached against CPU time spent, which is the honest way to
      report "more restarts help" — they help per search, and the price is linear.

Palette: the Okabe-Ito-derived categorical set this series uses, with the previous setting in
orange and the new one in blue throughout, so the reader learns the pairing once — the same
convention as 2026-09-14, where orange was 2026-08-31. Validated against the light surface
#fcfcfb by the dataviz palette checker (lightness band, chroma floor, CVD separation, normal-
vision floor). Every plotted value is also in a committed CSV, so no reading depends on
telling two colours apart.

Usage:
    uv run python weekly-artifact/2026-09-28/make_figure.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
PREV = OUT_DIR.parent / "2026-09-14"

SURFACE = "#fcfcfb"
INK = "#1a1a19"
INK_2 = "#55554f"
MUTED = "#8a8a82"
GRID = "#e4e4de"

BLUE = "#0072B2"      # the slower cooling factor, and this week's plan
ORANGE = "#D55E00"    # the config's cooling factor, and 2026-09-14, throughout
GREEN = "#009E73"

MCF = 1e6             # plot volumes in million cubic feet
N_CYCLES = 10
CONFIG_COOLING = 0.95


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=3, width=0.8)
    ax.yaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def _cycle_axis(ax):
    ax.set_xticks(range(1, N_CYCLES + 1))
    ax.set_xticklabels([f"{c}\n{2022 + 5 * c}" for c in range(1, N_CYCLES + 1)], fontsize=7)
    ax.set_xlabel("FVS cycle / calendar year", fontsize=8, color=INK_2)


# The config's cooling factor is always orange, as 2026-09-14 was; the slower ones take the
# remaining hues in order, slowest last. Read from the data rather than hardcoded so a sweep
# over a different set of factors still colours consistently.
_SLOWER_HUES = (BLUE, GREEN, "#CC79A7")


def _cooling_order(curve) -> list[float]:
    return sorted(curve["cooling_factor"].unique())


def _cooling_colour(alpha: float) -> str:
    if alpha == CONFIG_COOLING:
        return ORANGE
    rank = _COOLING_RANK.get(alpha, 0)
    return _SLOWER_HUES[rank % len(_SLOWER_HUES)]


def _cooling_label(alpha: float) -> str:
    return (f"cooling {alpha} (config)" if alpha == CONFIG_COOLING
            else f"cooling {alpha} (slower)")


# Filled by main() once the sweep's cooling factors are known.
_COOLING_RANK: dict[float, int] = {}


def _rank_coolings(alphas) -> None:
    slower = [a for a in sorted(alphas) if a != CONFIG_COOLING]
    _COOLING_RANK.clear()
    _COOLING_RANK.update({a: i for i, a in enumerate(slower)})


# --------------------------------------------------------------------------------------
# (a) where the restarts stop paying
# --------------------------------------------------------------------------------------

def panel_restart_curve(ax, curve: pd.DataFrame, prev_quality: dict):
    """Best-of-first-R against R. A step function by construction — it can only fall."""
    for alpha, g in curve.groupby("cooling_factor"):
        g = g.sort_values("restarts")
        ax.step(g["restarts"], g["best_of_first_r"], where="post", lw=1.8,
                color=_cooling_colour(alpha), zorder=3, label=_cooling_label(alpha))
        ax.scatter(g["restarts"], g["objective_this_seed"], s=9, alpha=0.35,
                   color=_cooling_colour(alpha), zorder=2, linewidths=0)
    published = prev_quality["objective_best"]
    ax.axhline(published, color=INK, lw=1.2, ls=(0, (5, 3)), zorder=4)
    ax.text(curve["restarts"].max() * 0.99, published,
            f"  2026-09-14 published: {published:.2f}", fontsize=7, color=INK,
            va="bottom", ha="right")
    ax.set_xlabel("restarts (seeds 42, 43, … consumed in order)", fontsize=8, color=INK_2)
    ax.set_ylabel("objective, best of the first R  (lower is better)", fontsize=8, color=INK_2)
    ax.set_title("(a)  Where the restarts stop paying", fontsize=9.5, color=INK,
                 loc="left", pad=8)
    ax.set_xticks([1] + list(range(5, int(curve["restarts"].max()) + 1, 5)))
    ax.legend(fontsize=7, frameon=False, labelcolor=INK_2, loc="upper right")


# --------------------------------------------------------------------------------------
# (b) how wide the seeds are
# --------------------------------------------------------------------------------------

def panel_spread(ax, detail: pd.DataFrame, arms: pd.DataFrame):
    """Every search as a point, so the spread is a distribution rather than a range.

    The previous artifact's five seeds are the left-hand group's first five, marked, because
    "the spread got worse" was its caveat and this is the same measurement carried forward.
    """
    alphas = sorted(detail["cooling_factor"].unique())
    for i, alpha in enumerate(alphas):
        g = detail[detail["cooling_factor"] == alpha].sort_values("seed")
        colour = _cooling_colour(alpha)
        jitter = [i + (j % 7 - 3) * 0.028 for j in range(len(g))]
        ax.scatter(jitter, g["objective"], s=26, color=colour, alpha=0.55, zorder=3,
                   linewidths=0)
        lo, hi = g["objective"].min(), g["objective"].max()
        ax.plot([i, i], [lo, hi], color=colour, lw=1.0, alpha=0.5, zorder=2)
        ax.scatter([i], [lo], s=58, facecolor="none", edgecolor=INK, lw=1.2, zorder=5)
        ax.text(i + 0.16, lo, f"best {lo:.2f}", fontsize=7, color=INK, va="center")
        ax.text(i + 0.16, hi, f"worst {hi:.2f}", fontsize=7, color=INK_2, va="center")
        n = len(g)
        row = arms[(arms["cooling_factor"] == alpha) & (arms["restarts"] == n)]
        if len(row):
            ax.text(i, hi + (hi - lo) * 0.16,
                    f"spread {float(row.iloc[0]['seed_spread']):.2f}"
                    f"  ({float(row.iloc[0]['seed_spread_pct_of_best']):.1f}% of best)",
                    fontsize=7, color=colour, ha="center")
    ax.set_xticks(range(len(alphas)))
    ax.set_xticklabels([_cooling_label(a) for a in alphas], fontsize=7.5)
    ax.xaxis.grid(False)
    ax.set_xlim(-0.5, len(alphas) - 0.22)
    ax.set_ylabel("objective of one search", fontsize=8, color=INK_2)
    ax.set_title("(b)  How wide the seeds are", fontsize=9.5, color=INK, loc="left", pad=14)


# --------------------------------------------------------------------------------------
# (c) the plan against its target
# --------------------------------------------------------------------------------------

def panel_plan(ax):
    prev = pd.read_csv(PREV / "harvest_by_cycle.csv").set_index("cycle")
    now = pd.read_csv(OUT_DIR / "harvest_by_cycle.csv").set_index("cycle")
    x = list(range(1, N_CYCLES + 1))
    w = 0.38
    prev_v = [prev["cuft"].get(c, 0) / MCF for c in x]
    now_v = [now["cuft"].get(c, 0) / MCF for c in x]
    ax.bar([i - w / 2 - 0.01 for i in x], prev_v, width=w, color=ORANGE, zorder=3,
           label="2026-09-14 plan (5 restarts, cooling 0.95)")
    ax.bar([i + w / 2 + 0.01 for i in x], now_v, width=w, color=BLUE, zorder=3,
           label="2026-09-28 plan (this sweep's best)")
    target = float(now["target_cuft"].iloc[0]) / MCF
    ax.axhline(target, color=INK, lw=1.4, ls=(0, (5, 3)), zorder=4)
    ax.set_ylim(0, target * 1.34)
    ax.text(0.62, target * 1.02, "TPO target", fontsize=7, color=INK, va="bottom", ha="left")
    ax.set_ylabel("harvest volume (million ft³ merch.)", fontsize=8, color=INK_2)
    ax.set_title("(c)  Same library, so this is all search", fontsize=9.5, color=INK,
                 loc="left", pad=8)
    _cycle_axis(ax)
    ax.legend(fontsize=7, frameon=False, labelcolor=INK_2, loc="upper right")


# --------------------------------------------------------------------------------------
# (d) what it cost
# --------------------------------------------------------------------------------------

def panel_cost(ax, curve: pd.DataFrame):
    """Objective against cumulative CPU minutes: the price of each increment of search."""
    for alpha, g in curve.groupby("cooling_factor"):
        g = g.sort_values("restarts").copy()
        g["cpu_minutes"] = g["seconds_this_seed"].cumsum() / 60.0
        ax.step(g["cpu_minutes"], g["best_of_first_r"], where="post", lw=1.8,
                color=_cooling_colour(alpha), zorder=3, label=_cooling_label(alpha))
        last = g.iloc[-1]
        ax.scatter([last["cpu_minutes"]], [last["best_of_first_r"]], s=30,
                   color=_cooling_colour(alpha), zorder=4, linewidths=0)
        ax.text(last["cpu_minutes"], last["best_of_first_r"],
                f"  {int(last['restarts'])} restarts\n  {last['best_of_first_r']:.2f}",
                fontsize=7, color=_cooling_colour(alpha), va="top", ha="right")
    ax.set_xlabel("cumulative CPU minutes spent searching", fontsize=8, color=INK_2)
    ax.set_ylabel("objective reached", fontsize=8, color=INK_2)
    ax.set_title("(d)  …and what it cost", fontsize=9.5, color=INK, loc="left", pad=8)
    ax.legend(fontsize=7, frameon=False, labelcolor=INK_2, loc="upper right")


def main() -> None:
    curve = pd.read_csv(OUT_DIR / "restart_curve.csv")
    detail = pd.read_csv(OUT_DIR / "seed_spread.csv")
    _rank_coolings(curve["cooling_factor"].unique())
    arms = pd.read_csv(OUT_DIR / "arm_summary.csv")
    quality = json.loads((OUT_DIR / "solution_quality.json").read_text())
    prev_quality = json.loads((PREV / "solution_quality.json").read_text())

    fig, axes = plt.subplots(2, 2, figsize=(13.6, 8.6), facecolor=SURFACE)
    for ax in axes.flat:
        _style(ax)

    panel_restart_curve(axes[0][0], curve, prev_quality)
    panel_spread(axes[0][1], detail, arms)
    panel_plan(axes[1][0])
    panel_cost(axes[1][1], curve)

    search = quality["search"]
    prev_obj = prev_quality["objective_best"]
    now_obj = quality["objective_best"]
    fig.suptitle("ARTEMIS — how much of the plan was search error (2026-09-28)",
                 fontsize=13, color=INK, x=0.055, ha="left", y=0.975, weight="medium")
    fig.text(0.055, 0.935,
             f"The same {quality['library_identical_to_20260914']['runs_compared']:,}-run "
             f"library as 2026-09-14, asserted identical after a fresh FVS build; only "
             f"restarts and the cooling factor changed.  "
             f"{search['searches_run']} searches.  "
             f"Objective {prev_obj:.2f} → {now_obj:.2f} "
             f"({100 * (prev_obj - now_obj) / prev_obj:.1f}% of it was search error, not the "
             f"landscape).",
             fontsize=8.5, color=INK_2, ha="left")
    fig.text(0.055, 0.012,
             "Library, landscape, TPO targets, objective weights, greedy start and move "
             "mixture are 2026-09-14's; the search code is imported from that artifact "
             "unmodified.\n"
             "Spatial penalties (adjacency, green-up, opening size) remain unavailable on a "
             "pixel-class landscape.  Every value plotted is in a committed CSV.",
             fontsize=7, color=MUTED, ha="left", linespacing=1.5)

    fig.tight_layout(rect=(0.045, 0.055, 0.985, 0.925))
    out = OUT_DIR / "convergence.png"
    fig.savefig(out, dpi=170, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
