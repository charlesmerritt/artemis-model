# Where FVSjl's per-stand wall time goes: reading the input SQLite vs writing DSNOut.
#
#   JULIA_DEPOT_PATH=~/.julia julia -O1 --project=<FVSjl> io_ablation.jl [WORK] [TMPFS_DIR]
#
# Why this exists: FVSjl's DATABASE/DSNOut writer is fast on a RAM filesystem and ~50-100x
# slower on a real disk, which is easy to measure by accident (a benchmark staged under a
# tmpfs /tmp looks ~90x faster than the same run inside the repo). The input side is cheap
# either way. Hence the production recommendation: run with `output = :csv` and bulk-load,
# never with DSNOut per stand.
using FVSjl
const WORK = abspath(get(ARGS, 1, joinpath(@__DIR__, "work")))
const TMP = abspath(get(ARGS, 2, get(ENV, "TMPDIR", "/tmp")))
const N = 40

# Stage N keyfiles into `dest`, pointing DSNOut at `out_db` ("" strips the DataBase block).
function stage(dest, out_db)
    rm(dest; recursive = true, force = true); mkpath(dest)
    ks = sort(filter(endswith(".key"), readdir(joinpath(WORK, "none_jl"))))[1:N]
    map(ks) do k
        txt = read(joinpath(WORK, "none_jl", k), String)
        txt = isempty(out_db) ?
              replace(txt, r"DataBase\nDSNOut\n[^\n]*\nSummary        2\nEnd\n" => "") :
              replace(txt, "\nout.db\n" => "\n" * out_db * "\n")
        p = joinpath(dest, k); write(p, txt); p
    end
end

function bench(label, paths, out_db)
    run_keyfile(paths[1]; variant = FVSjl.Southern())          # warm the JIT
    isempty(out_db) || rm(out_db; force = true)
    t = time()
    for p in paths
        run_keyfile(p; variant = FVSjl.Southern())
    end
    el = time() - t
    println(label, ": ", round(el, digits = 2), "s  ", round(N / el, digits = 1), " runs/s")
end

# The fixture (and so every input read) lives on the repo's filesystem in all three cases.
bench("in=disk  out=none (summary text only)", stage(joinpath(WORK, "abl_none"), ""), "")
bench("in=disk  out=DSNOut on disk", stage(joinpath(WORK, "abl_disk"), joinpath(WORK, "abl_disk.db")),
      joinpath(WORK, "abl_disk.db"))
bench("in=disk  out=DSNOut on tmpfs", stage(joinpath(WORK, "abl_tmpfs"), joinpath(TMP, "abl_tmpfs.db")),
      joinpath(TMP, "abl_tmpfs.db"))
