"""Menu variants for the 2026-10-08 permissive-menu sweep, and how each renders to FVS.

A *menu variant* is one answer to "what may each stand choose from?". The sweep holds the
landscape, the TPO targets, the objective weights and the search fixed (all the
2026-09-14/09-28 pipeline's) and varies only this. Three things vary:

* **which prescriptions each owner class may take** — the pre-2026-10-08 config
  (`baseline_management_regimes.yaml`, 2-3 options per class) or the live
  `config/management_regimes.yaml`, where every class takes every cutting prescription;
* **whether a rotation repeats** — `RotationMode.REPEAT` honours `repeats: true` by adding
  the next thin and clearcut to the same keyfile, each clearcut with its own `Estab`
  packet (`probe_second_rotation.py` shows FVS replants and regrows after the second);
  `SINGLE` is what every artifact so far has run;
* **the timing offsets** and **whether forest type filters the menu**.

The first rotation of every prescription is resolved by the repository's own
`assign_prescription`, so minimum harvest age, horizon clipping and the overmature
downgrade are the repository's. Only the repeat is this module's.
"""

from __future__ import annotations

import copy
import dataclasses
from enum import Enum
from pathlib import Path

from pipeline.s3_management.regime_assignment import (
    assign_prescription,
    eligible_prescriptions,
    load_regimes_config,
)
from pipeline.s4_fvs.regime_templates import (
    _KEYFILE,
    DEFAULT_INV_YEAR,
    Regeneration,
    ThinDBH,
    build_regeneration,
    build_thins,
    render_estab_block,
    render_schedule_block,
)

EXP_DIR = Path(__file__).resolve().parent
REPO = EXP_DIR.parents[1]
BASELINE_CONFIG = EXP_DIR / "baseline_management_regimes.yaml"
LIVE_CONFIG = REPO / "config/management_regimes.yaml"

INV_YEAR = DEFAULT_INV_YEAR            # 2022
CYCLE_YEARS = 5
LAST_ENTRY_YEAR = INV_YEAR + 50        # 2072: the last scored cycle
NUM_CYCLE = 11                         # one carrier cycle so a 2072 entry executes
OFFSET_SEP = "@+"                      # `{prescription}@+{offset}`, as 2026-09-14 names it

# The frozen 2026-08-17 library records owner classes in its own vocabulary.
LEGACY_OWNER_CLASSES = {"private_family": "family", "private_industrial": "corporate",
                        "private_corporate_other": "corporate"}


class RotationMode(Enum):
    SINGLE = "single"      # one rotation per keyfile, as every artifact to date
    REPEAT = "repeat"      # honour `repeats: true` inside the horizon


class ForestTypeRule(Enum):
    FILTER = "filter"      # a prescription's `forest_types` restricts who may take it
    IGNORE = "ignore"      # any stand may take any prescription (e.g. pine on hardwood)


class OwnerMenu(Enum):
    BASELINE = "baseline"  # the 2-3 options per class before 2026-10-08
    LIVE = "live"          # config/management_regimes.yaml: everything, for everyone


@dataclasses.dataclass(frozen=True)
class MenuVariant:
    name: str
    owner_menu: OwnerMenu
    rotations: RotationMode
    forest_types: ForestTypeRule
    offsets: tuple[int, ...]
    exclude: tuple[str, ...] = ()
    description: str = ""


OFFSETS_0_15 = (0, 5, 10, 15)          # 2026-09-14's grid
OFFSETS_0_25 = (0, 5, 10, 15, 20, 25)  # AGENTS.md's 5-25 year offsets

VARIANTS: tuple[MenuVariant, ...] = (
    MenuVariant("baseline", OwnerMenu.BASELINE, RotationMode.SINGLE, ForestTypeRule.FILTER,
                OFFSETS_0_15, description="the pre-2026-10-08 menus; reproduces 2026-09-28's setup"),
    MenuVariant("all_owners", OwnerMenu.LIVE, RotationMode.SINGLE, ForestTypeRule.FILTER,
                OFFSETS_0_15, exclude=("natural_short_rotation",),
                description="every owner may take every existing prescription"),
    MenuVariant("all_owners_short_natural", OwnerMenu.LIVE, RotationMode.SINGLE,
                ForestTypeRule.FILTER, OFFSETS_0_15,
                description="+ a 30-year natural-regeneration clearcut rotation for any type"),
    MenuVariant("repeat_rotations", OwnerMenu.LIVE, RotationMode.REPEAT, ForestTypeRule.FILTER,
                OFFSETS_0_15, description="+ short rotations repeat inside the 50-year horizon"),
    MenuVariant("repeat_offsets_25", OwnerMenu.LIVE, RotationMode.REPEAT, ForestTypeRule.FILTER,
                OFFSETS_0_25, description="+ timing offsets out to 25 years"),
    MenuVariant("repeat_offsets_25_any_type", OwnerMenu.LIVE, RotationMode.REPEAT,
                ForestTypeRule.IGNORE, OFFSETS_0_25,
                description="+ any forest type may take a planted-pine rotation"),
)


def load_variant_config(variant: MenuVariant) -> dict:
    path = BASELINE_CONFIG if variant.owner_menu is OwnerMenu.BASELINE else LIVE_CONFIG
    return load_regimes_config(str(path))


def policy_owner(owner_class: str) -> str:
    return LEGACY_OWNER_CLASSES.get(owner_class, owner_class)


def menu_for(variant: MenuVariant, config: dict, owner_class: str, branch: str) -> list[str]:
    """The prescriptions a managed stand of this owner and forest branch may take."""
    branch_arg = branch if variant.forest_types is ForestTypeRule.FILTER else None
    menu = eligible_prescriptions(policy_owner(owner_class), branch_arg, config=config)
    return [p for p in menu if p not in variant.exclude]


# --------------------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Entry:
    """One harvest operation and the regeneration it causes (empty unless stand-replacing)."""
    thin: ThinDBH
    regen: tuple[Regeneration, ...] = ()


def resolve_first_rotation(config: dict, prescription: str, stand_age: float) -> tuple[str, dict]:
    """`(template, params)` through the repository's resolver, for any owner and branch.

    `assign_prescription` resolves an owner's *default*. Writing the prescription into every
    default slot of a config copy resolves it through that same path — minimum harvest age,
    horizon clipping and template downgrade included — whoever the stand's owner is.
    """
    cfg = copy.deepcopy(config)
    for spec in cfg["owner_classes"].values():
        spec["default"] = {b: prescription for b in spec["default"]}
    p = assign_prescription({"OWN_CODE": 3, "FORTYPCD": 161, "stand_age": stand_age,
                             "SMZ_Pct": 0.0}, config=cfg)
    return p.template, p.params


def _snap(years: float) -> int:
    return int(-(-years // CYCLE_YEARS) * CYCLE_YEARS)


def _regen_for(template: str, year: int, stand_sdi: dict | None) -> tuple[Regeneration, ...]:
    params: dict = {"clearcut_year" if template == "plantation_rotation" else "year": year}
    if stand_sdi:
        params["stand_sdi"] = stand_sdi
    return tuple(build_regeneration(template, params))


def build_entries(config: dict, prescription: str, stand_age: float, rotations: RotationMode,
                  stand_sdi: dict | None) -> list[Entry]:
    """Every operation of one prescription on one stand, unshifted, inside the horizon."""
    template, params = resolve_first_rotation(config, prescription, stand_age)
    if template == "no_management":
        return []
    regen_params = {**params, "stand_sdi": stand_sdi} if stand_sdi else params
    regen = tuple(build_regeneration(template, regen_params))
    thins = build_thins(template, params)
    last_cut = max((t.year for t in thins if t.proportion >= 1.0), default=None)
    entries = [Entry(t, regen if t.year == last_cut else ()) for t in thins]

    spec = config["prescriptions"][prescription]
    if rotations is RotationMode.REPEAT and spec.get("repeats") and last_cut is not None:
        entries += _later_rotations(spec, last_cut, stand_sdi)
    return entries


def _later_rotations(spec: dict, cut: int, stand_sdi: dict | None) -> list[Entry]:
    """The next rotations after a clearcut in `cut`, until the horizon ends.

    A replanted stand is age 0 at the cut. Its thin falls at `first_thin_age` and its
    clearcut at `rotation_years`, both snapped up to a cycle; both are at least 15 years
    after the cut, so the minimum harvest age holds. A thin whose clearcut would fall past
    the horizon is kept: it happens inside the horizon whether or not the rotation finishes.
    """
    schedule, template = spec["schedule"], spec["template"]
    rotation = _snap(schedule["rotation_years"])
    out: list[Entry] = []
    while True:
        if "first_thin_age" in schedule:
            year = cut + _snap(schedule["first_thin_age"])
            if year > LAST_ENTRY_YEAR:
                break
            thin = build_thins("plantation_rotation", {"thin_year": year, "clearcut_year": year,
                                                       **spec.get("params", {})})[0]
            out.append(Entry(thin))
        cut += rotation
        if cut > LAST_ENTRY_YEAR:
            break
        out.append(Entry(ThinDBH(cut, 1.0), _regen_for(template, cut, stand_sdi)))
    return out


def shift_entries(entries: list[Entry], offset: int) -> list[Entry]:
    """Start the whole sequence `offset` years later; drop what leaves the horizon.

    A regeneration record moves with the harvest that caused it, and goes when it goes.
    """
    out = []
    for e in entries:
        year = e.thin.year + offset
        if year > LAST_ENTRY_YEAR:
            continue
        regen = tuple(dataclasses.replace(r, year=r.year + offset) for r in e.regen)
        out.append(Entry(dataclasses.replace(e.thin, year=year), regen))
    return out


def variant_label(prescription: str, offset: int) -> str:
    return prescription if prescription == "no_management" else f"{prescription}{OFFSET_SEP}{offset}"


def render(stand_cn: str, template: str, entries: list[Entry]) -> str:
    """A single-stand keyfile: the operations, then one `Estab` packet per clearcut.

    With one packet this is byte-identical to `regime_templates.render_keyfile`, so a
    single-rotation run here is the run 2026-09-14 made for the same operations.
    """
    blocks = [e.thin.render() for e in entries]
    blocks += [render_estab_block(list(e.regen)) for e in entries if e.regen]
    return _KEYFILE.format(
        stand_id=f"S{stand_cn}", stand_cn=stand_cn, regime=template, mgmt_id="A001",
        schedule_block=render_schedule_block(INV_YEAR, CYCLE_YEARS, NUM_CYCLE),
        activity_block="\n".join(blocks), out_db="FVS_Out.db", in_db="FVS_Data.db",
        stand_table="FVS_StandInit_Plot", tree_table="FVS_TreeInit_Plot", sp_label="ARTEMIS",
    )
