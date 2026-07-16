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

run_arm_c <- function() {
  # Segment 1: run from the keyword file, stop at 2004 and store all stands.
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_c.key --stoppoint=2,2004,arm_c_2004.rst")
  rtn <- fvsRun()
  cat("arm c stored at 2004, rtn:", rtn, "\n")

  # Later segments: restart from the previous file, store at the next barrier.
  segs <- list(c("arm_c_2004.rst", "2009", "arm_c_2009.rst"),
               c("arm_c_2009.rst", "2014", "arm_c_2014.rst"))
  for (s in segs) {
    fvsSetCmdLine(paste0("--restart=", s[1], " --stoppoint=2,", s[2], ",", s[3]))
    rtn <- fvsRun()
    cat("arm c restarted from", s[1], "stored at", s[2], "rtn:", rtn, "\n")
  }

  # Final segment: restart from 2014 and run to completion (no stop point).
  fvsSetCmdLine("--restart=arm_c_2014.rst")
  rtn <- fvsRun()
  cat("arm c final return code:", rtn, "\n")
  invisible(rtn)
}

# Arm C, one segment per process.
#
# FVS keeps stand state in global commons, so a fresh process per segment is both
# the cleaner test and the shape production would actually use.
#
# A NEGATIVE restart code is a signal, not a result. cmdline.f:299 sets
# `restartcode = -originalRestartCode ! signal return to caller` after getstd
# restores a stand: fvsRun() reloads the stand and returns WITHOUT running, and
# the caller must call fvsRun() again to actually resume. rFVS's own
# fvsInteractRun does the same (`if (stopPoint < 0) stopPoint = -stopPoint`).
# Calling fvsRun() once leaves the stand loaded but never grown.
#
# Usage: run_arms.R c1 | c2 | c3 | c4
run_until_settled <- function(max_iter = 20) {
  for (i in seq_len(max_iter)) {
    rtn  <- fvsRun()
    code <- fvsGetRestartcode()
    cat("  iter", i, "rtn:", rtn, "restartcode:", code, "\n")
    if (rtn != 0) return(list(rtn = rtn, code = code))   # 1 = error, 2 = all stands done
    if (code >= 0) return(list(rtn = rtn, code = code))  # settled: stored, or finished
    # code < 0 -> signal return after restore; loop to actually run
  }
  list(rtn = rtn, code = code)
}

run_arm_c_seg <- function(seg) {
  fvsLoad("FVSsn", bin = FVSBIN)
  cmd <- switch(seg,
    c1 = "--keywordfile=arm_c.key --stoppoint=2,2004,arm_c_2004.rst",
    c2 = "--restart=arm_c_2004.rst --stoppoint=2,2009,arm_c_2009.rst",
    c3 = "--restart=arm_c_2009.rst --stoppoint=2,2014,arm_c_2014.rst",
    c4 = "--restart=arm_c_2014.rst"
  )
  cat("segment", seg, "cmdline:", cmd, "\n")
  fvsSetCmdLine(cmd)
  res <- run_until_settled()
  cat("segment", seg, "settled rtn:", res$rtn, "restartcode:", res$code, "\n")
  invisible(res$rtn)
}

# Arm D: rebuild the tree list between segments.
#
# Only live-tree attributes carry forward. Calibration (CALCOM), RNG (RANCOM),
# establishment (ESTREE) and all FFE state are lost, so this is expected to
# diverge far more broadly than arm C.
run_arm_d <- function() {
  attrs <- c("id", "species", "dbh", "ht", "cratio", "tpa")

  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_d.key")
  rtn <- fvsRun(2, PAUSE_YEARS[1])
  cat("arm d segment 1 rtn:", rtn, "\n")
  trees <- fvsGetTreeAttrs(attrs)
  cat("  captured", nrow(trees), "tree records\n")

  for (yr in PAUSE_YEARS[-1]) {
    fvsSetCmdLine("--keywordfile=arm_d.key")
    rtn <- fvsRun(2, yr)
    fvsSetTreeAttrs(trees)
    trees <- fvsGetTreeAttrs(attrs)
    cat("arm d rebuilt at", yr, "rtn:", rtn, "trees:", nrow(trees), "\n")
  }
  rtn <- fvsRun()
  cat("arm d final return code:", rtn, "\n")
  invisible(rtn)
}

if (arm == "a") run_arm_a() else
if (arm == "b") run_arm_b() else
if (arm == "c") run_arm_c() else
if (arm == "d") run_arm_d() else
if (arm %in% c("c1", "c2", "c3", "c4")) run_arm_c_seg(arm)
