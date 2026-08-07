"""
FVS management-regime templates (Phase 3.1).

Renders per-stand FVS keyfiles for a small library of silvicultural regimes. Every harvest
is expressed with the **`ThinDBH` keyword** — the one management keyword already verified
against real FVS runs in this project (`research/restart_fidelity/make_cut_keyfiles.py`):

    ThinDBH   <year>   <min_dbh>   <max_dbh>   <proportion>   <species>

read in fixed 10-column fields, where ``proportion`` is the fraction of TPA removed across
the DBH window (min→max), species 0 = all. Building every regime from this one verified
keyword keeps the generated keyfiles trustworthy:

    - **no_management** — no cut (the baseline).
    - **clearcut** — remove all trees (proportion 1.0, DBH 0–999) at a target year.
    - **thin_from_below** — remove a proportion of the *small* trees (DBH 0–``max_dbh``).
    - **selection_harvest** — a light proportional thin repeated on an interval.
    - **plantation_rotation** — a commercial thin, then a final clearcut at rotation age.

Stand-replacing harvests (`clearcut`, and the final cut of `plantation_rotation`) are
followed by a **regeneration record** inside an `Estab` packet — `Plant` for an artificial
replant, `Natural` for natural regeneration. Without one, a clearcut stand stays bare for
the remainder of the horizon, which understates volume and carbon and (under the
trajectory-library architecture) biases the scheduler against clearcut trajectories for a
reason that is a modelling artifact rather than silviculture. See `_ESTAB_FIELDS` below for
the verified field layout and `REGEN_DEFAULTS` for the values still needing calibration.

`ThinBBA`/shelterwood and the FFE/carbon block are still **not** emitted here; they need
their field layouts verified the same way first. The schedule/DataBase scaffolding mirrors
the verified keyfiles exactly.

Usage:
    from pipeline.s4_fvs.regime_templates import render_keyfile
    key = render_keyfile(stand_id="MU_123", stand_cn="MU_123",
                         regime="thin_from_below", params={"year": 2032, "max_dbh": 8, "proportion": 0.4})
"""

from __future__ import annotations

from dataclasses import dataclass

# Default projection schedule (matches config/projection.yaml: 50 yr, 5-yr cycles from 2022).
DEFAULT_INV_YEAR = 2022
DEFAULT_CYCLE_YEARS = 5
DEFAULT_NUM_CYCLE = 10

# FVS reads keyword records in fixed 10-column fields: columns 1-10 hold the keyword,
# 11-20 field 1, 21-30 field 2, and so on. Getting a value into the wrong field is silent —
# FVS accepts the record and applies a different parameter — so every keyword rendered here
# goes through ``_keyword_line`` and every field position is pinned by a test.
FIELD_WIDTH = 10


def _keyword_line(keyword: str, *fields: str | int | None) -> str:
    """Render one fixed-format FVS keyword record.

    ``None`` leaves a field blank, which is how FVS is told to use its own default for that
    parameter — distinct from passing 0. Numeric values are pre-formatted by the caller so
    each field keeps the decimal precision FVS expects for it.
    """
    if len(keyword) > FIELD_WIDTH:
        raise ValueError(f"keyword {keyword!r} exceeds the {FIELD_WIDTH}-column keyword field")
    out = [f"{keyword:<{FIELD_WIDTH}}"]
    for value in fields:
        if value is None:
            out.append(" " * FIELD_WIDTH)
        elif isinstance(value, str):
            # Alpha fields (e.g. a species code) may sit anywhere in the field: SPDECD skips
            # leading blanks and reads up to three non-blank characters (base/spdecd.f).
            out.append(f"{value:>{FIELD_WIDTH}}")
        else:
            out.append(f"{value:>{FIELD_WIDTH}d}")
    return "".join(out)


def _f(value: float, decimals: int) -> str:
    """Format a numeric keyword field to a fixed number of decimals."""
    return f"{value:.{decimals}f}"


@dataclass(frozen=True)
class ThinDBH:
    """One proportional thin across a DBH window (FVS ThinDBH keyword)."""
    year: int
    proportion: float
    min_dbh: float = 0.0
    max_dbh: float = 999.0
    species: int = 0

    def render(self) -> str:
        if not (0.0 <= self.proportion <= 1.0):
            raise ValueError(f"proportion must be in [0, 1], got {self.proportion}")
        return _keyword_line(
            "ThinDBH",
            self.year,
            _f(self.min_dbh, 0),
            _f(self.max_dbh, 0),
            _f(self.proportion, 2),
            _f(self.species, 0),
        )


# Field layout of the `Plant` and `Natural` keywords, verified against the Open-FVS
# establishment-model keyword processor `estb/esin.f`:
#
#   option 2 -- PLANT    (IACTK=430)
#   option 3 -- NATURAL  (IACTK=431)  jumps to statement 1205, *inside* the PLANT handler
#                                     and before the date is read, so the two keywords take
#                                     an identical field layout.
#
#   field 1  date/cycle       ARRAY(1) -> IDT (defaults to 1 when blank)
#   field 2  species          SPDECD(2, ...) -- alpha code (<= 3 chars) or numeric index
#   field 3  trees per acre   ARRAY(3); must be > 0 or the record is rejected, ERRGRO(4)
#   field 4  percent survival ARRAY(4); FVS SILENTLY resets to 100.0 outside [0.001, 100]
#   field 5  age              ARRAY(5)
#   field 6  average height   ARRAY(6)
#   field 7  shade code       ARRAY(7) -- left blank here, FVS default
#
# Echoed back by esin.f FORMAT 1220 as DATE/CYCLE, SPECIES, TREES/ACRE, % SURVIVAL, AGE,
# AVE. HEIGHT, SHADE CODE, which is the confirmation that the order above is right.
_ESTAB_FIELDS = "date, species, trees_per_acre, survival_pct, age, height_ft, shade"

# Species codes are the SN (Southern) variant alpha codes from `sn/blkdat.f` DATA JSP:
# LP = loblolly pine (13), SA = slash pine (6), LL = longleaf (8), SP = shortleaf (5).
#
# POLICY DEFAULTS -- these are placeholders, not calibrated values, and the library
# generator should override them per stand rather than inherit them:
#   * `species`   should come from the unit's forest type. Diaz et al. (2015) limited
#                 regeneration to species already present in the stand; doing the same
#                 needs the unit's composite tree list, which the generator has and this
#                 template does not.
#   * `tpa`       605/ac is a conventional 8x9 ft plantation spacing for the Southeast.
#   * `survival`  100% is *FVS's own* default (esin.f resets out-of-range values to it).
#                 Operational southern pine plantings run nearer 80-90%; left at the FVS
#                 default rather than inventing a number.
REGEN_DEFAULTS = {
    "species": "LP",
    "plant_tpa": 605.0,
    "natural_tpa": 400.0,
    "survival_pct": 100.0,
    "age": 1.0,
    "height_ft": 0.5,
    "delay_years": 1,
}


@dataclass(frozen=True)
class Regeneration:
    """One `Plant` or `Natural` record for an FVS `Estab` packet.

    ``natural=True`` renders `Natural` instead of `Plant`. Note the side effect, stated by
    esin.f itself (FORMAT 1230): **NATURAL implies STOCKADJ = 0.0, NOAUTALY and NOINGROW**,
    so choosing it also turns off automatic tallying and ingrowth for the stand.
    """
    year: int
    species: str = REGEN_DEFAULTS["species"]
    trees_per_acre: float = REGEN_DEFAULTS["plant_tpa"]
    natural: bool = False
    survival_pct: float = REGEN_DEFAULTS["survival_pct"]
    age: float = REGEN_DEFAULTS["age"]
    height_ft: float = REGEN_DEFAULTS["height_ft"]

    def render(self) -> str:
        code = self.species.strip()
        if not 1 <= len(code) <= 3:
            # SPDECD reads at most three non-blank characters, so a longer code would be
            # silently truncated to a different species.
            raise ValueError(f"species code must be 1-3 characters, got {self.species!r}")
        if self.trees_per_acre <= 0:
            raise ValueError(f"trees_per_acre must be > 0, got {self.trees_per_acre}")
        if not (0.001 <= self.survival_pct <= 100.0):
            # FVS would silently substitute 100.0 here; fail loudly instead.
            raise ValueError(f"survival_pct must be in [0.001, 100], got {self.survival_pct}")
        return _keyword_line(
            "Natural" if self.natural else "Plant",
            self.year,
            code,
            _f(self.trees_per_acre, 0),
            _f(self.survival_pct, 2),
            _f(self.age, 1),
            _f(self.height_ft, 1),
        )


# ---- regime → list[ThinDBH] builders -------------------------------------------------

def no_management(params: dict) -> list[ThinDBH]:
    return []


def clearcut(params: dict) -> list[ThinDBH]:
    return [ThinDBH(year=int(params["year"]), proportion=1.0)]


def thin_from_below(params: dict) -> list[ThinDBH]:
    return [ThinDBH(
        year=int(params["year"]),
        proportion=float(params.get("proportion", 0.35)),
        min_dbh=0.0,
        max_dbh=float(params.get("max_dbh", 8.0)),
    )]


def selection_harvest(params: dict) -> list[ThinDBH]:
    """Light proportional thins every ``interval`` years across a window of years."""
    start = int(params["start_year"])
    end = int(params.get("end_year", start + 30))
    interval = int(params.get("interval", 10))
    proportion = float(params.get("proportion", 0.2))
    return [ThinDBH(year=y, proportion=proportion) for y in range(start, end + 1, interval)]


def plantation_rotation(params: dict) -> list[ThinDBH]:
    """A commercial thin from below, then a final clearcut at rotation age."""
    return [
        ThinDBH(year=int(params["thin_year"]), proportion=float(params.get("thin_proportion", 0.4)),
                max_dbh=float(params.get("thin_max_dbh", 8.0))),
        ThinDBH(year=int(params["clearcut_year"]), proportion=1.0),
    ]


REGIMES = {
    "no_management": no_management,
    "clearcut": clearcut,
    "thin_from_below": thin_from_below,
    "selection_harvest": selection_harvest,
    "plantation_rotation": plantation_rotation,
}


def build_thins(regime: str, params: dict) -> list[ThinDBH]:
    """Return the ordered ThinDBH operations for a regime."""
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}; choices: {sorted(REGIMES)}")
    return REGIMES[regime](params)


# ---- regime → list[Regeneration] builders --------------------------------------------
#
# Only stand-replacing harvests regenerate. `thin_from_below` and `selection_harvest`
# retain the overstory, so the residual stand is the seed source and FVS's own ingrowth
# handling applies — adding a Plant record to a thin would double-count.
#
# Default mode per regime: `plantation_rotation` replants (industrial intent, the whole
# point of the rotation), `clearcut` regenerates naturally. Override either with the
# `regen` param: "plant", "natural", or "none".

_REGEN_MODES = {"plant", "natural", "none"}


def _regen_after(harvest_year: int, params: dict, *, default_mode: str) -> list[Regeneration]:
    """Build the regeneration record following a stand-replacing harvest."""
    mode = str(params.get("regen", default_mode)).lower()
    if mode not in _REGEN_MODES:
        raise ValueError(f"regen must be one of {sorted(_REGEN_MODES)}, got {mode!r}")
    if mode == "none":
        return []
    natural = mode == "natural"
    default_tpa = REGEN_DEFAULTS["natural_tpa"] if natural else REGEN_DEFAULTS["plant_tpa"]
    return [Regeneration(
        year=harvest_year + int(params.get("regen_delay_years", REGEN_DEFAULTS["delay_years"])),
        species=str(params.get("regen_species", REGEN_DEFAULTS["species"])),
        trees_per_acre=float(params.get("regen_tpa", default_tpa)),
        natural=natural,
        survival_pct=float(params.get("regen_survival_pct", REGEN_DEFAULTS["survival_pct"])),
        age=float(params.get("regen_age", REGEN_DEFAULTS["age"])),
        height_ft=float(params.get("regen_height_ft", REGEN_DEFAULTS["height_ft"])),
    )]


def _no_regen(params: dict) -> list[Regeneration]:
    return []


def _clearcut_regen(params: dict) -> list[Regeneration]:
    return _regen_after(int(params["year"]), params, default_mode="natural")


def _plantation_regen(params: dict) -> list[Regeneration]:
    return _regen_after(int(params["clearcut_year"]), params, default_mode="plant")


REGEN_BUILDERS = {
    "no_management": _no_regen,
    "clearcut": _clearcut_regen,
    "thin_from_below": _no_regen,
    "selection_harvest": _no_regen,
    "plantation_rotation": _plantation_regen,
}


def build_regeneration(regime: str, params: dict) -> list[Regeneration]:
    """Return the regeneration records for a regime, ordered by year."""
    if regime not in REGEN_BUILDERS:
        raise ValueError(f"unknown regime {regime!r}; choices: {sorted(REGEN_BUILDERS)}")
    return sorted(REGEN_BUILDERS[regime](params), key=lambda r: r.year)


def render_estab_block(regen: list[Regeneration]) -> str:
    """Render the `Estab` … `End` packet holding the regeneration records.

    `Estab` field 1 is the *date of disturbance* (esin.f: ``IDSDAT=IFIX(ARRAY(1))``). We use
    the earliest regeneration year, which falls in the same FVS cycle as the harvest that
    triggered it at any sane planting delay. Returns "" when there is nothing to
    regenerate, so no empty packet is emitted.
    """
    if not regen:
        return ""
    lines = [_keyword_line("Estab", min(r.year for r in regen))]
    lines.extend(r.render() for r in regen)
    lines.append("End")
    return "\n".join(lines)


def render_schedule_block(inv_year: int, cycle_years: int, num_cycle: int) -> str:
    """Render the InvYear / TimeInt / NumCycle records.

    **`TimeInt` puts the cycle length in field 2, not field 1.** Field 1 is the *cycle
    number* the interval applies to, and leaving it blank applies the interval to every
    cycle. Writing the length into field 1 instead sets a cycle index and leaves the
    interval at the FVS default, so a 10-cycle run silently projects 100 years rather than
    50 — accepted without a warning, because both are valid records.

    Three independent confirmations in this repository:

    * `research/restart_fidelity/make_keyfiles.py` writes the interval in field 2, and its
      real FVS output (`.../outputs/arm_c_vs_a.txt`) reports cycle years
      1999/2004/2009/2014/2019 — four 5-year cycles.
    * `notes/fvs-smoke-rerun-plan.md` spells the same record out as ``TimeInt 0 5``:
      cycle 0 (= all cycles) in field 1, interval 5 in field 2. Blank and 0 are
      equivalent in field 1; the verified fixture leaves it blank, as we do.
    * `notes/treemap-fvs-workflow.md` records that FVS's default interval is 10 years,
      which is what a run gets when field 2 is left empty.
    """
    return "\n".join([
        _keyword_line("InvYear", inv_year),
        _keyword_line("TimeInt", None, cycle_years),
        _keyword_line("NumCycle", num_cycle),
    ])


_KEYFILE = """\
!!title: {stand_id}_{regime}
StdIdent
{stand_id}               {regime}
StandCN
{stand_cn}
MgmtId
{mgmt_id}
{schedule_block}
{activity_block}
DataBase
DSNOut
{out_db}
Summary        2
End

Database
DSNIn
{in_db}
StandSQL
SELECT * FROM {stand_table} WHERE Stand_CN = '%Stand_CN%'
EndSQL
TreeSQL
SELECT * FROM {tree_table} WHERE Stand_CN = '%Stand_CN%'
EndSQL
END
SPLabel
  {sp_label}
Process
Stop
"""


def render_keyfile(
    stand_id: str,
    stand_cn: str,
    regime: str,
    params: dict | None = None,
    *,
    mgmt_id: str = "A001",
    inv_year: int = DEFAULT_INV_YEAR,
    cycle_years: int = DEFAULT_CYCLE_YEARS,
    num_cycle: int = DEFAULT_NUM_CYCLE,
    out_db: str = "FVS_Out.db",
    in_db: str = "FVS_Data.db",
    stand_table: str = "FVS_StandInit_Plot",
    tree_table: str = "FVS_TreeInit_Plot",
    sp_label: str = "ARTEMIS",
) -> str:
    """Render a complete single-stand FVS keyfile for the given regime."""
    params = params or {}
    blocks = [t.render() for t in build_thins(regime, params)]
    estab = render_estab_block(build_regeneration(regime, params))
    if estab:
        blocks.append(estab)
    return _KEYFILE.format(
        stand_id=stand_id, stand_cn=stand_cn, regime=regime, mgmt_id=mgmt_id,
        schedule_block=render_schedule_block(inv_year, cycle_years, num_cycle),
        activity_block="\n".join(blocks), out_db=out_db, in_db=in_db,
        stand_table=stand_table, tree_table=tree_table, sp_label=sp_label,
    )
