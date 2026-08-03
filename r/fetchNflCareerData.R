#!/usr/bin/env Rscript
# fetchNflCareerData.R
# Builds the player-season panel for the fantasy career/aging-curve model.
# Room 40 exact scoring settings applied to raw nflverse stat columns.
# Output: outputs/sports/nfl/fantasy/career_panel.csv

suppressMessages({
  library(nflreadr)
  library(dplyr)
  library(tidyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

SEASONS <- 1999:2025

cat("Fetching season-level player stats", min(SEASONS), "-", max(SEASONS), "...\n")
ps <- load_player_stats(seasons = SEASONS, summary_level = "reg")
cat("  ->", nrow(ps), "player-season rows\n")

cat("Fetching rosters (birth_date, draft info)...\n")
ro <- load_rosters(seasons = SEASONS)
cat("  ->", nrow(ro), "roster rows (week-level, will dedupe to season-level)\n")

# Room 40 exact scoring formula (pulled live from Sleeper league settings API,
# league_id 1259616442014244864):
#   pass_yd=0.04  pass_td=4   pass_int=-1  pass_2pt=2
#   rush_yd=0.1   rush_td=6   rush_2pt=2
#   rec=1 (PPR)   rec_yd=0.1  rec_td=6     rec_2pt=2
#   fum_lost=-2
ps <- ps %>%
  mutate(
    room40_pts =
      coalesce(passing_yards, 0) * 0.04 +
      coalesce(passing_tds, 0) * 4 +
      coalesce(passing_interceptions, 0) * -1 +
      coalesce(passing_2pt_conversions, 0) * 2 +
      coalesce(rushing_yards, 0) * 0.1 +
      coalesce(rushing_tds, 0) * 6 +
      coalesce(rushing_2pt_conversions, 0) * 2 +
      coalesce(receptions, 0) * 1 +
      coalesce(receiving_yards, 0) * 0.1 +
      coalesce(receiving_tds, 0) * 6 +
      coalesce(receiving_2pt_conversions, 0) * 2 +
      coalesce(fumbles_lost_total, 0) * -2
  )

# Season-level roster info: birth_date, draft capital, years_exp are static per
# player-season -- take first non-NA row per player_id+season.
ro_season <- ro %>%
  filter(!is.na(gsis_id)) %>%
  group_by(gsis_id, season) %>%
  summarise(
    birth_date  = first(na.omit(birth_date)),
    draft_club  = first(na.omit(draft_club)),
    draft_number = suppressWarnings(min(as.numeric(draft_number), na.rm = TRUE)),
    entry_year  = suppressWarnings(min(as.numeric(entry_year), na.rm = TRUE)),
    rookie_year = suppressWarnings(min(as.numeric(rookie_year), na.rm = TRUE)),
    years_exp   = suppressWarnings(max(as.numeric(years_exp), na.rm = TRUE)),
    .groups = "drop"
  ) %>%
  mutate(across(c(draft_number, entry_year, rookie_year, years_exp),
                ~ifelse(is.infinite(.), NA, .)))

panel <- ps %>%
  filter(position %in% c("QB", "RB", "WR", "TE")) %>%
  select(player_id, player_display_name, position, season, games, room40_pts) %>%
  left_join(ro_season, by = c("player_id" = "gsis_id", "season" = "season")) %>%
  mutate(
    # age as of Sept 1 of that season (standard NFL-age convention)
    age = as.numeric(difftime(as.Date(paste0(season, "-09-01")), birth_date, units = "days")) / 365.25,
    pts_per_game = ifelse(games > 0, room40_pts / games, NA)
  ) %>%
  filter(!is.na(age), games > 0) %>%
  arrange(player_id, season)

cat("\nFinal panel:", nrow(panel), "player-seasons\n")
cat("Position breakdown:\n")
print(table(panel$position))
cat("\nAge range:", round(min(panel$age),1), "-", round(max(panel$age),1), "\n")
cat("Missing draft_number:", sum(is.na(panel$draft_number)), "/", nrow(panel),
    sprintf("(%.1f%%)", 100*mean(is.na(panel$draft_number))), "\n")

out_path <- file.path(OUT_DIR, "career_panel.csv")
write.csv(panel, out_path, row.names = FALSE)
cat("\nWrote:", out_path, "\n")
