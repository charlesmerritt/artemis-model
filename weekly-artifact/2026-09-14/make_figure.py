"""Stage 3 — render what the timing grid changed.

Reads only committed CSVs — this artifact's and 2026-08-31's — so the figure can be
regenerated without re-running FVS or the annealer, and every plotted number is also in a
committed table.

Four panels, in the order the argument runs:

  (a) **What the library can do at all**: how many trajectories are able to cut in each
      cycle, before and after the grid. This is the panel that carries the mechanism —
      last week's library could not cut in cycle 10 at any selection, and barely in cycle 5.
  (b) **What that made unreachable**: targets proven outside the library's attainable range,
      per cycle, out of eight. The count `attainable_envelope.csv` reports, before and after.
  (c) **What the plan then delivered**: harvest volume per cycle against the TPO target,
      before and after.
  (d) **What the scheduler did with the new axis**: acreage by the timing offset it chose.
      A magnitude ranking, so one hue rather than cycling categorical hues.

Palette: the Okabe-Ito-derived categorical set used by every figure in this series, with
2026-08-31 in orange and this week in blue throughout, so the reader learns the pairing once.
Validated with the dataviz palette checker against the light surface #fcfcfb — lightness
band, chroma floor, CVD separation (worst adjacent pair ΔE 11.0 deutan) and normal-vision
floor all pass. Labels are direct but selective — every bar in (b) and (d), and in (a) the
two cycles the panel is about — and every plotted value is also in a committed CSV, so no
reading depends on telling two colours apart.

Usage:
    uv run python weekly-artifact/2026-09-14/make_figure.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
PREV = OUT_DIR.parent / "2026-08-31"

SURFACE = "#fcfcfb"
INK = "#1a1a19"
INK_2 = "#55554f"
MUTED = "#8a8a82"
GRID = "#e4e4de"

BLUE = "#0072B2"      # this artifact, 2026-09-14
ORANGE = "#D55E00"    # 2026-08-31, throughout
GREEN = "#009E73"

MCF = 1e6             # plot volumes in million cubic feet
N_CYCLES = 10


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


def _label_bars(ax, xs, ys, values, color, fmt="{:.0f}", dy=0.015):
    span = ax.get_ylim()[1] - ax.get_ylim()[0]
    for x, y, v in zip(xs, ys, values):
        ax.text(x, y + dy * span, fmt.format(v), ha="center", va="bottom", fontsize=6,
                color=color)


# --------------------------------------------------------------------------------------
# (a) what the library can do at all
# --------------------------------------------------------------------------------------

def panel_reach(ax):
    """Trajectories able to cut in each cycle, before and after the timing grid.

    "Before" is counted from 2026-08-31's own committed `trajectory_harvest_by_cycle.csv`
    rather than quoted, so the two bars are the same measurement on the two libraries. The
    counts are not comparable in absolute size — this library has 3.5x the runs — so the
    panel is about *which cycles are reachable at all*, and cycle 10 is the point: zero runs,
    then 1,700-odd.
    """
    prev = pd.read_csv(PREV / "trajectory_harvest_by_cycle.csv")
    prev_reach = (prev[prev["cycle"].between(1, N_CYCLES)]
                  .assign(cut=lambda d: d["removed_merch_cuft_per_ac"] > 0)
                  .groupby("cycle")["cut"].sum())
    now = pd.read_csv(OUT_DIR / "library_reach_by_cycle.csv").set_index("cycle")

    x = list(range(1, N_CYCLES + 1))
    w = 0.38
    prev_v = [int(prev_reach.get(c, 0)) for c in x]
    now_v = [int(now["runs_cutting"].get(c, 0)) for c in x]
    ax.bar([i - w / 2 - 0.01 for i in x], prev_v, width=w, color=ORANGE, zorder=3,
           label="2026-08-31 library (3,781 runs)")
    n_runs = pd.read_csv(OUT_DIR / "trajectory_index.csv", usecols=["fvs_run_id"]).shape[0]
    ax.bar([i + w / 2 + 0.01 for i in x], now_v, width=w, color=BLUE, zorder=3,
           label=f"2026-09-14 library, with timing offsets ({n_runs:,} runs)")
    # Selective labels: the two cycles the panel is about. A number on all twenty bars
    # collides at four digits and says less.
    for c in (5, 10):
        i = x.index(c)
        _label_bars(ax, [c - w / 2 - 0.01], [prev_v[i]], [prev_v[i]], ORANGE)
        _label_bars(ax, [c + w / 2 + 0.01], [now_v[i]], [now_v[i]], BLUE)
    ax.set_ylim(0, max(now_v) * 1.22)
    ax.set_ylabel("trajectories able to cut in this cycle", fontsize=8, color=INK_2)
    ax.set_title("(a)  What the library can do at all", fontsize=9.5, color=INK,
                 loc="left", pad=8)
    _cycle_axis(ax)
    ax.legend(fontsize=7, frameon=False, labelcolor=INK_2, loc="upper right")


# --------------------------------------------------------------------------------------
# (b) targets proven unreachable
# --------------------------------------------------------------------------------------

def panel_unreachable(ax, comparison: pd.DataFrame):
    rows = comparison[comparison["measure"].str.startswith("targets proven unreachable, cycle")]
    rows = rows.assign(cycle=rows["measure"].str.extract(r"cycle (\d+)").astype(int)[0])
    rows = rows.sort_values("cycle")
    x = list(rows["cycle"])
    w = 0.38
    prev_v = list(rows["value_20260831"].astype(int))
    now_v = list(rows["value_20260914"].astype(int))
    ax.bar([i - w / 2 - 0.01 for i in x], prev_v, width=w, color=ORANGE, zorder=3,
           label="2026-08-31")
    ax.bar([i + w / 2 + 0.01 for i in x], now_v, width=w, color=BLUE, zorder=3,
           label="2026-09-14")
    _label_bars(ax, [i - w / 2 - 0.01 for i in x], prev_v, prev_v, ORANGE)
    _label_bars(ax, [i + w / 2 + 0.01 for i in x], now_v, now_v, BLUE)
    # The axis tops out at the eight targets a cycle has, so 8 needs no reference line.
    ax.set_ylim(0, 10.6)
    ax.set_yticks(range(0, 9, 2))
    ax.set_ylabel("targets proven unreachable (of 8)", fontsize=8, color=INK_2)
    ax.set_title("(b)  …and what that put out of reach", fontsize=9.5, color=INK,
                 loc="left", pad=8)
    _cycle_axis(ax)
    ax.legend(fontsize=7, frameon=False, labelcolor=INK_2, loc="upper left")


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
           label="2026-08-31 plan")
    ax.bar([i + w / 2 + 0.01 for i in x], now_v, width=w, color=BLUE, zorder=3,
           label="2026-09-14 plan")
    target = float(now["target_cuft"].iloc[0]) / MCF
    ax.axhline(target, color=INK, lw=1.4, ls=(0, (5, 3)), zorder=4)
    # Headroom above the target line, so the label and the legend each have their own space.
    ax.set_ylim(0, target * 1.34)
    ax.text(0.62, target * 1.02, "TPO target", fontsize=7, color=INK, va="bottom", ha="left")
    ax.set_ylabel("harvest volume (million ft³ merch.)", fontsize=8, color=INK_2)
    ax.set_title("(c)  …and what the plan then delivered", fontsize=9.5, color=INK,
                 loc="left", pad=8)
    _cycle_axis(ax)
    ax.legend(fontsize=7, frameon=False, labelcolor=INK_2, loc="upper right", ncol=2)


# --------------------------------------------------------------------------------------
# (d) the offsets the scheduler chose
# --------------------------------------------------------------------------------------

def panel_offsets(ax):
    """Acreage by chosen timing offset. A magnitude ranking, so one hue, not six."""
    t = pd.read_csv(OUT_DIR / "timing_offsets_chosen.csv").sort_values("timing_offset_years")
    labels = [f"+{int(o)} yr" if o else "as resolved\n(+0 yr)"
              for o in t["timing_offset_years"]]
    acres = t["acres"] / 1000.0
    bars = ax.barh(range(len(t)), acres, color=BLUE, zorder=3, height=0.62)
    ax.set_yticks(range(len(t)))
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.invert_yaxis()
    ax.xaxis.grid(True, color=GRID, lw=0.8)
    ax.yaxis.grid(False)
    span = max(acres) if len(acres) else 1.0
    for bar, a, n in zip(bars, acres, t["stands"]):
        ax.text(bar.get_width() + span * 0.02, bar.get_y() + bar.get_height() / 2,
                f"{a:,.0f}k ac · {int(n):,} stands", va="center", fontsize=6.8, color=INK_2)
    ax.set_xlim(0, span * 1.38)
    ax.set_xlabel("thousand acres on a cutting trajectory", fontsize=8, color=INK_2)
    ax.set_title("(d)  The delay the scheduler chose", fontsize=9.5, color=INK,
                 loc="left", pad=8)


def main() -> None:
    comparison = pd.read_csv(OUT_DIR / "comparison_to_20260831.csv")
    quality = json.loads((OUT_DIR / "solution_quality.json").read_text())
    prev_quality = json.loads((PREV / "solution_quality.json").read_text())

    fig, axes = plt.subplots(2, 2, figsize=(13.6, 8.6), facecolor=SURFACE)
    for ax in axes.flat:
        _style(ax)

    panel_reach(axes[0][0])
    panel_unreachable(axes[0][1], comparison)
    panel_plan(axes[1][0])
    panel_offsets(axes[1][1])

    unreachable_now = quality["targets_unreachable_from_library"]
    unreachable_prev = prev_quality["targets_unreachable_from_library"]
    fig.suptitle(
        "ARTEMIS — adding timing to the decision space (2026-09-14)",
        fontsize=13, color=INK, x=0.055, ha="left", y=0.975, weight="medium")
    fig.text(0.055, 0.935,
             f"Four timing variants of every cutting prescription, and an eleventh FVS cycle "
             f"so the horizon's final year can be harvested at all.  "
             f"Targets proven unreachable: {unreachable_prev} → {unreachable_now} of 80.  "
             f"Objective {prev_quality['objective_best']:.1f} → {quality['objective_best']:.1f} "
             f"(lower is better).",
             fontsize=8.5, color=INK_2, ha="left")
    fig.text(0.055, 0.012,
             "Landscape, targets, objective weights, cooling schedule and seeds are unchanged "
             "from 2026-08-31; only the menu each stand chooses from differs.\n"
             "Spatial penalties (adjacency, green-up, opening size) remain unavailable on a "
             "pixel-class landscape.  Every value plotted is in a committed CSV.",
             fontsize=7, color=MUTED, ha="left", linespacing=1.5)

    fig.tight_layout(rect=(0.045, 0.055, 0.985, 0.925))
    out = OUT_DIR / "timing_library.png"
    fig.savefig(out, dpi=170, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
