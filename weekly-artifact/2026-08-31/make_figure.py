"""Stage 3 — render the annealed plan and its solution-quality report.

Reads only the committed CSVs and `solution_quality.json` next to it, so the figure can be
regenerated without re-running FVS or the annealer.

Four panels, in the order a reader needs them:

  (a) The plan against its target, per cycle, with the library's own attainable ceiling
      drawn on top. This is the panel that carries the finding: the annealed plan tracks
      the ceiling closely, and it is the *ceiling* that misses the target.
  (b) Attainability by county and cycle — the ceiling as a percentage of target, diverging
      around 100%. Blue can reach the target, orange cannot, at any selection.
  (c) The chosen prescription mix by acreage.
  (d) Solution quality: the annealed objective against the greedy and random baselines and
      the relaxation bound.

Palette: the Okabe-Ito-derived categorical set used by every figure in this series,
validated with the dataviz palette checker against the light surface #fcfcfb (lightness
band, chroma floor, normal-vision floor all pass; worst adjacent CVD pair is in the 6-8
floor band, which is legal with the secondary encoding used here — every low-contrast hue
carries a direct value label, and every plotted number is also in a committed CSV).

Usage:
    uv run python weekly-artifact/2026-08-31/make_figure.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent

SURFACE = "#fcfcfb"
INK = "#1a1a19"
INK_2 = "#55554f"
MUTED = "#8a8a82"
GRID = "#e4e4de"

BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
PINK = "#CC79A7"
AMBER = "#E69F00"
SKY = "#56B4E9"
NEUTRAL = "#d9d9d3"

CAT = [BLUE, ORANGE, GREEN, PINK, AMBER, SKY]

MCF = 1e6   # plot in million cubic feet


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=3, width=0.8)
    ax.yaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def panel_plan(ax, cycles: pd.DataFrame, envelope: pd.DataFrame):
    """Achieved volume per cycle, its target, and the library's attainable ceiling."""
    ceiling = (envelope[envelope.dimension == "county"]
               .groupby("cycle", as_index=False)["max_attainable_cuft"].sum())
    x = cycles["cycle"]
    ax.bar(x, cycles["cuft"] / MCF, width=0.62, color=BLUE, zorder=3,
           label="Annealed plan")
    ax.plot(ceiling["cycle"], ceiling["max_attainable_cuft"] / MCF, marker="o", ms=5,
            lw=2, color=ORANGE, zorder=4, label="Library ceiling (max attainable)")
    target = cycles["target_cuft"].iloc[0] / MCF
    ax.axhline(target, ls=(0, (5, 3)), lw=2, color=INK_2, zorder=2,
               label="TPO target (2013–2024)")

    # Value labels sit inside the bars: the ceiling line runs through the same x
    # positions, so labels above the bars would collide with its markers.
    for xi, yi in zip(x, cycles["cuft"] / MCF):
        if yi >= 100:
            ax.annotate(f"{yi:,.0f}", (xi, yi), textcoords="offset points", xytext=(0, -12),
                        ha="center", fontsize=7.5, color=SURFACE, zorder=5)
        elif yi > 0:
            ax.annotate(f"{yi:,.0f}", (xi, yi), textcoords="offset points", xytext=(13, -3),
                        ha="center", fontsize=7.5, color=INK_2, zorder=5)

    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{y}" for y in cycles["calendar_year"]], rotation=45, ha="right")
    ax.set_ylabel("Removed merchantable volume  (million ft³ / 5-yr cycle)",
                  fontsize=8.5, color=INK_2)
    ax.set_title("(a)  The plan tracks the library's ceiling — the ceiling misses the target",
                 loc="left", fontsize=10.5, color=INK, pad=8)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="upper center",
              bbox_to_anchor=(0.5, -0.20), ncol=3, handletextpad=0.5, columnspacing=1.4)
    _style(ax)


def panel_attainability(ax, envelope: pd.DataFrame):
    """Ceiling as a percentage of target, county x cycle. Diverging around 100%."""
    c = envelope[envelope.dimension == "county"]
    piv = c.pivot_table(index="key", columns="cycle", values="max_as_pct_of_target")
    piv = piv.reindex(sorted(piv.index))

    cmap = LinearSegmentedColormap.from_list("reach", [ORANGE, NEUTRAL, BLUE])
    vmax = float(piv.to_numpy().max())
    norm = TwoSlopeNorm(vmin=0, vcenter=100, vmax=max(vmax, 101))
    ax.imshow(piv.to_numpy(), cmap=cmap, norm=norm, aspect="auto")

    ax.set_xticks(range(piv.shape[1]))
    ax.set_xticklabels([str(c) for c in piv.columns], fontsize=8)
    ax.set_yticks(range(piv.shape[0]))
    ax.set_yticklabels(piv.index, fontsize=8)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.to_numpy()[i, j]
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=7,
                    color=INK if 40 < v < 160 else SURFACE)
    ax.set_xlabel("cycle", fontsize=8.5, color=INK_2)
    ax.set_title("(b)  Attainable ceiling as % of county target", loc="left",
                 fontsize=10.5, color=INK, pad=8)
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK_2, length=0)
    handles = [Line2D([], [], marker="s", ls="", ms=8, color=BLUE, label="ceiling ≥ target"),
               Line2D([], [], marker="s", ls="", ms=8, color=NEUTRAL, label="at target"),
               Line2D([], [], marker="s", ls="", ms=8, color=ORANGE,
                      label="ceiling < target — unreachable by any selection")]
    ax.legend(handles=handles, frameon=False, fontsize=8, labelcolor=INK_2,
              loc="upper left", bbox_to_anchor=(0.0, -0.13), ncol=3,
              handletextpad=0.4, columnspacing=1.4)


def panel_mix(ax, mix: pd.DataFrame):
    """Acreage by prescription — a magnitude ranking, so one hue, not eight.

    Categorical hues would have to cycle past six here and colour would duplicate
    identity the axis labels already carry. `no_management` is held out in the neutral
    because it is the one option that is not a treatment.
    """
    m = (mix.groupby("prescription", as_index=False)
         .agg(acres=("acres", "sum"), removed=("removed_cuft", "sum"))
         .sort_values("acres"))
    colors = [NEUTRAL if p == "no_management" else BLUE for p in m["prescription"]]
    ax.barh(m["prescription"], m["acres"] / 1000, color=colors, height=0.62, zorder=3)
    for p, a in zip(m["prescription"], m["acres"] / 1000):
        ax.annotate(f"{a:,.0f}k ac", (a, p), textcoords="offset points", xytext=(4, 0),
                    va="center", fontsize=7.5, color=INK_2)
    ax.set_xlim(0, (m["acres"].max() / 1000) * 1.28)
    ax.set_xlabel("thousand acres", fontsize=8.5, color=INK_2)
    ax.set_title("(c)  Chosen prescription mix", loc="left", fontsize=10.5, color=INK, pad=8)
    _style(ax)
    ax.xaxis.grid(True, color=GRID, lw=0.8)
    ax.yaxis.grid(False)


def panel_quality(ax, q: dict, seeds: pd.DataFrame):
    labels = ["Random\n(mean of 5)", "Greedy\nbaseline", "Annealed\n(best of 5)",
              "Relaxation\nbound"]
    values = [q["objective_random_baseline_mean"], q["objective_greedy_baseline"],
              q["objective_best"], q["relaxation_bound"]]
    colors = [MUTED, AMBER, BLUE, NEUTRAL]
    bars = ax.bar(labels, values, color=colors, width=0.6, zorder=3)
    for b, v in zip(bars, values):
        ax.annotate(f"{v:,.1f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 4), ha="center",
                    fontsize=8, color=INK_2)
    lo, hi = seeds["objective"].min(), seeds["objective"].max()
    ax.annotate(f"seed spread {hi - lo:.2f}\n({len(seeds)} seeds)",
                (2, q["objective_best"]), textcoords="offset points", xytext=(0, 34),
                ha="center", fontsize=7.5, color=MUTED)
    ax.set_ylabel("objective  (lower is better)", fontsize=8.5, color=INK_2)
    ax.set_title("(d)  Solution quality — the plan beats both baselines",
                 loc="left", fontsize=10.5, color=INK, pad=8)
    ax.tick_params(axis="x", labelsize=8)
    _style(ax)


def main() -> None:
    cycles = pd.read_csv(OUT_DIR / "harvest_by_cycle.csv")
    envelope = pd.read_csv(OUT_DIR / "attainable_envelope.csv")
    mix = pd.read_csv(OUT_DIR / "prescription_mix.csv")
    seeds = pd.read_csv(OUT_DIR / "seed_spread.csv")
    q = json.loads((OUT_DIR / "solution_quality.json").read_text())

    fig = plt.figure(figsize=(16.0, 10.8), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, hspace=0.50, wspace=0.30,
                          left=0.055, right=0.975, top=0.880, bottom=0.075)

    panel_plan(fig.add_subplot(gs[0, 0]), cycles, envelope)
    panel_attainability(fig.add_subplot(gs[0, 1]), envelope)
    ax_mix = fig.add_subplot(gs[1, 0])
    ax_mix.set_position([0.155, ax_mix.get_position().y0,
                         0.34, ax_mix.get_position().height])
    panel_mix(ax_mix, mix)
    panel_quality(fig.add_subplot(gs[1, 1]), q, seeds)

    fig.suptitle("ARTEMIS — the first simulated-annealing harvest plan, five-county Florida pilot",
                 x=0.055, ha="left", fontsize=15.5, color=INK, y=0.968)
    fig.text(0.055, 0.936,
             f"{q['stands']:,} stands ({q['stands_with_a_choice']:,} with more than one "
             f"trajectory) · {q['targets_unreachable_from_library']} of {q['targets_total']} "
             "(dimension × cycle) targets unreachable from the enumerated library",
             ha="left", fontsize=9.5, color=INK_2)
    fig.text(0.055, 0.912,
             "Riparian no-entry enforced structurally (6,602 stands, library "
             "{no_management}) · spatial penalties unavailable: stands are pixel classes, "
             "so there is no polygon adjacency",
             ha="left", fontsize=9.5, color=MUTED)

    out = OUT_DIR / "annealed_plan.png"
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
