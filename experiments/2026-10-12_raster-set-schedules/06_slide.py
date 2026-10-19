"""Step 6 — the one slide this experiment adds to the 2026-10-08 open-menu deck.

Reads `outputs/comparison.csv`, `outputs/suwannee.csv` and `outputs/fia_by_county.csv`, and
writes `experiments/2026-10-08_permissive-menus/deck/project/slides/raster-sets.html` in that
deck's style, placed after `capacity` in its `deck.json`.

    uv run python experiments/2026-10-12_raster-set-schedules/06_slide.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import raster_sets as RS  # noqa: E402

DECK = REPO / "experiments/2026-10-08_permissive-menus/deck/project"
SLIDE_ID = "raster-sets"
AFTER = "capacity"

BG, INK, GREEN, BLUE, ORANGE, MUTED, BAND = ("#f6f4ee", "#1f2a24", "#2f6b4f", "#2b5c8a",
                                             "#b04a12", "#6b706a", "#ebe7dc")
SANS = "'IBM Plex Sans', Arial, sans-serif"
SERIF = "'Source Serif 4', Georgia, serif"


def k(acres: float) -> str:
    return f"{acres / 1000:,.0f}k"


def pct(x: float, signed: bool = False, digits: int = 0) -> str:
    text = f"{x:+.{digits}f}%" if signed else f"{x:.{digits}f}%"
    return text.replace("-", "−")


WIDTHS = (25, 10, 12, 16, 12, 11, 14)          # % of the table; every first-row cell carries one
HEADS = ("Landscape", "Forest acres", "Young added", "Suwannee forest (% of FIA)",
         "5-county vs TPO", "Even-flow cost", "Suwannee vs TPO: plan / most")


def cell(text: str, *, head: bool = False, align: str = "right", color: str = INK,
         width: int | None = None) -> str:
    """One cell in the deck's table subset: plain text, colour and alignment only."""
    tag = "th" if head else "td"
    style = f"padding:12px 16px; text-align:{align}; color:{MUTED if head else color}"
    if width is not None:
        style += f"; width:{width}%"
    if head:
        style += "; font-weight:600"
    return f'<{tag} style="{style}">{text}</{tag}>'


def table(cmp: pd.DataFrame) -> str:
    rows = ["<tr>" + "".join(cell(h, head=True, align="left" if i == 0 else "right", width=w)
                             for i, (h, w) in enumerate(zip(HEADS, WIDTHS))) + "</tr>"]
    for r in cmp.itertuples(index=False):
        base = r.raster_set == "published"
        shade = f' style="background:{BAND}"' if base else ""
        rows.append(f"<tr{shade}>" + "".join([
            cell(r.label, align="left"),
            cell(k(r.forest_acres)),
            cell("—" if r.added_back_young_acres == 0 else k(r.added_back_young_acres)),
            cell(f"{k(r.suwannee_acres)} ({pct(r.suwannee_pct_of_fia)})"),
            cell(pct(r.landscape_mean_dev_pct, signed=True, digits=1), color=INK if base else BLUE),
            cell(f"{r.evenflow_cost:.2f}", color=INK if base else BLUE),
            cell(f"{pct(r.suwannee_plan_pct_of_tpo)} / {pct(r.suwannee_max_pct_of_tpo)}",
                 color=ORANGE),
        ]) + "</tr>")
    return ('<table style="position:absolute; top:268px; left:128px; width:1664px; '
            f'font-family:{SANS}; font-size:28px; color:{INK}">' + "".join(rows) + "</table>")


def build(title: str, callout: str, notes: str) -> str:
    cmp = pd.read_csv(RS.OUTPUTS / "comparison.csv")
    fia = pd.read_csv(RS.OUTPUTS / "fia_by_county.csv").set_index("county")
    cmp["suwannee_pct_of_fia"] = 100 * cmp["suwannee_acres"] / fia.at["Suwannee", "forest_area_ac"]
    return "\n".join([
        f'<section id="{SLIDE_ID}" data-transition="fade" style="background:{BG}; color:{INK}; '
        f'font-family:{SANS}">',
        f'<div style="position:absolute; top:96px; left:128px; width:1664px; display:flex; '
        f'flex-direction:column; gap:12px"><p style="font-family:{SANS}; font-size:24px; '
        f'font-weight:600; letter-spacing:2px; color:{GREEN}">THE REPAIRED RASTERS</p>'
        f'<h2 style="font-family:{SERIF}; font-size:56px; font-weight:600; line-height:1.1; '
        f'color:{INK}">{title}</h2></div>',
        table(cmp),
        f'<div style="position:absolute; left:128px; top:760px; width:1664px; padding:20px 0 20px 32px; '
        f'border-left:4px solid {ORANGE}"><p style="font-size:28px; line-height:1.4; color:{INK}">'
        f'{callout}</p></div>',
        f'<p style="position:absolute; bottom:64px; left:128px; width:1664px; font-family:{SANS}; '
        f'font-size:24px; color:{MUTED}">Same menu, FVS, annealer and seeds as the deck · best of 22 '
        f'searches · FIA EVALIDator 2022</p>',
        f"<aside>{notes}</aside>",
        "</section>",
    ])


def place_in_deck() -> None:
    path = DECK / "deck.json"
    deck = json.loads(path.read_text())
    if SLIDE_ID not in deck["order"]:
        deck["order"].insert(deck["order"].index(AFTER) + 1, SLIDE_ID)
        path.write_text(json.dumps(deck, indent=2) + "\n")


if __name__ == "__main__":
    text = json.loads((EXP / "slide_text.json").read_text())
    html = build(text["title"], text["callout"], text["notes"])
    (DECK / "slides" / f"{SLIDE_ID}.html").write_text(html + "\n")
    place_in_deck()
    print(html)
