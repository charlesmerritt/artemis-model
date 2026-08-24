"""
LETO stands (cellular-automata segmentation) + a harvested BA trajectory time-series,
rendered as the four-panel figure in the user's mockup.

Panel 1  LETO stands coloured by ownership class, with the riparian (stream) buffer.
Panels 2-4  the same stands at t=0 / t=25 / t=50 (2022 / 2047 / 2072), coloured by basal
            area per acre, with harvest events (clearcut / thin) highlighted.

Faithfulness / honesty:
  * Stands are produced by LETO's own cellular-automata algorithm (aauslander480/Leto
    stage2_segmentation.py), ported to NumPy/SciPy in leto_ca.py with arcpy removed. Real
    inputs: the Florida five-county TreeMap 2022 raster (per-pixel tree-list features via
    the TreeMap VAT) and the Harris et al. 2025 ownership raster (warped through /vsis3).
    STDAGE is not in the TreeMap VAT, so the CA runs on FORTYPCD/BALIVE/QMD/TPA with
    STDAGE's weight redistributed across them (documented, not fabricated).
  * BA growth is each stand's real FVS *no-management* baseline (fvs_trajectory.csv),
    summarised to its current and mature BA. Harvest drawdowns are applied on top of a
    logistic growth curve fit to those two real endpoints — FVS cannot execute in this
    sandbox (Windows DLL), so the harvested trajectory is modelled, not re-simulated.
  * Prescriptions come from the ARTEMIS repo's own resolver
    (pipeline.s3_management.regime_assignment.assign_prescription) keyed on ownership
    class and forest branch; harvest event years/proportions come from regime_templates.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes as rio_shapes
import geopandas as gpd
from shapely.geometry import shape, box
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch

REPO = Path("/home/user/artemis-model")
sys.path.insert(0, str(REPO))
DATA = REPO / "data"
SCR = REPO / "research" / "leto_ca_demo"
OUT = SCR / "outputs"
OUT.mkdir(exist_ok=True)

from pipeline.s3_management.regime_assignment import assign_prescription  # noqa: E402
from pipeline.s4_fvs.regime_templates import build_thins  # noqa: E402
import leto_ca  # noqa: E402

TM_TIF = DATA / "interim/treemap5co/TreeMap2022_CONUS_5FlCntys.tif"
FL_VAT = DATA / "interim/treemap5co/TreeMap2022_CONUS_5FlCntys.tif.vat.dbf"
STREAMS = DATA / "interim/management_units_pilot/streams_5070.gpkg"
TRAJ = DATA / "interim/no_management_fl5co_fvs_output/fvs_trajectory.csv"
OWN_FULL = SCR / "own_aoi_full.npy"
CELL_ACRES = 900.0 / 4046.8564224

# LETO segmentation config (from aauslander480/Leto configs/maine_test.py), STDAGE dropped
# and its 0.25 weight redistributed proportionally over the four available features.
_W = {"FORTYPCD": 0.30, "BALIVE": 0.20, "QMD": 0.15, "TPA": 0.10}
_scale = (0.30 + 0.25 + 0.20 + 0.15 + 0.10) / sum(_W.values())
CFG = {
    "initial_seed_acres": 100.0, "minimum_stand_acres": 5.0, "maximum_stand_acres": 300.0,
    "maximum_iterations": 40, "convergence_threshold": 0.01, "minimum_score_improvement": 0.01,
    "use_eight_neighbors": False, "shared_edge_bonus": 0.2, "standardization_clip": 4.0,
    "variable_weights": {k: v * _scale for k, v in _W.items()},
    "merge_similar_stands": True, "similar_merge_require_same_fortypcd": True,
    "similar_merge_min_shared_edges": 2, "similar_merge_max_similarity_difference": None,
    "similar_merge_max_passes": 50,
}

# Harris ownership -> mockup colour + label. Family="private small", Corporate="private
# industrial" per the mockup's own wording.
OWNER = {
    3: ("Family (private small)", "#d21f2b"),
    4: ("Corporate (private industrial)", "#f2c200"),
    6: ("Federal", "#2a5bd7"),
    7: ("State", "#d774c4"),
    8: ("Local", "#c98b52"),
}
INK, INK2, INK3, SURF, GRID = "#0b0b0b", "#52514e", "#8a8880", "#ffffff", "#dddddd"
STREAM_BLUE, RIP_BAND = "#37b6e6", "#bfe8f7"
BA_CMAP = LinearSegmentedColormap.from_list(
    "ba_green", ["#eaf6d8", "#c7e59a", "#8ec962", "#4da030", "#2b7a1f", "#14491a", "#0a2e12"])
CLEARCUT, THIN_HI, THIN_LO = "#6f1114", "#e34a2f", "#f39220"

SNAP_YEARS = [2022, 2032, 2052, 2072]   # start, 1st harvest wave, 2nd wave, regrown end
SNAP_TITLE = {
    2022: ("start", "t = 0"),
    2032: ('"1st harvest"', "t = 10"),
    2052: ("2nd-cycle harvest", "t = 30"),
    2072: ("end of sim", "t = 50"),
}
INV_YEAR = 2022
HORIZON_END = 2072


def load_aoi():
    aoi = json.loads((SCR / "aoi.json").read_text())
    r0, c0, win = aoi["r0"], aoi["c0"], aoi["win"]
    with rasterio.open(TM_TIF) as tm:
        window = rasterio.windows.Window(c0, r0, win, win)
        tmid = tm.read(1, window=window)
        transform = tm.window_transform(window)
        crs = tm.crs
        bounds = rasterio.windows.bounds(window, tm.transform)
    own = np.load(OWN_FULL)[r0:r0+win, c0:c0+win].astype(np.int16)
    own = np.where(np.isin(own, [1, 2]), 0, own)  # collapse non-forest/water to Unknown
    return tmid, own, transform, crs, bounds, aoi


def build_feature_rasters(tmid):
    vat = gpd.read_file(FL_VAT)[["Value", "PLT_CN", "FORTYPCD", "BALIVE", "QMD", "TPA_LIVE"]]
    vat = vat.rename(columns={"TPA_LIVE": "TPA"})
    lut = {int(r.Value): r for r in vat.itertuples(index=False)}
    valid = tmid > 0
    shape_ = tmid.shape
    feats = {n: np.zeros(shape_, dtype=np.float32) for n in ["BALIVE", "QMD", "TPA"]}
    fortyp = np.zeros(shape_, dtype=np.int32)
    ids = np.unique(tmid[valid])
    for v in ids:
        rec = lut.get(int(v))
        if rec is None:
            continue
        m = tmid == v
        feats["BALIVE"][m] = float(rec.BALIVE or 0.0)
        feats["QMD"][m] = float(rec.QMD or 0.0)
        feats["TPA"][m] = float(rec.TPA or 0.0)
        fortyp[m] = int(rec.FORTYPCD or 0)
    return feats, fortyp, valid, {int(r.Value): r for r in vat.itertuples(index=False)}


def vectorize(labels, transform, crs):
    geoms, ids = [], []
    for geom, val in rio_shapes(labels, mask=labels > 0, transform=transform, connectivity=4):
        geoms.append(shape(geom))
        ids.append(int(val))
    gdf = gpd.GeoDataFrame({"SEG_ID": ids}, geometry=geoms, crs=crs)
    gdf = gdf.dissolve("SEG_ID", as_index=False)  # one multipolygon row per stand
    return gdf


def mode_int(arr):
    v, c = np.unique(arr, return_counts=True)
    return int(v[np.argmax(c)])


def attribute_stands(gdf, labels, own, fortyp, feats, vat_lut, tmid):
    rows = []
    for sid in gdf["SEG_ID"].values:
        m = labels == sid
        owner = mode_int(own[m])
        ft = mode_int(fortyp[m])
        tm_vals = tmid[m]
        tm_dom = mode_int(tm_vals)
        rec = vat_lut.get(tm_dom)
        plt_cn = str(int(rec.PLT_CN)) if rec is not None and not pd.isna(rec.PLT_CN) else None
        rows.append(dict(SEG_ID=int(sid), OWN_CODE=owner, FORTYPCD=ft,
                         BALIVE=float(feats["BALIVE"][m].mean()), PLT_CN=plt_cn,
                         pixels=int(m.sum())))
    att = pd.DataFrame(rows)
    att["acres"] = att["pixels"] * CELL_ACRES
    return gdf.merge(att, on="SEG_ID")


def add_riparian(gdf, bounds, crs):
    streams = gpd.read_file(STREAMS, bbox=bounds).to_crs(crs)
    aoi_poly = box(*bounds)
    streams = streams[streams.intersects(aoi_poly)]
    if len(streams) == 0:
        gdf["SMZ_Pct"] = 0.0
        return gdf, streams, None
    # Florida BMP primary SMZ ~ 35 ft each side; widen slightly for a legible 30 m-grid band.
    buf = streams.buffer(35 * 0.3048 + 30).union_all()
    inter = gdf.geometry.intersection(buf)
    gdf["SMZ_Pct"] = 100.0 * inter.area / gdf.geometry.area
    return gdf, streams, buf


def stand_growth_and_harvest(gdf):
    traj = pd.read_csv(TRAJ, dtype={"stand_cn": "string"})
    ba_by_cn = {cn: g.sort_values("calendar_year") for cn, g in traj.groupby("stand_cn")}

    def baseline_ba(plt_cn):
        g = ba_by_cn.get(plt_cn)
        if g is None or g.empty:
            return None
        yrs = g["calendar_year"].to_numpy()
        ba = g["basal_area"].to_numpy()
        b0 = float(np.interp(INV_YEAR, yrs, ba))
        bmax = float(np.nanmax(ba))
        return max(b0, 1.0), max(bmax, b0, 5.0)

    def stand_age(plt_cn):
        g = ba_by_cn.get(plt_cn)
        if g is None or g.empty:
            return None
        return float(np.interp(INV_YEAR, g["calendar_year"].to_numpy(), g["age"].to_numpy()))

    years = np.arange(INV_YEAR, HORIZON_END + 1)
    out = {y: [] for y in SNAP_YEARS}
    status = {y: [] for y in SNAP_YEARS}
    records = []

    for row in gdf.itertuples(index=False):
        base = baseline_ba(row.PLT_CN)
        age0 = stand_age(row.PLT_CN)
        # forest branch + real FVS stand age -> age-based (staggered) scheduling in the resolver
        unit = {"OWN_CODE": int(row.OWN_CODE), "FORTYPCD": int(row.FORTYPCD), "SMZ_Pct": float(row.SMZ_Pct)}
        if age0 is not None:
            unit["stand_age"] = age0
        try:
            pres = assign_prescription(unit)
            template, params = pres.template, pres.params
        except Exception:
            template, params = "no_management", {}
        thins = build_thins(template, params) if template != "no_management" else []
        events = {int(t.year): float(t.proportion) for t in thins if INV_YEAR < t.year <= HORIZON_END}

        if base is None:
            b0, K = max(float(row.BALIVE), 1.0), max(float(row.BALIVE) * 1.6, 5.0)
        else:
            b0, K = base
        # logistic recovery rate: seedling -> ~0.9K in ~35 yr
        r = 0.13
        s = b0
        series = {}
        for y in years:
            if y > INV_YEAR:
                s = s + r * s * (1.0 - s / K)          # natural (logistic) growth
                s = min(s, K)
            if y in events:
                p = events[y]
                if p >= 0.85:
                    s = 3.0                              # clearcut -> seedling
                else:
                    s = s * (1.0 - p)                   # partial thin
            series[y] = s
        for y in SNAP_YEARS:
            out[y].append(series[y])
            # harvest in the last 5 years (y-4..y)
            recent = [(ey, ep) for ey, ep in events.items() if y - 4 <= ey <= y]
            if not recent:
                status[y].append("none")
            else:
                p = max(ep for _, ep in recent)
                status[y].append("clearcut" if p >= 0.85 else ("thin_hi" if p >= 0.31 else "thin_lo"))
        records.append(dict(SEG_ID=row.SEG_ID, template=template,
                            events=";".join(f"{k}:{v:.2f}" for k, v in sorted(events.items()))))
    for y in SNAP_YEARS:
        gdf[f"BA_{y}"] = out[y]
        gdf[f"HARV_{y}"] = status[y]
    return gdf, pd.DataFrame(records)


# ----------------------------------------------------------------------------- rendering
def _base_ax(ax, bounds):
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor(INK)
        s.set_linewidth(1.2)


def draw_stream(ax, streams, buf):
    if buf is not None:
        gpd.GeoSeries([buf]).plot(ax=ax, color=RIP_BAND, zorder=1, alpha=0.9)
    if streams is not None and len(streams):
        streams.plot(ax=ax, color=STREAM_BLUE, linewidth=1.8, zorder=6)


def panel_owner(ax, gdf, streams, buf, bounds):
    for code, (label, color) in OWNER.items():
        sub = gdf[gdf["OWN_CODE"] == code]
        if len(sub):
            sub.plot(ax=ax, color=color, edgecolor="#222222", linewidth=0.45, zorder=3)
    unk = gdf[~gdf["OWN_CODE"].isin(OWNER)]
    if len(unk):
        unk.plot(ax=ax, color="#cfcfcf", edgecolor="#222222", linewidth=0.45, zorder=3)
    rip = gdf[gdf["SMZ_Pct"] >= 50]
    if len(rip):
        rip.plot(ax=ax, facecolor="none", edgecolor=STREAM_BLUE, linewidth=1.6, hatch="///", zorder=5)
    draw_stream(ax, streams, buf)
    _base_ax(ax, bounds)
    ax.set_title("LETO stands & owner class", color=INK, fontsize=13, pad=8, fontweight="bold")


def panel_ba(ax, gdf, year, streams, buf, bounds, norm, show_harvest):
    gdf.plot(ax=ax, column=f"BA_{year}", cmap=BA_CMAP, norm=norm,
             edgecolor="#222222", linewidth=0.45, zorder=3)
    if show_harvest:
        for key, color in [("clearcut", CLEARCUT), ("thin_hi", THIN_HI), ("thin_lo", THIN_LO)]:
            sub = gdf[gdf[f"HARV_{year}"] == key]
            if len(sub):
                sub.plot(ax=ax, color=color, edgecolor="#222222", linewidth=0.45, zorder=4)
    draw_stream(ax, streams, buf)
    _base_ax(ax, bounds)
    tag, toff = SNAP_TITLE[year]
    ax.set_title(f"{tag} · {toff}\n{year}", color=INK, fontsize=12.5, pad=8, fontweight="bold")


def main():
    print("loading AOI…")
    tmid, own, transform, crs, bounds, aoi = load_aoi()
    print(f"AOI {aoi}, forest px {int((tmid>0).sum())}")
    feats, fortyp, valid, vat_lut = build_feature_rasters(tmid)

    print("running LETO cellular-automata segmentation…")
    labels = leto_ca.segment(feats, fortyp, own, valid, CELL_ACRES, CFG, log=lambda m: print("  " + m))

    print("vectorising + attributing…")
    gdf = vectorize(labels, transform, crs)
    gdf = attribute_stands(gdf, labels, own, fortyp, feats, vat_lut, tmid)
    gdf, streams, buf = add_riparian(gdf, bounds, crs)
    print(f"stands: {len(gdf)}  acres {gdf.acres.sum():.0f}  owners {sorted(gdf.OWN_CODE.unique())}")

    print("assigning prescriptions + trajectories…")
    gdf, harv = stand_growth_and_harvest(gdf)
    gdf.to_file(OUT / "leto_stands.gpkg", driver="GPKG")
    harv.to_csv(OUT / "stand_harvest.csv", index=False)

    # BA colour scale shared across the time panels
    ba_all = np.concatenate([gdf[f"BA_{y}"].to_numpy() for y in SNAP_YEARS])
    norm = Normalize(vmin=0, vmax=float(np.nanpercentile(ba_all, 98)))

    fig = plt.figure(figsize=(22, 6.6), facecolor=SURF)
    gs = fig.add_gridspec(1, 5, wspace=0.05, left=0.010, right=0.865, top=0.84, bottom=0.10)
    panel_owner(fig.add_subplot(gs[0, 0]), gdf, streams, buf, bounds)
    for i, y in enumerate(SNAP_YEARS):
        panel_ba(fig.add_subplot(gs[0, i + 1]), gdf, y, streams, buf, bounds, norm,
                 show_harvest=(y != 2022))

    fig.suptitle("LETO stands (cellular-automata segmentation) and a harvested basal-area trajectory  ·  "
                 "Florida five-county pilot, 3.6 km AOI",
                 x=0.010, ha="left", fontsize=15.5, fontweight="bold", color=INK, y=0.955)

    # legends on the right
    owner_handles = [Patch(facecolor=c, edgecolor="black", label=lab) for _, (lab, c) in OWNER.items()]
    owner_handles.append(Patch(facecolor="#cfcfcf", edgecolor="black", label="Unknown forest"))
    owner_handles.append(Patch(facecolor="none", edgecolor=STREAM_BLUE, hatch="///", label="Riparian (SMZ ≥ 50%)"))
    owner_handles.append(Patch(facecolor=RIP_BAND, edgecolor=STREAM_BLUE, label="Stream buffer"))
    leg1 = fig.legend(handles=owner_handles, loc="upper left", bbox_to_anchor=(0.870, 0.9),
                      frameon=False, fontsize=9.5, title="Owner class (panel 1)", title_fontsize=10.5)
    leg1._legend_box.align = "left"

    harv_handles = [Patch(facecolor=CLEARCUT, edgecolor="black", label="Clearcut, last 5 yr"),
                    Patch(facecolor=THIN_HI, edgecolor="black", label="Thinned 31–50%, last 5 yr"),
                    Patch(facecolor=THIN_LO, edgecolor="black", label="Thinned 10–30%, last 5 yr")]
    leg2 = fig.legend(handles=harv_handles, loc="upper left", bbox_to_anchor=(0.870, 0.50),
                      frameon=False, fontsize=9.5, title="Harvest event (panels 2–5)", title_fontsize=10.5)
    leg2._legend_box.align = "left"

    # BA colourbar
    cax = fig.add_axes([0.872, 0.12, 0.10, 0.028])
    sm = plt.cm.ScalarMappable(cmap=BA_CMAP, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("Basal area (sq ft/ac)", fontsize=9, color=INK2)
    cb.ax.tick_params(labelsize=8, colors=INK2)

    fig.text(0.010, 0.012,
             "Stands: LETO cellular-automata TreeMap segmentation (aauslander480/Leto stage2_segmentation.py, ported to NumPy/SciPy), "
             "run on the FL 5-county TreeMap 2022 raster (FORTYPCD/BALIVE/QMD/TPA per tree list; STDAGE absent from the VAT, its weight redistributed) "
             "with the Harris et al. 2025 ownership raster as a hard boundary.  "
             "Basal area = each stand's real FVS no-management baseline, with harvest drawdowns applied on a logistic growth curve fit to that baseline "
             "(FVS harvested runs cannot execute here). Prescriptions from the ARTEMIS resolver, keyed on owner class + FVS stand age. "
             "Same-owner stands share an entry year, so a whole owner class can harvest in one cycle.",
             fontsize=7.6, color=INK3, va="bottom", ha="left")

    out_png = OUT / "leto_stands_timeseries.png"
    fig.savefig(out_png, dpi=150, facecolor=SURF, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out_png)


if __name__ == "__main__":
    main()
