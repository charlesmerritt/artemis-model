"""Harvest-schedule policies for the visualization experiment.

Three policies, selectable in 04_fvs_run.py via --policy. All of them are
stand-ins for the simulated-annealing trajectory scheduler that will
ultimately select harvest schedules to optimize an even-flow target across a
region (county or sub-unit); each produces a per-stand event list the
annealer would instead search over.

  default    The deterministic owner-class default prescriptions from
             config/management_regimes.yaml (the repo's current assignment).
             Known weakness this experiment exposed: offset-based public
             prescriptions treat every stand of a class in the same cycle
             years — the whole federal forest thins at once.

  random     Purely random harvests per stand: at each 5-year cycle boundary
             an eligible stand harvests with probability P_HARVEST; an event
             locks the stand out for MIN_REENTRY_YEARS (10), so no stand is
             harvested more than once in any 10-year window. Events are 30%
             clearcut (followed by regeneration) / 70% proportional thin with
             a random intensity. Seeded per stand — reproducible, and
             uncorrelated across stands, so harvests scatter in space and
             time instead of pulsing.

  heuristic  Three rules (stated on the figure itself):
               1. Small family private and unknown forest — no entry, like
                  riparian.
               2. Industrial — one thin from below at stand age 10 (age from
                  the FIA STDAGE of the stand's donor condition, re-
                  established at 0 by each clearcut), and a clearcut whenever
                  live BA exceeds 100 sq ft/acre. The BA trigger is evaluated
                  against actual FVSsn output, iteratively: run, find the
                  first cycle year the projected BA crosses 100, schedule the
                  clearcut + replant + next age-10 thin, rerun — until no new
                  trigger fires inside the horizon.
               3. Everything else (federal, state, local) — random clearcuts
                  and thinnings, same generator and 10-year lockout as the
                  random policy.
             Riparian stays absolute no-entry under every policy.

Events are rendered with the repo's verified keyword layouts
(pipeline/s4_fvs/regime_templates: ThinDBH, and Estab/Plant/Natural for the
post-clearcut regeneration — planted loblolly on pine types, natural
regeneration apportioned over the stand's own species by SDI share
otherwise).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CYCLE_YEARS, HORIZON_YEARS, INV_YEAR, SEED  # noqa: E402
from pipeline.s4_fvs.regime_templates import (  # noqa: E402
    Regeneration,
    ThinDBH,
    apportion_by_sdi,
)

CYCLE_YEARS_LIST = list(range(INV_YEAR + CYCLE_YEARS, INV_YEAR + HORIZON_YEARS + 1,
                              CYCLE_YEARS))

# -- random policy parameters -----------------------------------------------
P_HARVEST = 0.25            # chance an eligible stand harvests at a cycle year
P_CLEARCUT = 0.30           # of harvests, how many are clearcuts
THIN_PROPORTION_RANGE = (0.15, 0.45)
MIN_REENTRY_YEARS = 10      # no stand harvested more than once per 10 years

# -- heuristic policy parameters --------------------------------------------
INDUSTRIAL_THIN_AGE = 10
INDUSTRIAL_THIN_PROPORTION = 0.40    # thin-from-below, config's industrial thin
INDUSTRIAL_THIN_MAX_DBH = 8.0
INDUSTRIAL_BA_TRIGGER = 100.0        # clearcut whenever BA exceeds this
REGEN_DELAY_YEARS = 1
NATURAL_REGEN_TPA = 400.0

PINE_FORTYP = (140, 179)

HEURISTIC_RULES_TEXT = (
    "Heuristic rules — stand-ins for the simulated-annealing scheduler that will select "
    "per-stand schedules against a regional even-flow target:\n"
    "1)  Small family private & unknown forest — no entry, like riparian.      "
    "2)  Industrial — one thin from below at stand age 10 (age from FIA STDAGE, "
    "re-established at 0 by each clearcut), and a clearcut whenever\n"
    "projected live BA exceeds 100 sq ft/ac, evaluated iteratively against FVSsn output.      "
    "3)  Everything else — random clearcuts and thinnings, never re-entering "
    "a stand within 10 years."
)


@dataclass
class Schedule:
    """A stand's harvest events, renderable straight into a keyfile."""
    thins: list = field(default_factory=list)          # list[ThinDBH]
    regen: list = field(default_factory=list)          # list[Regeneration]
    clearcut_years: list = field(default_factory=list)

    def event_years(self) -> list[int]:
        return sorted(t.year for t in self.thins)


def _snap_up(year: float) -> int:
    """Snap a year up to the next cycle boundary, never before the first cycle."""
    k = int(np.ceil((year - INV_YEAR) / CYCLE_YEARS))
    return INV_YEAR + max(1, k) * CYCLE_YEARS


def _regen_records(year_cut: int, fortypcd: int, stand_sdi: dict | None) -> list:
    """Post-clearcut regeneration: plant loblolly on pine types, natural
    (SDI-apportioned over the stand's own species) otherwise."""
    year = year_cut + REGEN_DELAY_YEARS
    if PINE_FORTYP[0] <= int(fortypcd) <= PINE_FORTYP[1] or not stand_sdi:
        return [Regeneration(year=year)]     # Plant, config species/TPA defaults
    return [Regeneration(year=year, species=sp, trees_per_acre=tpa, natural=True)
            for sp, tpa in apportion_by_sdi(stand_sdi, NATURAL_REGEN_TPA)]


def stand_rng(policy: str, mu_id: str) -> np.random.Generator:
    """A reproducible per-stand RNG, uncorrelated across stands and policies.

    zlib.crc32, not hash(): Python salts string hashes per process, which
    would silently change every "random" schedule between runs.
    """
    import zlib

    return np.random.default_rng([SEED, zlib.crc32(policy.encode()), int(mu_id)])


def random_schedule(mu_id: str, fortypcd: int, stand_sdi: dict | None,
                    policy_tag: str = "random") -> Schedule:
    """Random events at cycle years with the 10-year re-entry lockout."""
    rng = stand_rng(policy_tag, mu_id)
    sched = Schedule()
    last_event: int | None = None
    for year in CYCLE_YEARS_LIST:
        if last_event is not None and year - last_event < MIN_REENTRY_YEARS:
            continue
        if rng.random() >= P_HARVEST:
            continue
        last_event = year
        if rng.random() < P_CLEARCUT:
            sched.thins.append(ThinDBH(year=year, proportion=1.0))
            sched.clearcut_years.append(year)
            sched.regen.extend(_regen_records(year, fortypcd, stand_sdi))
        else:
            proportion = round(float(rng.uniform(*THIN_PROPORTION_RANGE)), 2)
            sched.thins.append(ThinDBH(year=year, proportion=proportion))
    return sched


def industrial_initial_schedule(stand_age: float) -> Schedule:
    """The industrial rule's pass-1 events: only the first-rotation age-10
    thin (if the stand is still younger than 10). The BA-triggered clearcuts
    are added iteratively from actual FVS output by
    industrial_next_clearcut()."""
    sched = Schedule()
    if 0 <= stand_age < INDUSTRIAL_THIN_AGE:
        year = _snap_up(INV_YEAR + (INDUSTRIAL_THIN_AGE - stand_age))
        if year <= INV_YEAR + HORIZON_YEARS:
            sched.thins.append(ThinDBH(year=year,
                                       proportion=INDUSTRIAL_THIN_PROPORTION,
                                       max_dbh=INDUSTRIAL_THIN_MAX_DBH))
    return sched


def industrial_next_clearcut(sched: Schedule, ba_by_year: dict[int, float],
                             fortypcd: int, stand_sdi: dict | None) -> bool:
    """Apply the BA>100 trigger to a projected trajectory; extend the schedule.

    `ba_by_year` is the post-treatment BA at each cycle year from the latest
    FVSsn run of this schedule. Finds the first cycle year past the previous
    clearcut's regeneration where BA exceeds the trigger, schedules the
    clearcut there with its replant and the next rotation's age-10 thin, and
    returns True. Returns False when no year triggers (schedule is final).
    """
    horizon_end = INV_YEAR + HORIZON_YEARS
    floor = (sched.clearcut_years[-1] + REGEN_DELAY_YEARS
             if sched.clearcut_years else INV_YEAR)
    for year in CYCLE_YEARS_LIST:
        if year <= floor or year > horizon_end:
            continue
        ba = ba_by_year.get(year)
        if ba is None or ba <= INDUSTRIAL_BA_TRIGGER:
            continue
        sched.thins.append(ThinDBH(year=year, proportion=1.0))
        sched.clearcut_years.append(year)
        sched.regen.extend(_regen_records(year, fortypcd, stand_sdi))
        thin_year = _snap_up(year + REGEN_DELAY_YEARS + INDUSTRIAL_THIN_AGE)
        if thin_year <= horizon_end:
            sched.thins.append(ThinDBH(year=thin_year,
                                       proportion=INDUSTRIAL_THIN_PROPORTION,
                                       max_dbh=INDUSTRIAL_THIN_MAX_DBH))
        return True
    return False


def heuristic_group(owner_class: str, mgmt_class: int) -> str:
    """Which heuristic rule a stand falls under: noop / industrial / random."""
    if mgmt_class == 1:                                   # riparian: absolute
        return "noop"
    if owner_class in ("private_family", "unknown"):
        return "noop"
    if owner_class == "private_industrial":
        return "industrial"
    return "random"
