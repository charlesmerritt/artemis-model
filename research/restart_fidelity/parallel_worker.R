# One FVS worker: load FVSsn, run one keyfile to completion, report.
#
# This is the unit a parallel launcher spawns N of, one OS process each. Because
# official FVS keeps stand state in global common blocks, isolation is per
# PROCESS -- so each worker must be its own Rscript invocation, never threads in
# one R session. Run on the Windows host via Rscript.exe.
#
# Usage: Rscript.exe parallel_worker.R <keyfile> [run_dir]

library(rFVS)

args    <- commandArgs(trailingOnly = TRUE)
keyfile <- args[1]
run_dir <- if (length(args) > 1) args[2] else "C:\\FVS\\artemis_spike"

setwd(run_dir)
fvsLoad("FVSsn", bin = "C:\\FVS\\FVSSoftware\\FVSbin")
fvsSetCmdLine(paste0("--keywordfile=", keyfile))

# fvsRun() returns 0 per stand and 2 when a multi-stand keyfile is exhausted;
# loop so one worker can own a bundle of stands, not just one.
repeat {
  rc <- fvsRun()
  if (rc != 0) break
}
cat(keyfile, "done rc=", rc, "\n")
