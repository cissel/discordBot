#!/usr/bin/env Rscript
# fetchNflSchemeFeatures.R
# Team-season offensive scheme/pace features from nflverse PBP.
# Captures play-calling tendency (PROE = pass rate over expected) independent
# of game script, plus pace and situational pass rates. Designed to be joined
# with a coaching-staff table (team/season -> HC/OC/DC) so tendencies can be
# re-attributed to a COACH (travels across team moves) rather than staying
# stuck to a team identity.
#
# Usage: Rscript fetchNflSchemeFeatures.R [start_year] [end_year]
# Default: 2015 2024
suppressMessages({
  library(nflreadr)
  library(dplyr)
  library(tidyr)
})

args <- commandArgs(trailingOnly = TRUE)
start_yr <- if (length(args) >= 1) as.integer(args[1]) else 2015
end_yr   <- if (length(args) >= 2) as.integer(args[2]) else 2024

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

cat(sprintf("Loading PBP %d-%d ...\n", start_yr, end_yr))
pbp <- load_pbp(start_yr:end_yr)
cat("Rows loaded:", nrow(pbp), "\n")

# Only plays with a real posteam and a pass/rush decision (exclude special teams,
# penalties-only plays, etc.)
plays <- pbp %>%
  filter(!is.na(posteam), play_type %in% c("pass", "run"), !is.na(pass_oe))

# Neutral game script: win prob 20-80%, not a garbage-time 4th-quarter blowout.
# This isolates "what does this playcaller actually want to do" from
# score-driven pass/run forcing late in games.
neutral <- plays %>%
  filter(wp >= 0.20, wp <= 0.80)

team_season <- plays %>%
  group_by(posteam, season) %>%
  summarise(
    n_plays          = n(),
    proe_avg          = mean(pass_oe, na.rm = TRUE),
    early_down_pass_rate = mean(pass[down %in% c(1, 2)], na.rm = TRUE),
    redzone_pass_rate = mean(pass[yardline_100 <= 20], na.rm = TRUE),
    .groups = "drop"
  )

neutral_season <- neutral %>%
  group_by(posteam, season) %>%
  summarise(
    n_neutral_plays = n(),
    proe_neutral     = mean(pass_oe, na.rm = TRUE),
    .groups = "drop"
  )

# Pace: plays run per game (offensive snaps / games played that season by that team)
games_played <- pbp %>%
  filter(!is.na(posteam)) %>%
  distinct(posteam, season, game_id) %>%
  count(posteam, season, name = "games")

pace <- plays %>%
  count(posteam, season, name = "off_plays") %>%
  left_join(games_played, by = c("posteam", "season")) %>%
  mutate(plays_per_game = off_plays / games)

scheme <- team_season %>%
  left_join(neutral_season, by = c("posteam", "season")) %>%
  left_join(pace %>% select(posteam, season, plays_per_game), by = c("posteam", "season")) %>%
  rename(team = posteam) %>%
  arrange(team, season)

out_path <- file.path(OUT_DIR, "team_scheme_features.csv")
write.csv(scheme, out_path, row.names = FALSE)
cat("Wrote:", out_path, "\n")
cat("Rows:", nrow(scheme), "\n\n")

cat("=== Sample: 2024 PROE (neutral script) leaders/laggards ===\n")
sample24 <- scheme %>% filter(season == 2024) %>% arrange(desc(proe_neutral))
print(head(sample24 %>% select(team, proe_neutral, plays_per_game, redzone_pass_rate), 5))
cat("...\n")
print(tail(sample24 %>% select(team, proe_neutral, plays_per_game, redzone_pass_rate), 5))
