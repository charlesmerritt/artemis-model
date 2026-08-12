"""
FVS management-regime templates (Phase 3.1).

These are the **prescription families** the trajectory libraries are built from: one
keyfile per ``(stand, prescription)`` pair, where a prescription is a family with its
parameters bound from the grid in ``config/prescriptions.yaml``. Which families a stand may
draw on is set by its ownership class; see `notes/trajectory-library-and-annealing.md`.
Library runs are **continuous — one uninterrupted FVS run per keyfile, no restart barrier**,
which is what keeps them independent, parallelizable, and free of the FFE carbon artifact
measured in `notes/restart-fidelity-findings.md`.

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
    - **thin_from_below_repeated** — that same thin repeated on an interval.
    - **selection_harvest** — a light proportional thin repeated on an interval.
    - **plantation_rotation** — a commercial thin, then a final clearcut at rotation age.

Stand-replacing harvests (`clearcut`, and the final cut of `plantation_rotation`) are
followed by a **regeneration record** inside an `Estab` packet — `Plant` for an artificial
replant, `Natural` for natural regeneration. Without one, a clearcut stand stays bare for
the remainder of the horizon, which understates volume and carbon and (under the
trajectory-library architecture) biases the scheduler against clearcut trajectories for a
reason that is a modelling artifact rather than silviculture.

Which species regenerate follows **Diaz et al. (2015)** (`docs/references/`): natural
regeneration is limited to the species already present in the stand, apportioned by each
species' share of stand SDI (``apportion_by_sdi``, from the caller's ``stand_sdi``).
Planting is treated separately in that same work — planted species is a management choice,
so `plantation_rotation` uses the configured commercial species regardless of what stood
there before.

**Every keyword layout and every number lives in `config/fvs_keywords.yaml`**, which is the
assumptions register: the parameter fields of each keyword we emit, where that layout was
verified, the silvicultural defaults, and an `open_questions` list naming the values that
are still placeholders. This module reads that file rather than keeping its own copies.

`ThinBBA`/shelterwood and the FFE/carbon block are still **not** emitted here; they need
their field layouts verified the same way first. The schedule/DataBase scaffolding mirrors
the verified keyfiles exactly.

Usage:
    from pipeline.s4_fvs.regime_templates import render_keyfile
    key = render_keyfile(stand_id="MU_123", stand_cn="MU_123",
                         regime="thin_from_below", params={"year": 2032, "max_dbh": 8, "proportion": 0.4})
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default projection schedule (matches config/projection.yaml: 50 yr, 5-yr cycles from 2022).
DEFAULT_INV_YEAR = 2022
DEFAULT_CYCLE_YEARS = 5
DEFAULT_NUM_CYCLE = 10

KEYWORD_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "fvs_keywords.yaml"


def load_keyword_config(path: Path | None = None) -> dict:
    """Load the FVS keyword register (`config/fvs_keywords.yaml`).

    That file is the single record of both halves of a keyfile's correctness: the parameter
    field layout of every keyword we emit, and the silvicultural numbers those keywords are
    filled with. Nothing in this module keeps a second copy — see the register's own header
    for why, and its `open_questions` list for which values are still placeholders.
    """
    with open(path or KEYWORD_CONFIG_PATH) as f:
        return yaml.safe_load(f)


KEYWORD_CONFIG = load_keyword_config()
KEYWORD_FIELDS: dict[str, list[str]] = KEYWORD_CONFIG["keyword_fields"]
REGIME_DEFAULTS: dict[str, dict] = KEYWORD_CONFIG["defaults"]["regimes"]
REGEN_DEFAULTS: dict = KEYWORD_CONFIG["defaults"]["regeneration"]

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
# Every value below comes from `config/fvs_keywords.yaml`; see that file for the basis of
# each number and for the ones still flagged as placeholders.


@dataclass(frozen=True)
class Regeneration:
    """One `Plant` or `Natural` record for an FVS `Estab` packet.

    ``natural=True`` renders `Natural` instead of `Plant`. Note the side effect, stated by
    esin.f itself (FORMAT 1230): **NATURAL implies STOCKADJ = 0.0, NOAUTALY and NOINGROW**,
    so choosing it also turns off automatic tallying and ingrowth for the stand.
    """
    year: int
    species: str = REGEN_DEFAULTS["fallback_species"]
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
    defaults = REGIME_DEFAULTS["thin_from_below"]
    return [ThinDBH(
        year=int(params["year"]),
        proportion=float(params.get("proportion", defaults["proportion"])),
        min_dbh=0.0,
        max_dbh=float(params.get("max_dbh", defaults["max_dbh"])),
    )]


def thin_from_below_repeated(params: dict) -> list[ThinDBH]:
    """A thin from below repeated every ``interval`` years across a window.

    The mechanical half of the thin-and-burn regime used on southern pine public land.
    Prescribed fire is not modelled — the FVS fire keywords are unverified here and the FFE
    state does not survive a restart barrier (`notes/restart-fidelity-findings.md`).
    """
    defaults = REGIME_DEFAULTS["thin_from_below_repeated"]
    start = int(params["start_year"])
    end = int(params.get("end_year", start + defaults["window_years"]))
    interval = int(params.get("interval", defaults["interval"]))
    proportion = float(params.get("proportion", defaults["proportion"]))
    max_dbh = float(params.get("max_dbh", defaults["max_dbh"]))
    return [
        ThinDBH(year=y, proportion=proportion, min_dbh=0.0, max_dbh=max_dbh)
        for y in range(start, end + 1, interval)
    ]


def selection_harvest(params: dict) -> list[ThinDBH]:
    """Light proportional thins every ``interval`` years across a window of years."""
    defaults = REGIME_DEFAULTS["selection_harvest"]
    start = int(params["start_year"])
    end = int(params.get("end_year", start + defaults["window_years"]))
    interval = int(params.get("interval", defaults["interval"]))
    proportion = float(params.get("proportion", defaults["proportion"]))
    return [ThinDBH(year=y, proportion=proportion) for y in range(start, end + 1, interval)]


def plantation_rotation(params: dict) -> list[ThinDBH]:
    """A commercial thin from below, then a final clearcut at rotation age."""
    defaults = REGIME_DEFAULTS["plantation_rotation"]
    return [
        ThinDBH(year=int(params["thin_year"]),
                proportion=float(params.get("thin_proportion", defaults["thin_proportion"])),
                max_dbh=float(params.get("thin_max_dbh", defaults["thin_max_dbh"]))),
        ThinDBH(year=int(params["clearcut_year"]), proportion=1.0),
    ]


REGIMES = {
    "no_management": no_management,
    "clearcut": clearcut,
    "thin_from_below": thin_from_below,
    "thin_from_below_repeated": thin_from_below_repeated,
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


def apportion_by_sdi(
    stand_sdi: Mapping[str, float],
    total_tpa: float,
    min_share: float | None = None,
) -> list[tuple[str, float]]:
    """Split ``total_tpa`` across a stand's species in proportion to their SDI share.

    This is the Diaz et al. (2015) natural-regeneration rule: regeneration is limited to
    the species **already present in the stand**, with density set by "the proportion of
    SDI occupied by each species in the stand" (report p. 27, `docs/references/`). It
    replaces picking one species for the whole landscape, which regenerated every clearcut
    as loblolly regardless of what stood there.

    ``stand_sdi`` maps an FVS species code to that species' stand density index — or to any
    non-negative quantity proportional to it, since only the shares matter. Species below
    ``min_share`` of the total are dropped and the remainder renormalised, so a trace
    species does not produce a record FVS would reject for near-zero TPA. Returns
    ``(species, trees_per_acre)`` pairs ordered by descending share.
    """
    if min_share is None:
        min_share = float(REGEN_DEFAULTS["min_species_share"])
    if total_tpa <= 0:
        raise ValueError(f"total_tpa must be > 0, got {total_tpa}")

    positive = {str(sp): float(v) for sp, v in stand_sdi.items() if float(v) > 0}
    total = sum(positive.values())
    if not positive:
        raise ValueError("stand_sdi has no species with positive SDI")

    kept = {sp: v for sp, v in positive.items() if v / total >= min_share}
    if not kept:
        # Every species is below the floor, which means the stand is very mixed rather
        # than empty. Keep the single largest instead of returning nothing.
        kept = {max(positive, key=positive.get): max(positive.values())}

    kept_total = sum(kept.values())
    pairs = [(sp, total_tpa * v / kept_total) for sp, v in kept.items()]
    return sorted(pairs, key=lambda pair: (-pair[1], pair[0]))


def _regen_after(harvest_year: int, params: dict, *, default_mode: str) -> list[Regeneration]:
    """Build the regeneration records following a stand-replacing harvest.

    Natural regeneration follows the stand's own composition (see ``apportion_by_sdi``) when
    the caller supplies ``stand_sdi``; planting uses the configured commercial species,
    because what gets planted is a management decision rather than a property of the stand
    that was cut — the same split Diaz et al. drew.
    """
    mode = str(params.get("regen", default_mode)).lower()
    if mode not in _REGEN_MODES:
        raise ValueError(f"regen must be one of {sorted(_REGEN_MODES)}, got {mode!r}")
    if mode == "none":
        return []

    natural = mode == "natural"
    year = harvest_year + int(params.get("regen_delay_years", REGEN_DEFAULTS["delay_years"]))
    default_tpa = REGEN_DEFAULTS["natural_tpa"] if natural else REGEN_DEFAULTS["plant_tpa"]
    total_tpa = float(params.get("regen_tpa", default_tpa))
    shared = {
        "natural": natural,
        "survival_pct": float(params.get("regen_survival_pct", REGEN_DEFAULTS["survival_pct"])),
        "age": float(params.get("regen_age", REGEN_DEFAULTS["age"])),
        "height_ft": float(params.get("regen_height_ft", REGEN_DEFAULTS["height_ft"])),
    }

    stand_sdi = params.get("stand_sdi")
    if natural and REGEN_DEFAULTS["natural_follows_stand_composition"] and stand_sdi:
        return [
            Regeneration(year=year, species=sp, trees_per_acre=tpa, **shared)
            for sp, tpa in apportion_by_sdi(stand_sdi, total_tpa)
        ]

    if natural and not stand_sdi:
        # Not silent: falling back means the Diaz composition rule was skipped for this
        # stand, and every such stand regenerates as one hard-coded species.
        logger.warning(
            "natural regeneration for year %d has no stand_sdi; falling back to a single "
            "%s record. Supply stand_sdi to follow the stand's own composition.",
            year, REGEN_DEFAULTS["fallback_species"],
        )

    species_default = (
        REGEN_DEFAULTS["fallback_species"] if natural else REGEN_DEFAULTS["plant_species"]
    )
    return [Regeneration(
        year=year,
        species=str(params.get("regen_species", species_default)),
        trees_per_acre=total_tpa,
        **shared,
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
    # A repeated thin is still a partial entry — residual stocking carries the stand, so
    # there is nothing to re-initialize from a fixed list.
    "thin_from_below_repeated": _no_regen,
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
    if any(not r.natural for r in regen) and REGEN_DEFAULTS["suppress_automatic_regeneration"]:
        # `Natural` already forces NOINGROW and NOAUTALY (esin.f FORMAT 1230). Stating them
        # for planted stands too keeps clearcut and plantation_rotation trajectories
        # comparable instead of differing by a side effect nobody chose, and is the
        # plain-FVS analogue of Diaz et al. turning the default regeneration process off.
        lines.append(_keyword_line("NoInGrow"))
        lines.append(_keyword_line("NoAutAly"))
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
    thins: list[ThinDBH] | None = None,
    regen: list[Regeneration] | None = None,
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
    """Render a complete single-stand FVS keyfile for the given regime.

    ``thins`` overrides the built-in builders, so a caller that assembled its operations
    elsewhere — `pipeline.s4_fvs.regime_library`, which reads them from
    `config/regimes.yaml` — can reuse this scaffolding without a Python builder per
    regime. When omitted, ``regime``/``params`` drive the built-in `REGIMES` table.

    A caller supplying its own ``thins`` also owns regeneration: the regime name is then
    a label from another library, so there is no `REGEN_BUILDERS` entry to consult and
    nothing is guessed. Pass ``regen`` to attach an `Estab` packet in that case.
    """
    params = params or {}
    if thins is None:
        thins = build_thins(regime, params)
        regen = build_regeneration(regime, params) if regen is None else regen
    blocks = [t.render() for t in thins]
    estab = render_estab_block(regen or [])
    if estab:
        blocks.append(estab)
    return _KEYFILE.format(
        stand_id=stand_id, stand_cn=stand_cn, regime=regime, mgmt_id=mgmt_id,
        schedule_block=render_schedule_block(inv_year, cycle_years, num_cycle),
        activity_block="\n".join(blocks), out_db=out_db, in_db=in_db,
        stand_table=stand_table, tree_table=tree_table, sp_label=sp_label,
    )
