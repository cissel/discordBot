#!/usr/bin/env Rscript
# mergeNflCoachingStaff.R
# Merges the 4 delegated coaching-staff CSV chunks (compiled from LLM
# knowledge, not scraped) into one canonical team/season table, validates
# team code coverage against nflverse standard codes, and reports confidence
# distribution so low-confidence rows can be spot-checked before use.
suppressMessages({
  library(dplyr)
  library(readr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")

files <- c("coaching_staff_group_a.csv", "coaching_staff_group_b1.csv",
           "coaching_staff_group_b2.csv", "coaching_staff_group_c.csv")

all_staff <- bind_rows(lapply(files, function(f) {
  read_csv(file.path(OUT_DIR, f), show_col_types = FALSE)
}))

cat("Total rows merged:", nrow(all_staff), "\n")
cat("Distinct teams:", n_distinct(all_staff$team), "\n")
cat("Distinct seasons:", paste(sort(unique(all_staff$season)), collapse=","), "\n\n")

expected_teams <- c("ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET",
                     "GB","HOU","IND","JAX","KC","LA","LAC","LV","MIA","MIN","NE",
                     "NO","NYG","NYJ","PHI","PIT","SEA","SF","TB","TEN","WAS")
missing_teams <- setdiff(expected_teams, unique(all_staff$team))
extra_teams   <- setdiff(unique(all_staff$team), expected_teams)
cat("Missing expected teams:", if(length(missing_teams)) paste(missing_teams, collapse=",") else "none", "\n")
cat("Unexpected team codes:", if(length(extra_teams)) paste(extra_teams, collapse=",") else "none", "\n\n")

# Duplicate check: exactly one row per team-season
dupe_check <- all_staff %>% count(team, season) %>% filter(n != 1)
cat("Duplicate/missing team-season combos:", nrow(dupe_check), "\n")
if (nrow(dupe_check) > 0) print(dupe_check)

cat("\nConfidence distribution:\n")
print(table(all_staff$confidence))

out_path <- file.path(OUT_DIR, "coaching_staff.csv")
write_csv(all_staff %>% arrange(team, season), out_path)
cat("\nWrote:", out_path, "\n")
cat("Final row count:", nrow(all_staff), "\n")
