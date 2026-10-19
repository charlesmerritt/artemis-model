"""SVG/HTML fragments for the open-menu deck, in the 2026-10-05 deck's style."""
from __future__ import annotations

BG = "#f6f4ee"
INK = "#1f2a24"
MUTED = "#4a514c"
FAINT = "#6b706a"
GRID = "#d9d4c7"
AXIS = "#8a8780"
BAR = "#4f8a4a"
TARGET = "#d9601a"
TARGET_TXT = "#b04a12"
KICK = "#2f6b4f"
CLEARCUT = "#b3261e"
PARTIAL = "#f2a33a"
SANS = "'IBM Plex Sans', Arial, sans-serif"
SERIF = "'Source Serif 4', Georgia, serif"
YEARS = [2027 + 5 * i for i in range(10)]


def p(text, left, top, width, size=24, color=MUTED, align="left", weight=400, extra=""):
    return (f'<p style="position:absolute; top:{top:.0f}px; left:{left:.0f}px; width:{width:.0f}px; '
            f'font-family:{SANS}; font-size:{size}px; font-weight:{weight}; text-align:{align}; '
            f'color:{color}{extra}">{text}</p>')


def header(kicker, title, top=96):
    return (f'<div style="position:absolute; top:{top}px; left:128px; width:1664px; display:flex; '
            f'flex-direction:column; gap:12px">'
            f'<p style="font-family:{SANS}; font-size:24px; font-weight:600; letter-spacing:2px; color:{KICK}">{kicker}</p>'
            f'<h2 style="font-family:{SERIF}; font-size:56px; font-weight:600; line-height:1.1; color:{INK}">{title}</h2></div>')


def footer(text):
    return (f'<p style="position:absolute; bottom:64px; left:128px; width:1664px; font-family:{SANS}; '
            f'font-size:24px; color:{FAINT}">{text}</p>')


def bar_chart(planned, target, left, top, width, height, ymax, ticks, label_dev=True,
              tick_size=24, show_years=True, highlight=None, year_every=1, unit_scale=1e6,
              target_label=None):
    """Bars (planned) under a target line (scalar or per-cycle). Returns HTML fragments."""
    n = len(planned)
    slot = width / n
    bw = slot * 0.62
    def y(v):
        return height - height * v / ymax

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" aria-label="Planned harvest per period against target" '
             f'viewBox="0 0 {width:.0f} {height:.0f}" style="position:absolute; top:{top:.0f}px; left:{left:.0f}px; '
             f'width:{width:.0f}px; height:{height:.0f}px">']
    for t in ticks[1:]:
        parts.append(f'<line x1="0" y1="{y(t):.1f}" x2="{width:.0f}" y2="{y(t):.1f}" stroke="{GRID}" stroke-width="1"/>')
    for i, v in enumerate(planned):
        x = i * slot + (slot - bw) / 2
        h = max(height - y(v), 0)
        fill = BAR if highlight is None or highlight == i else "#c5d3bd"
        parts.append(f'<rect x="{x:.1f}" y="{y(v):.1f}" width="{bw:.1f}" height="{h:.1f}" rx="4" fill="{fill}"/>')
    tg = target if isinstance(target, (list, tuple)) else [target] * n
    pts = " ".join(f"{i * slot + slot / 2:.1f},{y(t):.1f}" for i, t in enumerate(tg))
    if len(set(round(t) for t in tg)) == 1:
        parts.append(f'<line x1="0" y1="{y(tg[0]):.1f}" x2="{width:.0f}" y2="{y(tg[0]):.1f}" stroke="{TARGET}" stroke-width="4"/>')
    else:
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{TARGET}" stroke-width="4"/>')
    parts.append(f'<line x1="0" y1="{height:.0f}" x2="{width:.0f}" y2="{height:.0f}" stroke="{AXIS}" stroke-width="2"/>')
    parts.append("</svg>")
    out = ["".join(parts)]
    for t in ticks:
        out.append(p(f"{t / unit_scale:,.0f}", left - 118, top + y(t) - 16, 100, tick_size, align="right"))
    if show_years:
        for i, yr in enumerate(YEARS[:n]):
            if i % year_every == 0:
                out.append(p(str(yr), left + i * slot, top + height + 12, slot, tick_size, align="center"))
    if label_dev:
        for i, (v, t) in enumerate(zip(planned, tg)):
            d = 100 * (v - t) / t
            out.append(p(f"{d:+.0f}%".replace("-", "−"), left + i * slot, top + y(v) + 14,
                         slot, tick_size, BG, "center", 600))
    if target_label:
        out.append(p(target_label, left + width - 520, top + y(tg[-1]) - 40, 520, 24, TARGET_TXT, "right", 600))
    return out


def dev_color(d):
    """Diverging fill for a deviation %: blue under, orange over, pale near zero."""
    a = min(abs(d) / 40, 1)
    lo, hi = (0xf6, 0xf4, 0xee), ((0x2b, 0x5c, 0x8a) if d < 0 else (0xd9, 0x60, 0x1a))
    c = [round(low + (high - low) * a) for low, high in zip(lo, hi)]
    return "#%02x%02x%02x" % tuple(c), (BG if a > 0.55 else INK)
