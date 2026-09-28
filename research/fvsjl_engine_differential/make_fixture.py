"""Stage the engine-differential fixture: 200 random SN plots x 3 artemis regimes.

    PYTHONPATH=. uv run python -m research.fvsjl_engine_differential.make_fixture [--source DB] [--work DIR]

Writes WORK/in.db (FVS_STANDINIT_PLOT / FVS_TREEINIT_PLOT subset, indexed on STAND_CN),
WORK/cns.txt, and one keyfile per (engine, regime, stand) under WORK/<regime>_<engine>/,
rendered by the production renderer `pipeline.s4_fvs.regime_templates.render_keyfile`.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from pathlib import Path

from pipeline.s4_fvs.regime_templates import render_keyfile

HERE = Path(__file__).resolve().parent
REGIMES = {
    "none": ("no_management", {}),
    "thin": ("thin_from_below", {"year": 2032, "max_dbh": 8, "proportion": 0.4}),
    "plant": ("plantation_rotation", {"thin_year": 2032, "clearcut_year": 2047}),
}
ENGINES = ("f", "jl")   # f = Fortran FVSsn, jl = FVSjl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="/mnt/c/FVS/Artemis_project/FVS_Data.db")
    ap.add_argument("--work", default=str(HERE / "work"))
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(f"file:{a.source}?mode=ro&immutable=1", uri=True)
    cns = [r[0] for r in src.execute("SELECT STAND_CN FROM FVS_STANDINIT_PLOT WHERE VARIANT='SN'")]
    random.seed(a.seed)
    cns = random.sample(cns, a.n)
    (work / "in.db").unlink(missing_ok=True)
    dst = sqlite3.connect(work / "in.db")
    marks = ",".join("?" * len(cns))
    for table in ("FVS_STANDINIT_PLOT", "FVS_TREEINIT_PLOT"):
        dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0])
        rows = src.execute(f"SELECT * FROM {table} WHERE STAND_CN IN ({marks})", cns).fetchall()
        dst.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
        dst.execute(f"CREATE INDEX i_{table} ON {table}(STAND_CN)")
        print(table, len(rows))
    dst.commit()
    (work / "cns.txt").write_text("\n".join(map(str, cns)))

    for tag, (regime, params) in REGIMES.items():
        for engine in ENGINES:
            d = work / f"{tag}_{engine}"
            d.mkdir(exist_ok=True)
            for cn in cns:
                (d / f"{cn}.key").write_text(render_keyfile(
                    cn, cn, regime, params, in_db=str(work / "in.db"), out_db="out.db"))


if __name__ == "__main__":
    main()
