"""
Exact-string handling for join keys (FIA control numbers, TreeMap IDs, MU_IDs).

FIA control numbers — ``PLT_CN``, ``STAND_CN``, ``COND.CN``, ``PLOT.CN`` — are up to
19-digit integers. An IEEE-754 double carries only 15–17 significant digits, so any trip
through a float silently damages them, and the damage never surfaces where it happens: the
join simply returns fewer rows, and a stand quietly inherits the wrong tree list.

Two distinct failure modes, both reachable from this repo's stack:

1. **Truncation** — above ``2**53`` consecutive integers are no longer distinct in a
   double: ``int(float("1234567890123456789")) == 1234567890123456768``. Those digits are
   gone; no downstream cast recovers them, and two different plots can collapse onto one
   key. The bound belongs to the *dtype*, not to the pipeline: a float32 gives out at
   ``2**24``, so an ordinary 15-digit control number sitting in a float32 column is already
   rounded (``236048879010661`` → ``236048886005760``) while still looking small enough to
   trust. :func:`exact_int_limit` derives the right bound per dtype.
2. **Reformatting** — an ID that *is* exactly representable still stops being a usable key
   once it round-trips through a float. pandas renders ``236048879010661.0`` as
   ``"236048879010661.0"``; R's ``write.csv`` renders the same value as
   ``"1.7498047010478e+13"``. The digits survive, the string key does not, and every
   equality join misses.

The rule for the pipeline: read ID columns as strings, keep them as strings, and pass them
through :func:`as_id_series` at any boundary where the dtype is not already guaranteed.
Plain ``.astype(str)`` is never correct on an ID column — on a float column it produces
``"1.0"`` where the other side of the join holds ``"1"``.

Mode 2 is repairable and is repaired here (with a warning naming the column), because a
value inside its dtype's exact-integer range provably survived the float intact. Mode 1 is
not repairable and raises :class:`IdPrecisionError` — a truncated control number must not
be passed off as a join key.

Note on zero-padded IDs: digit-only strings are passed through untouched, so FVS
``STAND_ID`` values like ``"010006100083"`` keep their leading zeros. A zero-padded ID that
has *already* been through a float has lost that padding irrecoverably (there is no way to
know the intended width), so those must be read as strings at the source.
"""

from __future__ import annotations

import logging
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Largest integer a float64 holds exactly. At or above it, consecutive integers collide,
# so a value that has been through a double cannot be trusted to be the value we started
# with — the only honest response is to refuse it.
MAX_EXACT_FLOAT_INT = 2**53


def exact_int_limit(spec) -> int:
    """Largest integer the given float type represents exactly.

    The bound is a property of the *source* dtype, not a constant. A float32 carries 24
    mantissa bits, so it stops representing consecutive integers at 2**24 — a control
    number like 236048879010661 is already rounded to 236048886005760 by the time it is
    sitting in a float32 column, yet it is still far below the float64 bound of 2**53.
    Checking every float against 2**53 would wave that through and emit a corrupted value
    as an exact-looking key. ``spec`` may be a numpy dtype, a pandas extension dtype, or a
    scalar type; anything unrecognised falls back to the float64 bound.
    """
    numpy_dtype = getattr(spec, "numpy_dtype", spec)  # pandas Float32Dtype -> float32
    try:
        return 2 ** (int(np.finfo(numpy_dtype).nmant) + 1)
    except (TypeError, ValueError):
        return MAX_EXACT_FLOAT_INT

# The dtype every identifier column should end up in.
ID_DTYPE = "string"

# The shape an identifier should have on arrival: digits, nothing else.
_DIGITS_RE = re.compile(r"^[+-]?\d+$")

# The shape it has after a round trip through a float, in either rendering we have seen:
# "236048879010661.0" (pandas .astype(str)) and "1.7498047010478e+13" (R write.csv).
_FLOATISH_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

# Identifier columns used as join keys across the pipeline. Loaders use this to pin dtypes
# without every call site restating the list.
ID_COLUMNS: tuple[str, ...] = (
    "PLT_CN", "STAND_CN", "Stand_CN", "stand_cn",
    "CN", "COND_CN", "PLOT_CN", "TREE_CN", "SUBP_CN",
    "STAND_ID", "Stand_ID", "stand_id", "StandID",
    "MU_ID", "unit_id", "TM_ID", "TM_VALUE",
)


class IdPrecisionError(ValueError):
    """An identifier has lost digits to a float and cannot be recovered.

    Raised rather than warned on purpose: a truncated control number still *looks* like a
    control number, so letting it through produces a join that silently drops or
    mis-assigns stands instead of failing.
    """


def _fail(column: str, value: object, why: str) -> IdPrecisionError:
    return IdPrecisionError(
        f"column {column!r}: {value!r} {why}. FIA control numbers must be read and kept as "
        f"strings (e.g. pd.read_csv(..., dtype={{'{column}': 'string'}}), or "
        f"colClasses = c({column} = 'character') in R); a value that has been through a "
        f"float cannot be repaired here."
    )


def _normalize(
    value: object, column: str, limit: int | None = None, width: str | None = None
) -> tuple[str, bool]:
    """Return ``(exact_string, was_repaired)`` for one identifier value.

    ``limit`` is the exact-integer bound of the source dtype (see :func:`exact_int_limit`)
    and ``width`` its name, for the error message. Callers that know the column's dtype
    should pass both — pandas hands plain Python floats to the iterator for some dtypes, so
    the scalar cannot always be trusted to report the width it came from. A bare string
    carries no evidence of what produced it, so those fall back to the float64 bound.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _fail(column, value, "is blank")
        if _DIGITS_RE.match(text):
            return text, False
        if _FLOATISH_RE.match(text):
            # Written by something that held this ID in a float. Decimal parses the text
            # exactly, so no second float round trip is introduced here.
            try:
                dec = Decimal(text)
            except InvalidOperation:
                raise _fail(column, value, "is not an integer identifier") from None
            if dec != dec.to_integral_value():
                raise _fail(column, value, "is not a whole number")
            # A string has no dtype to interrogate, so the float64 bound is the only one
            # available — text is assumed to have come from the widest float.
            if abs(dec) >= MAX_EXACT_FLOAT_INT:
                raise _fail(column, value, f"exceeds {MAX_EXACT_FLOAT_INT} and lost digits to a float")
            return str(int(dec)), True
        # Anything else is a non-numeric identifier — "mu_12125_00000001", a synthetic
        # test key, an FVS stand label. It cannot have been through a float, so there is
        # nothing to repair and nothing to reject; pass it through untouched. This module
        # guards against float damage, it does not police identifier formats.
        return text, False

    if isinstance(value, (bool, np.bool_)):
        raise _fail(column, value, "is not an integer identifier")

    # Match the integer types explicitly. numpy integers do not subclass Python ``int``,
    # and ``.is_integer()`` on integer scalars is a recent addition (CPython 3.12,
    # NumPy 1.25) — relying on the method existing would silently reject an already-exact
    # numpy int sitting in an object-dtype column on an older stack.
    if isinstance(value, (int, np.integer)):
        return str(int(value)), False

    if isinstance(value, (float, np.floating)) or hasattr(value, "is_integer"):
        number = float(value)
        if not math.isfinite(number):
            raise _fail(column, value, "is not finite")
        if not number.is_integer():
            raise _fail(column, value, "is not a whole number")
        # Bound by the dtype the value actually arrived in. Using 2**53 for a float32 would
        # accept an already-rounded control number and hand it on as an exact-looking key.
        dtype = getattr(value, "dtype", None)
        bound = limit if limit is not None else exact_int_limit(dtype if dtype is not None else type(value))
        if abs(number) >= bound:
            label = width or getattr(dtype, "name", "float64")
            raise _fail(column, value, f"exceeds {bound}, the exact-integer limit for {label}, "
                                       f"and has already lost digits")
        return str(int(number)), True

    raise _fail(column, value, "is not an integer identifier")


def normalize_id(value: object, *, column: str = "id") -> str:
    """Exact digit string for a single identifier. Raises on unrecoverable precision loss."""
    return _normalize(value, column)[0]


def as_id_series(values, *, column: str | None = None) -> pd.Series:
    """
    Coerce a column of identifiers to exact strings, preserving every digit.

    Use this instead of ``.astype(str)`` anywhere an ID column's dtype is not already
    guaranteed — reading a GeoPackage field, joining two frames, or accepting a caller's
    DataFrame. Missing values pass through as ``pd.NA``.

    Values that went through a float but stayed inside that float's exact-integer range are
    repaired to their exact digits and logged; values at or above it raise
    :class:`IdPrecisionError`. The bound comes from the column's own dtype — 2**53 for a
    float64, but only 2**24 for a float32 — so a control number already rounded by a narrow
    float is rejected rather than passed on as an exact-looking key.
    """
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    name = column or (series.name if series.name is not None else "id")
    is_float = pd.api.types.is_float_dtype(series.dtype)
    limit = exact_int_limit(series.dtype) if is_float else None
    width = getattr(series.dtype, "name", str(series.dtype)) if is_float else None

    if pd.api.types.is_integer_dtype(series.dtype) and not pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(ID_DTYPE).rename(series.name)

    # Fast path for text columns, which is the healthy case and the one that runs over
    # million-row tree lists. Only '.', 'e' and 'E' can appear in a float's rendering, so
    # a column with none of them is already exact and needs no per-value work.
    if pd.api.types.is_string_dtype(series.dtype) or series.dtype == object:
        text = series.astype(ID_DTYPE).str.strip()
        if not bool(text.str.contains(r"[.eE]", na=False).any()):
            return text.rename(series.name)
        series = text

    out: list[object] = []
    repaired = 0
    for value in series:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            out.append(pd.NA)
            continue
        text_value, was_repaired = _normalize(value, str(name), limit, width)
        repaired += was_repaired
        out.append(text_value)

    if repaired:
        logger.warning(
            "column %r: repaired %d identifier(s) that had been through a float "
            "(e.g. '1.0' or '1.7498047010478e+13'). The values were below %d, the exact-"
            "integer limit for %s, so no digits were lost — but the producing step should "
            "emit them as strings.",
            name, repaired, limit or MAX_EXACT_FLOAT_INT, width or "float64",
        )
    return pd.Series(out, index=series.index, dtype=ID_DTYPE, name=series.name)


def normalize_id_columns(df: pd.DataFrame, columns) -> pd.DataFrame:
    """Return a copy of ``df`` with each named column coerced by :func:`as_id_series`."""
    out = df.copy()
    for column in columns:
        if column in out.columns:
            out[column] = as_id_series(out[column], column=column)
    return out


def read_id_csv(path: Path, id_columns=ID_COLUMNS, **kwargs) -> pd.DataFrame:
    """
    ``pd.read_csv`` that never lets an identifier column be inferred as a number.

    Any column in ``id_columns`` present in the file is read as text and then validated,
    so a CSV that already carries float-damaged IDs fails (or is repaired and logged) at
    the read, not silently three joins later.
    """
    dtype = dict(kwargs.pop("dtype", None) or {})
    header = pd.read_csv(path, nrows=0, **{k: v for k, v in kwargs.items() if k != "usecols"})
    present = [c for c in id_columns if c in header.columns]
    for column in present:
        dtype.setdefault(column, ID_DTYPE)

    df = pd.read_csv(path, dtype=dtype, **kwargs)
    return normalize_id_columns(df, [c for c in present if c in df.columns])


def report_key_overlap(left, right, *, left_name: str, right_name: str) -> tuple[int, int, int]:
    """
    Log how two identifier sets overlap before a join, and shout if they do not.

    A zero overlap between two ID columns is almost always a formatting mismatch
    (``"1"`` vs ``"1.0"``, or a scientific-notation control number) rather than genuinely
    disjoint data, and an inner join on it looks exactly like a successful run that found
    nothing. Returns ``(n_left, n_right, n_shared)``.
    """
    left_keys = {k for k in pd.Series(left).dropna().astype(str)}
    right_keys = {k for k in pd.Series(right).dropna().astype(str)}
    shared = left_keys & right_keys

    if left_keys and right_keys and not shared:
        logger.warning(
            "no %s key matched any %s key (%d vs %d distinct). This is usually a dtype "
            "mismatch, not disjoint data — sample %s: %s; sample %s: %s",
            left_name, right_name, len(left_keys), len(right_keys),
            left_name, sorted(left_keys)[:3], right_name, sorted(right_keys)[:3],
        )
    return len(left_keys), len(right_keys), len(shared)
