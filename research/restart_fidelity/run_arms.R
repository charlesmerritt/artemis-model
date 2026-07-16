# Restart-fidelity spike driver. Runs on the Windows host via Rscript.exe.
#
# Usage: Rscript.exe run_arms.R <arm>
# Arms: a = continuous; b = in-process pause; c = stop/restart; d = tree-list rebuild
#
# Stop point 2 = "just after the first call to the Event Monitor" (rFVS::fvsRun),
# which is where fvsCutNow() applies management. Arms B and C pause there.

library(rFVS)

FVSBIN <- "C:\\FVS\\FVSSoftware\\FVSbin"
SPIKE  <- "C:\\FVS\\artemis_spike"
PAUSE_YEARS <- c(2004, 2009, 2014)

setwd(SPIKE)

args <- commandArgs(trailingOnly = TRUE)
arm  <- if (length(args) > 0) args[1] else "a"

run_arm_a <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_a.key")
  rtn <- fvsRun()
  cat("arm a return code:", rtn, "\n")
  invisible(rtn)
}

run_arm_b <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_b.key")
  for (yr in PAUSE_YEARS) {
    rtn <- fvsRun(2, yr)                       # stop point 2, at year yr
    cat("arm b paused at", yr, "rtn:", rtn, "code:", fvsGetRestartcode(), "\n")
    if (rtn != 0) {
      cat("arm b: unexpected return", rtn, "at", yr, "\n")
      break
    }
    s <- fvsGetSummary()                        # read state at the barrier
    cat("  summary rows:", nrow(s), "\n")
  }
  rtn <- fvsRun()                               # run to completion
  cat("arm b final return code:", rtn, "\n")
  invisible(rtn)
}

if (arm == "a") run_arm_a() else if (arm == "b") run_arm_b()
