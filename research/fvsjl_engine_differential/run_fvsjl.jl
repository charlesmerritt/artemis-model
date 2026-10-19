# Serial FVSjl over the fixture, writing DSNOut SQLite exactly as the keyfiles ask.
#   JULIA_DEPOT_PATH=~/.julia julia -O1 --project=<FVSjl> run_fvsjl.jl [WORK]
# -O1: the default -O2 JIT segfaulted inside LLVM on Julia 1.12.6 (see the design spec, B2).
using FVSjl
const WORK = abspath(get(ARGS, 1, joinpath(@__DIR__, "work")))

function batch(tag)
    d = joinpath(WORK, "$(tag)_jl"); cd(d); rm("out.db"; force = true)
    bad = String[]
    for k in sort(filter(endswith(".key"), readdir(d)))
        try
            run_keyfile(k; variant = FVSjl.Southern())
        catch e
            push!(bad, k * " => " * first(sprint(showerror, e), 300))
        end
    end
    bad
end

t0 = time(); cd(joinpath(WORK, "none_jl"))
run_keyfile(first(sort(filter(endswith(".key"), readdir()))); variant = FVSjl.Southern())
println("first call (JIT) ", round(time() - t0, digits = 1), " s")
for tag in ("none", "thin", "plant")
    t = time(); bad = batch(tag)
    println(tag, " jl serial: ", round(time() - t, digits = 2), " s bad=", length(bad))
    foreach(println, first(bad, 3))
end
