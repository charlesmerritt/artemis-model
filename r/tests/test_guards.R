# ============================================================
# Executable checks for the PLT_CN precision guards in r/01 and r/02
# ============================================================
#
# WHY THIS EXISTS:
#   The identifier-precision work (see notes/identifier-precision.md) was
#   written in a container without R, so the R changes were reviewed by reading
#   rather than running. Four defects escaped that review, and two of them were
#   introduced by the fix for the previous one:
#
#     - format() pads a CHARACTER vector to a common width; `trim` only
#       suppresses the blanks from right-justifying numerics (?format). Every
#       shorter control number silently gained a trailing space.
#     - format(NA_real_) is the string "NA", not NA, so the digits-only guard
#       treated missing data as corruption and aborted.
#
#   Both are behavioural facts about base R that no amount of reading reliably
#   catches. This script pins them down by executing the guard blocks straight
#   out of the scripts -- it parses the real source rather than restating the
#   logic, so it cannot drift from what actually ships.
#
#   It needs base R only: no DBI, RSQLite, dplyr or terra, and no /mnt/d data.
#
# RUN:
#   Rscript r/tests/test_guards.R          # from the repository root
#
# Exits non-zero on the first failure, so it is usable as a gate.
# ============================================================

options(scipen = 999)

REPO <- if (file.exists("r/01_subset_TreeMap_5county.R")) "." else ".."
failures <- 0

check <- function(label, condition) {
  passed <- isTRUE(condition)
  if (!passed) failures <<- failures + 1
  cat(sprintf("  %-56s %s\n", label, if (passed) "PASS" else "*** FAIL ***"))
}

section <- function(title) cat(paste0("\n", title, "\n"))

# Pull a block out of a script by its first and last line patterns, so these
# checks exercise the shipped code rather than a copy of it.
extract_block <- function(path, start_pattern, end_pattern) {
  src <- readLines(file.path(REPO, path), warn = FALSE)
  s <- grep(start_pattern, src)[1]
  e <- grep(end_pattern, src)[1] - 1
  if (is.na(s) || is.na(e) || e < s) {
    stop("could not locate the guard block in ", path,
         " -- the anchors in this test have drifted from the script.")
  }
  parse(text = paste(src[s:e], collapse = "\n"))
}


section("Base-R behaviours the guards depend on (the ones that bit us)")

check("format() pads a character vector, trim does not stop it",
      format(c("17498047010478", "236048879010661"), trim = TRUE)[1] == "17498047010478 ")
check("format(NA_real_) yields the string \"NA\", not NA",
      identical(format(NA_real_), "NA") && !is.na(format(NA_real_)))
check("format() on a numeric is unpadded and unscientific",
      identical(format(c(17498047010478, 236048879010661), scientific = FALSE, trim = TRUE),
                c("17498047010478", "236048879010661")))
check("2^53 is the exact-integer limit for a double",
      2^53 == 9007199254740992 && (2^53 + 1) == 2^53)


section("r/01 guard block, executed against each input shape")

guard01 <- extract_block("r/01_subset_TreeMap_5county.R",
                         "^if \\(is\\.factor\\(county_data\\$PLT_CN\\)\\) \\{",
                         "^tmid_list <- county_data")

run01 <- function(values) {
  env <- new.env()
  assign("county_data", data.frame(PLT_CN = values, stringsAsFactors = FALSE), envir = env)
  tryCatch(
    withCallingHandlers(
      { eval(guard01, envir = env)
        list(stopped = FALSE, value = get("county_data", envir = env)$PLT_CN, msg = NA_character_) },
      warning = function(w) invokeRestart("muffleWarning")),
    error = function(e) list(stopped = TRUE, value = NULL, msg = conditionMessage(e)))
}

exact <- c("17498047010478", "236048879010661")
check("character input passes through unpadded",
      identical(run01(exact)$value, exact))
check("factor input converts losslessly",
      identical(run01(factor(exact))$value, exact))
check("numeric input renders full digits, no padding",
      identical(run01(c(17498047010478, 236048879010661))$value, exact))
check("numeric NA stays NA rather than becoming \"NA\"",
      { r <- run01(c(17498047010478, NA)); !r$stopped && is.na(r$value[2]) })
check("a value at 2^53 aborts before the crosswalk is written",
      { r <- run01(c(2^53, 17498047010478))
        r$stopped && grepl("2\\^53|lost digits", r$msg) })


section("r/02 crosswalk guard, executed against each input shape")

guard02 <- extract_block("r/02_subset_FIA_SQLite_multistateR.R",
                         "^if \\(!is\\.character\\(tmid_lookup\\$PLT_CN\\)\\) \\{",
                         "^# Save output")

run02 <- function(values) {
  env <- new.env()
  assign("tmid_lookup", data.frame(PLT_CN = values, stringsAsFactors = FALSE), envir = env)
  tryCatch({ eval(guard02, envir = env); FALSE }, error = function(e) TRUE)
}

check("a clean crosswalk is allowed through", !run02(exact))
check("a genuine NA is tolerated", !run02(c("17498047010478", NA)))
check("a numeric PLT_CN is blocked before write.csv", run02(c(17498047010478, 236048879010661)))
check("scientific notation is blocked", run02("1.7498047010478e+13"))
check("trailing-blank padding is blocked", run02(c("17498047010478 ", "236048879010661")))


section("Cross-script primitives the character migration relies on")

check("setdiff() clears matched CNs when both sides are character",
      identical(setdiff(c("1", "2"), "1"), "2"))
check("paste(character(0)) is empty, not \"integer(0)\"",
      paste(character(0), collapse = ",") == "")
check("a digits-only key survives a write/read round trip",
      { f <- tempfile(fileext = ".csv"); on.exit(unlink(f), add = TRUE)
        write.csv(data.frame(PLT_CN = exact), f, row.names = FALSE)
        identical(read.csv(f, colClasses = c(PLT_CN = "character"))$PLT_CN, exact) })
check("without colClasses the same round trip loses the string form",
      { f <- tempfile(fileext = ".csv"); on.exit(unlink(f), add = TRUE)
        write.csv(data.frame(PLT_CN = exact), f, row.names = FALSE)
        !is.character(read.csv(f)$PLT_CN) })


cat(sprintf("\n%s: %d check(s) failed\n",
            if (failures == 0) "OK" else "FAILED", failures))
quit(status = if (failures == 0) 0 else 1)
