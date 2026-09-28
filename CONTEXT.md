# ARTEMIS

Landscape forest-management simulation for the Eastern US: FVS grows every stand under every
management option it is allowed, and a simulated annealer picks one option per stand so
harvest volume follows observed TPO levels per owner group.

## Language

### The landscape

**Management unit**:
The smallest piece of landscape that carries one management decision. Delineated by LETO.
_Avoid_: Stand (FVS's word for the same object; use it only when naming an FVS input)

**Owner class**:
One of the seven forest classes of the Harris et al. (2025) ownership raster, RDS-2025-0045:
family, corporate, tribal, federal, state, local, unknown.
_Avoid_: Ownership type, owner group, LETO owner code

**Owner group**:
One of the three groups the TPO harvest reports budget in — Private, Federal (NF), Other
public. Several owner classes charge against one owner group.

**Riparian zone**:
Streamside area a management unit falls in, which may only grow. Structural, never a
weighted preference.
_Avoid_: SMZ, buffer (both name the same thing in source data)

### Management

**Prescription**:
A named silvicultural pattern, meaning the ordered harvest entries and their intensities,
that a management unit may be assigned.
_Avoid_: Regime, treatment, scenario

**Rotation age**:
The stand age at which a prescription takes its regeneration harvest. Pine cuts at 25 or 40,
hardwood at 60 or 80. Separate from the offset, which delays when the prescription starts.

**Commercial thin**:
A harvest entry that removes merchantable stems inside a diameter window and leaves the stand
growing. The only kind of thin ARTEMIS models.
_Avoid_: Pre-commercial thin, retention harvest (neither is modelled)

**Regeneration harvest**:
A harvest entry that removes the stand and restarts it at age 0 from an imputed tree list.
_Avoid_: Clearcut (same thing; use it in prose, not as a config name)

**Menu**:
The prescriptions an owner class may choose among. `no_management` is always on it.
_Avoid_: Eligible set, library (the library is the FVS output, not the choices)

**Offset**:
Whole years by which a prescription's first entry is delayed, carrying every later entry with
it. A separate axis from the prescription, never part of its name. Diaz et al. used 5, 10 and
15 years; ARTEMIS's grid is under review.
_Avoid_: Delay, timing shift

**Trajectory**:
What FVS produces for one prescription at one offset on one donor plot: the stand's state
every cycle over the horizon.

**Trajectory library**:
Every trajectory, precomputed, so scheduling is arithmetic rather than another FVS run.
