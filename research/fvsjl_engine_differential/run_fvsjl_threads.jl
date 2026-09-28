# Threaded FVSjl throughput: all 600 keyfiles with the DataBase/DSNOut block stripped
# (so threads do not contend on one SQLite file), summaries returned as CSV text.
#   JULIA_DEPOT_PATH=~/.julia julia -O1 -t 16 --project=<FVSjl> run_fvsjl_threads.jl [WORK]
using FVSjl, Base.Threads
const WORK = abspath(get(ARGS, 1, joinpath(@__DIR__, "work")))
d = joinpath(WORK, "thr"); mkpath(d)
ks = String[]
for tag in ("none", "thin", "plant"), k in filter(endswith(".key"), readdir(joinpath(WORK, "$(tag)_jl")))
    txt = replace(read(joinpath(WORK, "$(tag)_jl", k), String),
                  r"DataBase\nDSNOut\nout.db\nSummary        2\nEnd\n" => "")
    p = joinpath(d, "$(tag)_$k"); write(p, txt); push!(ks, p)
end
run_keyfile(ks[1]; variant = FVSjl.Southern(), output = :csv)   # warm the JIT
for rep in 1:2
    res = Vector{String}(undef, length(ks)); t = time()
    @threads for i in eachindex(ks)
        res[i] = run_keyfile(ks[i]; variant = FVSjl.Southern(), output = :csv)
    end
    el = time() - t
    println("threads=", nthreads(), " runs=", length(ks), " ", round(el, digits = 2), "s  ",
            round(length(ks) / el, digits = 1), " runs/s")
end
