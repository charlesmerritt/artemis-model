"""Write the open-menu deck's slide files from the 2026-10-08 permissive-menus outputs."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from charts import (AXIS, BAR, BG, CLEARCUT, FAINT, GRID, INK, KICK, MUTED, PARTIAL, SANS, SERIF,
                    TARGET, TARGET_TXT, YEARS, bar_chart, dev_color, footer, header, p)

EXP = Path(__file__).resolve().parents[1]
D = EXP / "outputs/deck"
ROOT = Path(__file__).resolve().parent
SL = ROOT / "project/slides"
SL.mkdir(parents=True, exist_ok=True)

DARK = "#1f2a24"
ALT = "#ebe7dc"
summary = json.loads((D / "summary.json").read_text())
frames = json.loads((D / "frames.json").read_text())["cycles"]
land = pd.read_csv(D / "landscape_by_cycle.csv")
viol = pd.read_csv(D / "deviation_by_cycle.csv")
plan = pd.read_csv(D / "plan.csv.gz")
events = pd.read_csv(D / "events.csv.gz")
cap = pd.read_csv(EXP / "outputs/horizon_capacity.csv")
cap = cap[cap["variant"] == "repeat_offsets_25"]
BASE = {"evenflow_cost": 10.512, "owner": 29.47, "county": 31.05, "landscape": -33.29}
BLOBS = dict(zip([2022 + 5 * k for k in range(11)], [
    "b647e1d94cadb5e653c8d902a3a07dba", "a36fe6c95276d5c0df864f42d3988883",
    "d82754570420c383a8d2e8d7974ee9ca", "8c5287033eb3c0b0c4217e6ddbabb70f",
    "168556599c6fbced7f1621ba03fb934d", "849871717d35eff72427c42af3caab7a",
    "f04a267f1d02c965f38828c52edac0ae", "198821b3f69fc486077a3272099911cf",
    "a2cea5c2b7729e74006df368a51e0673", "129593b12794e2b82ed2122d77086df2",
    "d398a5af49258c68893b0d56419b7f29"]))
NAME = {"Suwanee": "Suwannee"}
PRESC = {
    "pine_plantation_short_rotation": "Pine plantation, short rotation",
    "pine_plantation_long_rotation": "Pine plantation, long rotation",
    "natural_short_rotation": "Natural-regen short rotation",
    "hardwood_clearcut_regen": "Hardwood clearcut and regen",
    "family_light_thin": "Light thin",
    "family_uneven_aged_selection": "Uneven-aged selection",
    "public_selection_light": "Light selection",
    "public_thin_restore": "Thin and restore",
    "no_management": "Grow only",
}
FOOT = "FL5: 11,831 stands, 925k ac · TPO target: 2013–2024 reports · plan: best of 22 annealing searches"
written = []


def section(sid, body, bg=BG, notes=""):
    html = (f'<section id="{sid}" data-transition="fade" style="background:{bg}; color:{INK}; '
            f'font-family:{SANS}">\n' + "\n".join(body) + f"\n<aside>{notes}</aside>\n</section>\n")
    (SL / f"{sid}.html").write_text(html)
    written.append(sid)


def M(v):
    return f"{v / 1e6:,.0f}"


def pct(v, digits=0):
    return f"{v:+.{digits}f}%".replace("-", "−")


# ---------------------------------------------------------------- cover
section("cover", [
    f'<div style="position:absolute; left:128px; top:128px; width:1664px; display:flex; flex-direction:column; gap:24px">'
    f'<p style="font-size:24px; font-weight:600; letter-spacing:2px; color:#9cc47a">ARTEMIS · FL5 HARVEST SCHEDULING</p>'
    f'<h1 style="font-family:{SERIF}; font-size:120px; font-weight:600; line-height:1.05; color:#f6f4ee">Every owner,<br>every regime</h1>'
    f'<p style="font-size:40px; line-height:1.3; color:#c9d3c4; width:1300px">What simulated annealing does with the five-county pilot when any owner class may thin, clearcut, or run a short or long rotation.</p></div>',
    f'<div style="position:absolute; left:128px; top:690px; width:1664px; display:flex; flex-direction:row; gap:64px">'
    + "".join(
        f'<div style="flex:1; display:flex; flex-direction:column; gap:8px; padding:0 0 0 32px; border-left:2px solid #4f8a4a">'
        f'<p style="font-family:{SERIF}; font-size:72px; font-weight:600; line-height:1; color:#f6f4ee">{a}</p>'
        f'<p style="font-size:24px; line-height:1.35; color:#c9d3c4">{b}</p></div>'
        for a, b in [(pct(summary["landscape_mean_dev_pct"]), "mean five-county miss against TPO, down from −33% under owner-specific menus"),
                     (f'{summary["owner_mean_abs_dev_pct"]:.1f}%', "mean |miss| per owner group and period (80 targets in all)"),
                     ("10 periods", "of cuts, stepped through stand by stand, 2027 to 2072")])
    + "</div>",
    f'<p style="position:absolute; left:128px; bottom:64px; width:1664px; font-size:24px; color:#9aa39b">Five-county north Florida pilot: Baker, Columbia, Hamilton, Suwannee, Union · run 2026-10-12</p>',
], bg=DARK, notes=(
    "This deck asks one question: what happens to the harvest schedule if we stop assigning management by owner class and let every owner do everything? "
    "Every owner class may now take every cutting prescription we have defined: thin, select, clearcut, short pine rotation, long pine rotation, natural-regeneration rotation. "
    "Forest type still matters: a pine plantation can only go on a pine stand, a hardwood clearcut only on hardwood or mixed. The ground does not change, only what each owner is allowed to do with it. "
    "Simulated annealing then picks one trajectory per stand to match TPO removals by county and by owner group in every five-year period. "
    "Headline: the five-county harvest comes in 8% under target on average, against 33% under with the owner-specific menus from the last deck."))

# ---------------------------------------------------------------- the menu
owners = ["Family", "Corporate", "Federal", "State", "Local"]
cols = [("Light thin", "any"), ("Thin and restore", "any"), ("Uneven-aged selection", "any"), ("Light selection", "any"),
        ("Pine short rotation", "pine"), ("Pine long rotation", "pine"), ("Natural-regen rotation", "any"), ("Hardwood clearcut", "hardwood, other")]
before = {"Family": {0, 2, 5}, "Corporate": {4, 5, 7}, "Federal": {3, 1}, "State": {3, 1, 5}, "Local": {3, 1}}
x0, w0, cw, top, rh = 128, 260, 175, 330, 76
body = [header("THE OPEN MENU", "Every owner class may take every regime")]
for j, (c, ft) in enumerate(cols):
    body.append(p(c, x0 + w0 + j * cw, top - 92, cw - 12, 24, INK, "center", 600))
    body.append(p(ft, x0 + w0 + j * cw, top + 5 * rh + 14, cw - 12, 24, MUTED, "center"))
for i, o in enumerate(owners):
    y = top + i * rh
    body.append(f'<div style="position:absolute; left:{x0}px; top:{y}px; width:{w0 + 8 * cw}px; height:{rh}px; '
                f'background:{"#ebe7dc" if i % 2 == 0 else BG}"></div>')
    body.append(p(o, x0 + 16, y + 20, w0 - 16, 28 if False else 24, INK, "left", 600))
    for j in range(8):
        was = j in before[o]
        fill = BAR if was else "#9cc47a"
        body.append(f'<div style="position:absolute; left:{x0 + w0 + j * cw + (cw - 12) / 2 - 16:.0f}px; top:{y + 22}px; '
                    f'width:32px; height:32px; border-radius:50%; background:{fill}"></div>')
body.append(p("forest type →", x0 + 16, top + 5 * rh + 14, w0 - 16, 24, MUTED, "left"))
legend_y = top + 5 * rh + 80
body.append(f'<div style="position:absolute; left:{x0}px; top:{legend_y}px; width:1664px; display:flex; flex-direction:row; gap:48px; align-items:center">'
            f'<div style="width:28px; height:28px; border-radius:50%; background:{BAR}"></div><p style="font-size:24px; color:{INK}">on the owner-specific menu (last deck)</p>'
            f'<div style="width:28px; height:28px; border-radius:50%; background:#9cc47a"></div><p style="font-size:24px; color:{INK}">newly open to this owner</p>'
            f'<p style="font-size:24px; color:{MUTED}">Grow only stays on every menu; riparian stays grow only.</p></div>')
body.append(footer("config/management_regimes.yaml · forest type still filters: no pine plantation on a hardwood stand"))
section("menu", body, notes=(
    "Here is the change. Rows are owner classes, columns are the eight cutting prescriptions. Dark dots are what each class could do in the last deck, two or three options each. Light dots are what this run opens up. Every class now has all eight. "
    "So family forest can clearcut and replant pine on a short rotation, corporate land can do a light thin or a selection cut, and federal and state land can run either rotation. "
    "Forest type is the one thing still held fixed, along the bottom: the two pine plantation rotations only go on pine stands, the hardwood clearcut only on hardwood or mixed stands, the rest on anything. The stands, their tree lists and their volumes are identical to the last deck. "
    "Two more things are switched on. Short rotations now repeat inside the 50 years: a stand clearcut in 2030 can be thinned and clearcut again before 2072, replanted each time. And every regime can start 0, 5, 10, 15, 20 or 25 years late, the Diaz et al. timing offsets, out to the 25 years the design calls for. "
    "Riparian buffers are untouched: grow only, never cut."))

# ---------------------------------------------------------------- library
body = [header("THE TRAJECTORY LIBRARY", "Four times the choices per stand")]
cards = [("4.5 → 17.8", "options per stand, on average (up to 43)"),
         ("12.5k → 25.8k", "distinct FVS runs, one per plot × regime × offset"),
         ("0–25 yr", "timing offsets in five-year steps, up from 0–15"),
         ("2–3 rotations", "a short rotation repeats inside 50 years, replanted after each clearcut")]
body.append('<div style="position:absolute; left:128px; top:330px; width:1664px; display:grid; grid-template-columns:repeat(2, 1fr); gap:40px">' + "".join(
    f'<div style="display:flex; flex-direction:column; gap:12px; padding:40px; background:#ebe7dc; border-radius:16px">'
    f'<p style="font-family:{SERIF}; font-size:72px; font-weight:600; line-height:1; color:{INK}">{a}</p>'
    f'<p style="font-size:32px; line-height:1.3; color:{MUTED}">{b}</p></div>' for a, b in cards) + "</div>")
body.append(footer("Every run is FVSsn on the 676 FIA donor plots behind the 11,831 stands; 1 of 32,850 runs failed and is dropped"))
section("library", body, notes=(
    "Opening the menus is cheap in FVS terms, because prescriptions are shared: a plot runs a regime once, however many owners might choose it. "
    "The library goes from 4.5 options per stand to 17.8, and from about 12,500 distinct FVS runs to about 25,800. The runs take about 20 minutes on 11 cores. "
    "Two ingredients besides the open menus: offsets now go out to 25 years, and rotations repeat. The repeat matters more than it sounds. In the last deck each keyfile cut a stand at most once in the 50 years. Here a 25-year pine rotation clearcuts, replants through FVS's establishment model, and can be thinned and cut again within the horizon. "
    "One FVS run out of 32,850 failed; that single option is dropped and every stand keeps a full menu."))

# ---------------------------------------------------------------- schedule
planned = land["volume_cuft"].tolist()
tgt = summary["target_cuft_per_cycle"]
body = [header("SIMULATED ANNEALING HARVEST SCHEDULE", "Within 10% of TPO in every period")]
body.append(p("M ft³ per 5-year period", 128, 262, 400))
body += bar_chart(planned, tgt, 248, 300, 1544, 560, 450e6, [0, 100e6, 200e6, 300e6, 400e6],
                  target_label=f"TPO target {tgt / 1e6:.1f} M ft³")
body.append(f'<div style="position:absolute; top:110px; left:1292px; width:500px; display:flex; flex-direction:column; gap:4px; '
            f'padding:16px 0 16px 24px; background:{BG}; border-left:4px solid {BAR}">'
            f'<p style="font-family:{SERIF}; font-size:72px; font-weight:600; line-height:1; color:{INK}">−{(tgt * 10 - sum(planned)) / 10 / 1e6:.0f} M ft³</p>'
            f'<p style="font-size:24px; line-height:1.35; color:{INK}">short per period on average ({pct(summary["landscape_mean_dev_pct"])}), against −129 M (−33%) under owner menus</p></div>')
body.append(footer(FOOT))
section("schedule", body, notes=(
    f"The schedule annealing chose, as bars under the TPO line. Planned removals run from {min(planned) / 1e6:.0f} to {max(planned) / 1e6:.0f} million cubic feet per five-year period against a target of 387.5. "
    f"Every period is within 10% of target, and the miss closes over time: about −10% through the 2040s, −2% by 2072. Over 50 years that is {sum(planned) / 1e9:.2f} billion cubic feet against 3.88 billion. "
    "For comparison, the owner-specific menus on the same landscape, same targets and same search came in 33% short on average, 129 million cubic feet a period. "
    f"Search: 22 independent annealing runs at cooling factor 0.98, from a greedy seed. The best objective is {summary['objective_best']:.3f}; the worst of the 22 is {summary['objective_worst_seed']:.3f}. "
    "The best plan is the same one the 11-seed sweep found on October 8th, so doubling the restarts did not improve it. "
    "The shortfall that is left is concentrated in two places, Suwannee county and the Other public owner group; the next slides show where."))

# ---------------------------------------------------------------- small multiples helper


def multiples(sid, kicker, title, dim, keys, ncol, notes, ymax_round=50e6):
    body = [header(kicker, title)]
    v = viol[viol["dimension"] == dim]
    n = len(keys)
    gapx = 72
    pw = (1664 - 120 - gapx * (ncol - 1)) / ncol
    nrow = -(-n // ncol)
    ph = 210 if nrow > 1 else 400
    for i, k in enumerate(keys):
        r, c = divmod(i, ncol)
        left = 128 + 120 + c * (pw + gapx)
        top = 360 + r * (ph + 150)
        s = v[v["key"] == k].sort_values("cycle")
        vals, tg = s["volume_cuft"].tolist(), s["target_cuft"].tolist()
        ym = max(max(vals), max(tg)) * 1.18
        step = ymax_round
        while ym / step > 4:
            step *= 2
        while ym / step < 2:
            step /= 2
        ticks = [0] + [step * t for t in range(1, int(ym // step) + 1)]
        ym = ticks[-1] * 1.15
        mean = s["deviation_pct"].mean()
        body.append(p(f'<b>{NAME.get(k, k)}</b> · target {tg[0] / 1e6:.0f} M ft³ · mean {pct(mean)}',
                      left - 120, top - 64, pw + 120, 24, INK))
        body += bar_chart(vals, tg, left, top, pw, ph, ym, ticks, label_dev=False,
                          year_every=3 if pw < 600 else 1)
    body.append(footer(FOOT))
    section(sid, body, notes=notes)


og = viol[viol["dimension"] == "owner_group"].groupby("key")["deviation_pct"].mean()
multiples("owners", "BY OWNER GROUP", "Private and federal track the target; other public falls short",
          "owner_group", ["Private", "Federal (NF)", "Other public"], 3, notes=(
              "The annealer scores each TPO owner group separately, because owner classes harvest consistently in aggregate. Bars are planned removals per period, the orange line is that group's TPO target. "
              f"Federal, essentially Osceola National Forest, is on target in every period (mean {pct(og['Federal (NF)'], 1)}). Private, which carries 92% of the target, averages {pct(og['Private'], 1)}, closing from about −10% early to −2% by 2072. "
              f"Other public, state and local land, averages {pct(og['Other public'], 1)}. That group's target is 22 million cubic feet a period from about 34,000 acres, and even with every stand on its highest-volume option it can supply only 96% of ten periods of target, so the early periods cannot be filled."))
cd = viol[viol["dimension"] == "county"].groupby("key")["deviation_pct"].mean()
multiples("counties", "BY COUNTY", "Four counties on target; Suwannee cannot supply its share",
          "county", ["Baker", "Columbia", "Hamilton", "Suwanee", "Union"], 3, notes=(
              "The same plan by county. Baker, Columbia, Hamilton and Union are within 2% of their targets in every period, slightly over in fact, which is the annealer leaning on them to make up some of the landscape shortfall. "
              f"Suwannee is the miss: {pct(cd['Suwanee'])} on average, about −37% through the 2040s, closing to −9% by 2072. Suwannee has the largest TPO target of the five, 112 million cubic feet a period, and the smallest forest supply relative to it. "
              "That is the whole story of the remaining error: the county and owner-group cells that miss are Suwannee and Other public, and both are supply limits, as the error slides show."), ymax_round=25e6)

# ---------------------------------------------------------------- total error
body = [header("TOTAL ERROR", "Opening the menus cuts the even-flow error by 88%")]
rows = [("Even-flow cost", "Σ squared relative miss, 80 targets", f"{BASE['evenflow_cost']:.2f}", f"{summary['evenflow_cost']:.2f}"),
        ("Owner group miss", "mean |%| per group and period", f"{BASE['owner']:.1f}%", f"{summary['owner_mean_abs_dev_pct']:.1f}%"),
        ("County miss", "mean |%| per county and period", f"{BASE['county']:.1f}%", f"{summary['county_mean_abs_dev_pct']:.1f}%"),
        ("Five-county total", "mean signed miss per period", pct(BASE['landscape'], 1), pct(summary['landscape_mean_dev_pct'], 1)),
        ("Worst single cell", "largest |%| of the 80", "—", f"{summary['worst_cell_abs_dev_pct']:.0f}%")]
body.append(f'<div style="position:absolute; left:128px; top:300px; width:1664px; display:flex; flex-direction:column">'
            f'<div style="display:flex; flex-direction:row; padding:0 0 12px 0; border-bottom:2px solid {AXIS}">'
            f'<p style="width:820px; font-size:24px; font-weight:600; color:{MUTED}">Measure</p>'
            f'<p style="width:420px; font-size:24px; font-weight:600; text-align:right; color:{MUTED}">Owner-specific menus</p>'
            f'<p style="width:424px; font-size:24px; font-weight:600; text-align:right; color:{KICK}">Every owner, every regime</p></div>'
            + "".join(
                f'<div style="display:flex; flex-direction:row; align-items:center; padding:18px 0; border-bottom:1px solid {GRID}">'
                f'<div style="width:820px; display:flex; flex-direction:column; gap:2px"><p style="font-size:32px; font-weight:600; color:{INK}">{a}</p>'
                f'<p style="font-size:24px; color:{MUTED}">{b}</p></div>'
                f'<p style="width:420px; font-family:{SERIF}; font-size:48px; text-align:right; color:{MUTED}">{c}</p>'
                f'<p style="width:424px; font-family:{SERIF}; font-size:48px; font-weight:600; text-align:right; color:{INK}">{d}</p></div>'
                for a, b, c, d in rows) + "</div>")
body.append(footer("Same landscape, TPO targets, objective weights and search for both columns; only the menu differs · 2026-10-08 sweep and this run"))
section("error", body, notes=(
    "Total error, three ways, against the owner-specific menus from the last deck. Both columns use the same 11,831 stands, the same TPO targets, the same objective and the same annealer; only the menus differ, so every gap is the menu. "
    f"The even-flow cost is what the annealer actually minimises: the sum, over 5 counties and 3 owner groups in each of 10 periods, of the squared relative miss. It falls from 10.5 to {summary['evenflow_cost']:.2f}. "
    f"In plain percentages, the average owner group misses its target by {summary['owner_mean_abs_dev_pct']:.1f}% in a period, down from 29.5%, and the average county by {summary['county_mean_abs_dev_pct']:.1f}%, down from 31%. "
    f"The five-county total averages {pct(summary['landscape_mean_dev_pct'], 1)}. The worst single cell is Suwannee in the 2040s at −37%. "
    "Where the reduction came from, from the October 8 sweep: opening every regime to every owner alone takes the cost from 10.5 to 5.6; letting rotations repeat inside the horizon takes it to 1.4; offsets out to 25 years take it to 1.28. Ignoring forest type entirely would only reach 1.23, so keeping forest type costs almost nothing."))

# ---------------------------------------------------------------- error grid
keys = [("county", k) for k in ["Baker", "Columbia", "Hamilton", "Suwanee", "Union"]] + \
       [("owner_group", k) for k in ["Private", "Federal (NF)", "Other public"]]
body = [header("ERROR BY TARGET AND PERIOD", "Two of the 80 targets hold nearly all the error")]
gx, gy, lw, cwid, rhh = 128, 300, 300, 136, 64
for j, yr in enumerate(YEARS):
    body.append(p(str(yr), gx + lw + j * cwid, gy, cwid, 24, MUTED, "center", 600))
for i, (dim, k) in enumerate(keys):
    y = gy + 48 + i * rhh + (24 if dim == "owner_group" else 0)
    body.append(p(NAME.get(k, k), gx, y + 16, lw - 16, 24, INK, "left", 600))
    s = viol[(viol["dimension"] == dim) & (viol["key"] == k)].sort_values("cycle")
    for j, d in enumerate(s["deviation_pct"]):
        fill, txt = dev_color(d)
        body.append(f'<p style="position:absolute; left:{gx + lw + j * cwid}px; top:{y}px; width:{cwid - 4}px; height:{rhh - 4}px; '
                    f'background:{fill}; font-size:24px; line-height:{(rhh - 4) / 24:.2f}; text-align:center; color:{txt}">{pct(d)}</p>')
body.append(p("COUNTIES", gx, gy + 4, lw, 24, KICK, "left", 600, "; letter-spacing:2px"))
body.append(p("OWNER GROUPS", gx, gy + 48 + 5 * rhh + 2, lw, 24, KICK, "left", 600, "; letter-spacing:2px"))
body.append(p("Blue: under target · orange: over · paler is closer", gx + lw, gy + 48 + 8 * rhh + 48, 1200, 24, MUTED))
body.append(footer(FOOT))
section("error-grid", body, notes=(
    "Every one of the 80 targets the annealer scores, as a grid: rows are the five counties and the three owner groups, columns are the ten periods, each cell the planned harvest's percentage miss. "
    "Blue is under target, orange over, and the colour fades to the background near zero. "
    "Nearly the whole grid is within ±2%. The blue is two rows: Suwannee, −36% to −37% through 2047 and closing to −9%, and Other public, −16% to −19% through 2052 and closing to −2%. Private runs −10% early to −2% late: the same shortfall seen from the owner side, since private land carries 92% of the target. "
    "The pale orange on Baker, Columbia, Hamilton and Union is about +1%: the annealer slightly overcutting where it can, because the five-county total is short."))

# ---------------------------------------------------------------- capacity
body = [header("WHY THE REST IS MISSED", "What is left is timber, not menu")]
cap_rows = cap.assign(name=cap["key"].map(lambda k: NAME.get(k, k)))
order = ["Suwanee", "Other public", "Private", "Hamilton", "Columbia", "Union", "Baker", "Federal (NF)"]
cap_rows = cap_rows.set_index("key").loc[order]
bx, bw, by, bh = 128 + 330, 1100, 320, 62
xmax = 300
body.append(f'<svg xmlns="http://www.w3.org/2000/svg" aria-label="Most each target could supply over 50 years, as % of ten periods of target" '
            f'viewBox="0 0 {bw} {bh * len(order)}" style="position:absolute; left:{bx}px; top:{by}px; width:{bw}px; height:{bh * len(order)}px">'
            + "".join(f'<rect x="0" y="{i * bh + 12}" width="{min(r.max_pct_of_target, xmax) / xmax * bw:.1f}" height="{bh - 24}" rx="4" '
                      f'fill="{"#2b5c8a" if r.max_pct_of_target < 100 else "#c5d3bd"}"/>'
                      for i, r in enumerate(cap_rows.itertuples()))
            + f'<line x1="{100 / xmax * bw:.1f}" y1="0" x2="{100 / xmax * bw:.1f}" y2="{bh * len(order)}" stroke="{TARGET}" stroke-width="4"/></svg>')
for i, r in enumerate(cap_rows.itertuples()):
    body.append(p(r.name, 128, by + i * bh + 14, 310, 24, INK, "right", 600))
    v = r.max_pct_of_target
    body.append(p(f"{v:.0f}%" + ("+" if v > xmax else ""), bx + min(v, xmax) / xmax * bw + 12 if v <= xmax - 30 else bx + bw - 130,
                  by + i * bh + 14, 120, 24, INK if v <= xmax - 30 else BG, "left", 600))
body.append(p("100% = ten periods of TPO target", bx + 100 / xmax * bw + 12, by + bh * len(order) + 12, 600, 24, TARGET_TXT, "left", 600))
body.append(f'<div style="position:absolute; left:1300px; top:330px; width:492px; display:flex; flex-direction:column; gap:16px; padding:24px 0 24px 32px; background:{BG}; border-left:4px solid #2b5c8a">'
            f'<p style="font-family:{SERIF}; font-size:48px; font-weight:600; line-height:1.1; color:{INK}">Suwannee: 76% at most</p>'
            f'<p style="font-size:24px; line-height:1.4; color:{INK}">Even with every stand on its highest-volume option, the county cannot reach its 50-year TPO total. No schedule from this library can.</p></div>')
body.append(footer("Upper bound: every stand takes its largest 50-year removal, independently · horizon_capacity.py"))
section("capacity", body, notes=(
    "Is the remaining miss the search, or the forest? This is a hard upper bound: for each target, put every stand on whichever option removes the most over 50 years, ignoring even flow entirely. If that cannot reach ten periods of target, no schedule can. "
    "Suwannee tops out at 76% of its 50-year TPO total. The plan delivers 70%. Other public tops out at 96% and the plan delivers 86%, and the plan cannot hit early periods because so much of that capacity is in stands that only become harvestable later. "
    "Every other target has room to spare: Federal could supply seven times its target, which is why the annealer uses it so lightly. "
    "So the residual error is a supply statement about this library on this landscape. Part of it may be real, Suwannee's TPO removals may draw on timber from outside the county or from stands the TreeMap classes underrepresent, and part is the pilot's limits, since these stands are pixel classes on the published TreeMap rather than LETO units on the improved rasters."))

# ---------------------------------------------------------------- what owners chose
groups = [("Short rotation", ["pine_plantation_short_rotation", "natural_short_rotation"], "#1e4d2b"),
          ("Long rotation", ["pine_plantation_long_rotation", "hardwood_clearcut_regen"], "#4f8a4a"),
          ("Thinning", ["family_light_thin", "public_thin_restore"], "#9cc47a"),
          ("Selection", ["family_uneven_aged_selection", "public_selection_light"], "#f2a33a"),
          ("Grow only", ["no_management"], "#d9d4c7")]
oc = [("private_family", "Family"), ("private_industrial", "Corporate"), ("federal", "Federal"), ("state", "State"), ("local", "Local")]
mix = plan.groupby(["owner_class", "base_prescription"])["acres"].sum()
body = [header("WHAT THE SCHEDULE CHOSE", "Every owner class uses the whole menu")]
bx, bw, by, bh = 128 + 280, 1100, 340, 84
for i, (code, label) in enumerate(oc):
    tot = mix[code].sum()
    x = 0.0
    segs = []
    for g, members, col in groups:
        a = sum(mix[code].get(m, 0.0) for m in members)
        segs.append((g, a / tot, col))
    body.append(p(f"{label}", 128, by + i * bh + 14, 260, 24, INK, "right", 600))
    body.append(p(f"{tot / 1000:,.0f}k ac", 128, by + i * bh + 42, 260, 24, MUTED, "right"))
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" aria-label="{label} acres by regime family" viewBox="0 0 {bw} {bh - 20}" '
           f'style="position:absolute; left:{bx}px; top:{by + i * bh + 10}px; width:{bw}px; height:{bh - 20}px">']
    for g, f, col in segs:
        svg.append(f'<rect x="{x * bw:.1f}" y="0" width="{f * bw:.1f}" height="{bh - 20}" fill="{col}" stroke="{BG}" stroke-width="2"/>')
        x += f
    svg.append("</svg>")
    body.append("".join(svg))
    x = 0.0
    for g, f, col in segs:
        if f >= 0.07:
            body.append(p(f"{100 * f:.0f}%", bx + x * bw, by + i * bh + 28, f * bw, 24,
                          BG if col in ("#1e4d2b", "#4f8a4a") else INK, "center", 600))
        x += f
body.append(f'<div style="position:absolute; left:{bx}px; top:{by + 5 * bh + 24}px; width:1300px; display:flex; flex-direction:row; gap:32px; flex-wrap:wrap">'
            + "".join(f'<div style="display:flex; flex-direction:row; gap:10px; align-items:center"><div style="width:28px; height:28px; border-radius:4px; background:{c}"></div>'
                      f'<p style="font-size:24px; color:{INK}">{g}</p></div>' for g, _, c in groups) + "</div>")
body.append(footer("Share of each owner class's acres in the chosen plan · short: pine 25 yr, natural 30 yr · long: pine 35 yr, hardwood clearcut"))
section("choices", body, notes=(
    "What the annealer actually did with the freedom. Each bar is one owner class's acres in the chosen plan, split by regime family. "
    "Short rotations are the 25-year pine plantation and the 30-year natural-regeneration clearcut; long rotations are the 35-year pine plantation and the hardwood clearcut. Thinning is the light thin and thin-and-restore; selection the two selection regimes. "
    "Every class uses every family. Family forest, the largest class, is mostly on rotations, because it has to supply most of the private target. Federal land leans on thinning and selection: its target is small, so it can meet it with light entries. "
    "Grow only stays on every menu and the annealer uses it, about 45,000 acres including the 11,000 riparian acres that can take nothing else, where cutting would overshoot a target. "
    "Read this with care: the annealer picks whatever regime matches TPO, not what a family landowner would plausibly do. This is the answer to 'what would it take', not a forecast of owner behaviour."))

# ---------------------------------------------------------------- cut-by-cut
ev = events.copy()
for k, fr in enumerate(frames):
    year = fr["year"]
    sid = f"cut-{year}"
    imgs = [f'<img src="/_blob/{BLOBS[year]}" alt="FL5 standing volume under the open-menu plan in {year}'
            + (", clearcuts and partial cuts this period" if k else ", before any cut") +
            '" style="position:absolute; left:96px; top:90px; width:1309px; height:900px; object-fit:contain">']
    side = [f'<p style="font-size:24px; font-weight:600; letter-spacing:2px; color:{KICK}">CUT BY CUT · {k} OF 10</p>']
    if k == 0:
        side.append(f'<h2 style="font-family:{SERIF}; font-size:48px; font-weight:600; line-height:1.15; color:{INK}">2022: the forest before any cut</h2>')
    else:
        side.append(f'<h2 style="font-family:{SERIF}; font-size:48px; font-weight:600; line-height:1.15; color:{INK}">{year - 4}–{year}</h2>')
    side.append(f'<div style="display:flex; flex-direction:column; gap:12px">'
                f'<div style="display:flex; flex-direction:row; gap:12px; align-items:center"><div style="width:28px; height:28px; flex:none; background:{CLEARCUT}; border-radius:4px"></div><p style="font-size:24px; color:{INK}">Clearcut, then replanted</p></div>'
                f'<div style="display:flex; flex-direction:row; gap:12px; align-items:center"><div style="width:28px; height:28px; flex:none; background:{PARTIAL}; border-radius:4px"></div><p style="font-size:24px; color:{INK}">Thin or selection cut</p></div>'
                f'<div style="display:flex; flex-direction:column; gap:6px"><div style="width:355px; height:20px; background:linear-gradient(to right, #e3ecca, #9cc47a, #4f8a4a, #1e4d2b); border-radius:4px"></div>'
                f'<div style="display:flex; flex-direction:row; justify-content:space-between"><p style="font-size:24px; color:{MUTED}">0</p><p style="font-size:24px; color:{MUTED}">5,000+ ft³/ac</p></div></div></div>')
    if k == 0:
        side.append(f'<p style="font-size:24px; line-height:1.35; color:{INK}">Mean standing volume 1,509 ft³/ac. Each click is the next five years.</p>')
        notes = ("The cut-by-cut step-through starts here: the five counties in 2022, shaded by standing merchantable volume per acre, from the same FVS runs the schedule uses. "
                 "Over the next ten slides each period's cuts are painted on: dark red for clearcuts, which FVS replants in the same keyfile, and amber for thins and selection cuts. Green is what stands between cuts. "
                 "A caveat on reading the maps: the plan's stands are pixel classes, TreeMap plot by county by owner, not contiguous management units, so one decision paints scattered pixels across a county. Riparian buffers are masked and never shown cut.")
    else:
        e = ev[ev["cycle"] == k]
        cc = e[e["cut"] == "clearcut"].drop_duplicates("stand_id")["acres"].sum()
        pc = e[e["cut"] == "partial"].drop_duplicates("stand_id")["acres"].sum()
        top3 = (e.groupby("base_prescription")["acres"].sum().sort_values(ascending=False).head(3))
        side.append(f'<div style="display:flex; flex-direction:column; gap:4px; padding:0 0 0 20px; border-left:4px solid {BAR}">'
                    f'<p style="font-family:{SERIF}; font-size:48px; font-weight:600; line-height:1.1; color:{INK}">{fr["planned_cuft"] / 1e6:.0f} M ft³</p>'
                    f'<p style="font-size:24px; color:{INK}">cut, {pct(fr["deviation_pct"], 1)} vs TPO {tgt / 1e6:.0f} M</p></div>')
        side.append(f'<p style="font-size:24px; line-height:1.35; color:{INK}"><b>{cc / 1000:.0f}k ac</b> clearcut · <b>{pc / 1000:.0f}k ac</b> thinned or selected</p>')
        side.append(f'<p style="font-size:24px; line-height:1.35; color:{MUTED}">Most acres: {", ".join(PRESC[x].lower() for x in top3.index[:2])}</p>')
        # period strip
        sw = 355
        slot = sw / 10
        ym = 450e6
        strip = [f'<svg xmlns="http://www.w3.org/2000/svg" aria-label="Planned harvest by period, this period highlighted" viewBox="0 0 {sw} 120" style="width:{sw}px; height:120px">']
        for j, v in enumerate(planned):
            h = 120 * v / ym
            strip.append(f'<rect x="{j * slot + 3:.1f}" y="{120 - h:.1f}" width="{slot - 6:.1f}" height="{h:.1f}" rx="2" fill="{BAR if j == k - 1 else "#c5d3bd"}"/>')
        ty = 120 - 120 * tgt / ym
        strip.append(f'<line x1="0" y1="{ty:.1f}" x2="{sw}" y2="{ty:.1f}" stroke="{TARGET}" stroke-width="3"/></svg>')
        side.append("".join(strip))
        notes = (f"Period {k}, {year - 4} to {year}. The plan removes {fr['planned_cuft'] / 1e6:.0f} million cubic feet against the 387.5 million target, {pct(fr['deviation_pct'], 1)}. "
                 f"About {cc / 1000:.0f} thousand acres are clearcut and {pc / 1000:.0f} thousand thinned or selection cut. "
                 f"By acres the busiest regimes this period are {', '.join(f'{PRESC[x].lower()} ({a / 1000:.0f}k ac)' for x, a in top3.items())}. "
                 f"Mean standing volume across the plan's pixels is {fr['mean_standing_cuft_ac']:,.0f} cubic feet per acre. "
                 "Clearcuts are read from the operations rendered into each stand's FVS keyfile, not inferred from volume; the volume removed is FVS's.")
    section(sid, imgs + [f'<div style="position:absolute; left:1469px; top:90px; width:355px; height:900px; display:flex; flex-direction:column; gap:24px">'
                         + "".join(side) + "</div>"], notes=notes)

# ---------------------------------------------------------------- takeaways
body = [header("WHAT THIS SAYS", "Flexibility closes most of the gap; Suwannee is a supply question")]
items = [("Open menus fix most of the miss.", "Letting every owner thin, clearcut and run either rotation takes the five-county shortfall from −33% to −8% and the even-flow cost from 10.5 to 1.28."),
         ("Repeat rotations matter as much as the menu.", "A stand cut once in 50 years cannot sustain TPO; replanting and cutting again inside the horizon is the other half."),
         ("Forest type costs almost nothing.", "Dropping the forest-type rule entirely only improves the cost from 1.28 to 1.23. Keeping it is right."),
         ("The residual is supply.", "Suwannee can reach at most 76% of its TPO total and Other public 96%, whatever the schedule."),
         ("Next.", "Rerun on LETO units over the improved rasters, and check Suwannee's supply against FIA EVALID.")]
body.append('<div style="position:absolute; left:128px; top:320px; width:1664px; display:flex; flex-direction:column; gap:28px">' + "".join(
    f'<div style="display:flex; flex-direction:row; gap:32px; align-items:baseline"><p style="width:600px; flex:none; font-size:32px; font-weight:600; line-height:1.3; color:{INK}">{a}</p>'
    f'<p style="flex:1; font-size:28px; line-height:1.4; color:{MUTED}">{b}</p></div>' for a, b in items) + "</div>")
body.append(footer(FOOT))
section("takeaways", body, notes=(
    "Four things to take away. First, the owner-specific menus were the main reason the last schedule ran a third short: with every owner allowed every regime, the same landscape gets to within 8% of TPO on average. "
    "Second, repeating rotations inside the 50 years is as important as opening the menus; a keyfile that cuts a stand once cannot sustain a pine landscape's harvest. "
    "Third, keeping forest type fixed costs almost nothing, so the open menu does not need to put pine plantations on hardwood sites to work. "
    "Fourth, what remains is Suwannee and other public land, and both are limited by timber, not by the schedule. "
    "Caveats: these are pixel-class stands on the published TreeMap, not LETO units on the improved rasters; the minimum harvestable percentage is not yet enforced; and the annealer is choosing what matches TPO, not modelling what owners would choose. "
    "Next: the same run on the LETO units, and an FIA EVALID comparison to test whether Suwannee's TPO removals are supportable from its own forest."))

order = written
deck = {"v": 4, "createdOnFiles": {"v": 1, "at": "2026-10-12T04:00:00Z"}, "lists": "css",
        "title": "ARTEMIS · FL5 open menus", "cover": "cover", "order": order,
        "sections": {
            "s1": {"description": "Every owner class may take every regime; forest type still filters.", "start": "cover"},
            "s2": {"description": "The annealed harvest schedule against TPO, landscape, owner groups and counties.", "start": "schedule"},
            "s3": {"description": "Total error, where it sits, and why what remains is supply.", "start": "error"},
            "s4": {"description": "The plan cut by cut, 2022 to 2072.", "start": "cut-2022"},
            "s5": {"description": "What this says and what comes next.", "start": "takeaways"}},
        "faces": {"ibm-plex-sans": {"family": "IBM Plex Sans", "href": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap"},
                  "source-serif-4": {"family": "Source Serif 4", "href": "https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400;600&display=swap"}},
        "designSystems": []}
(ROOT / "project/deck.json").write_text(json.dumps(deck, indent=2))
print(order)
for s in order:
    t = (SL / f"{s}.html").read_text()
    print(s, len(t), t.count("<") // 2)
