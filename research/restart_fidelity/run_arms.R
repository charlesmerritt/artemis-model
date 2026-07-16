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

# Arm M: multi-stand continuous reference (5 stands, Inv_Year 2019).
# fvsRun() processes ONE stand then returns 0 ("good running state"); it must be
# called until it returns 2 ("finished processing all the stands").
run_arm_m <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_m.key")
  for (i in 1:50) {
    rtn <- fvsRun()
    cat("  arm m stand-loop iter", i, "rtn:", rtn, "\n")
    if (rtn != 0) break
  }
  cat("arm m final return code:", rtn, "\n")
  invisible(rtn)
}

# Arm N: multi-stand stop/restart. Barriers at 2024/2029/2034 (Inv_Year 2019).
# Tests the claim that ONE restart file stores and rehydrates ALL stands --
# the mechanism a global barrier is.
MULTI_PAUSE <- c(2024, 2029, 2034)

run_arm_n_seg <- function(seg, code = 6) {
  fvsLoad("FVSsn", bin = FVSBIN)
  f <- function(y) paste0("arm_n_", y, ".rst")
  cmd <- switch(seg,
    n1 = paste0("--keywordfile=arm_n.key --stoppoint=", code, ",2024,", f(2024)),
    n2 = paste0("--restart=", f(2024), " --stoppoint=", code, ",2029,", f(2029)),
    n3 = paste0("--restart=", f(2029), " --stoppoint=", code, ",2034,", f(2034)),
    n4 = paste0("--restart=", f(2034))
  )
  cat("segment", seg, "cmdline:", cmd, "\n")
  fvsSetCmdLine(cmd)
  # Drive until FVS reports all stands done (2) or errors (1). A negative
  # restart code is a signal to call again; 0 means another stand is pending.
  for (i in 1:200) {
    rtn  <- fvsRun()
    code_now <- fvsGetRestartcode()
    if (rtn != 0) break
  }
  cat("segment", seg, "settled rtn:", rtn, "restartcode:", code_now, "\n")
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

run_arm_c_seg <- function(seg, code = 2, tag = "c") {
  # `code` is the stop point used for the store. Stop point 2 (just after the
  # first Event Monitor call) is early in the cycle; FMDOUT -- which recomputes
  # BIOSHRB from FLIVE -- runs later (fmmain.f:202), immediately before the
  # carbon report (fmmain.f:206). Varying `code` tests whether the carbon
  # divergence is a stop-point PLACEMENT artifact rather than state loss.
  fvsLoad("FVSsn", bin = FVSBIN)
  k <- paste0("arm_", tag)
  f <- function(y) paste0(k, "_", y, ".rst")
  cmd <- switch(seg,
    c1 = paste0("--keywordfile=", k, ".key --stoppoint=", code, ",2004,", f(2004)),
    c2 = paste0("--restart=", f(2004), " --stoppoint=", code, ",2009,", f(2009)),
    c3 = paste0("--restart=", f(2009), " --stoppoint=", code, ",2014,", f(2014)),
    c4 = paste0("--restart=", f(2014))
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

# Segment args accept an optional stop code and output tag:
#   run_arms.R c2            -> stop point 2, arm_c.*
#   run_arms.R c2 6 e        -> stop point 6, arm_e.*
code <- if (length(args) > 1) as.integer(args[2]) else 2
tag  <- if (length(args) > 2) args[3] else "c"

if (arm == "a") run_arm_a() else
if (arm == "b") run_arm_b() else
if (arm == "c") run_arm_c() else
if (arm == "d") run_arm_d() else
if (arm == "m") run_arm_m() else
if (arm %in% c("n1", "n2", "n3", "n4")) run_arm_n_seg(arm, code) else
if (arm %in% c("c1", "c2", "c3", "c4")) run_arm_c_seg(arm, code, tag)
