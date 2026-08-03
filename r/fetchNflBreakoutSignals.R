#!/usr/bin/env Rscript
# fetchNflBreakoutSignals.R
# Phase 5: raw signal inputs for the 2nd/3rd-year breakout candidate score.
# Three outputs, all keyed off 2025 season data (the last full season before
# the 2026 draft):
#   1. breakout_efficiency_2025.csv - per-player NGS rate efficiency (position-
#      specific: QB CPOE/EPA, RB RYOE/efficiency, WR/TE separation+YAC-oe),
#      sample-gated so small-sample-size players don't get spurious extreme
#      efficiency reads.
#   2. breakout_usage_trend_2025.csv - each player's snap_share/target_share
#      in the back half of 2025 (weeks 10-18) minus the front half (weeks
#      1-9). Trend, not level - a player finishing hot on volume is a better
#      bet than one flat all year even at the same season-average share.
#   3. breakout_team_vacancy_2026.csv - per 2025 team, how much target_share
#      (pass-catchers) and rush_attempt share (backs) is walking out the door
#      via free agency/retirement/cut before 2026, using the 2026 roster
#      (ACT status) as "who's still here." This is the situational-tailwind
#      signal - a team losing its top target earner opens a real, measurable
#      opportunity for whoever's left.
#
# Usage: Rscript fetchNflBreakoutSignals.R
suppressMessages({
  library(nflreadr)
  library(dplyr)
  library(tidyr)
  library(readr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

SEASON <- 2025

# ── 1. NGS rate efficiency, position-specific, sample-gated ────────────────
# Sample gates mirror the existing nflXxStats.R leaderboard scripts so a
# player showing up here has met the same "real signal, not noise" bar used
# everywhere else in this pipeline.
cat("Loading NGS passing/rushing/receiving for", SEASON, "...\n")
# NOTE: week == 0 in nflreadr's NGS tables is a pre-aggregated SEASON TOTAL
# row, not a real week - summing/averaging across all rows without excluding
# it silently double-counts every player's season (discovered while building
# this script: Mahomes week 0 attempts == sum of his weeks 1-15 exactly).
# Every NGS load below filters week > 0 for this reason.
ngs_pass <- load_nextgen_stats(seasons = SEASON, stat_type = "passing") %>%
  filter(season_type == "REG", player_position == "QB", week > 0) %>%
  group_by(player_gsis_id) %>%
  summarise(
    att            = sum(attempts, na.rm = TRUE),
    cpoe_ngs       = mean(completion_percentage_above_expectation, na.rm = TRUE),
    aggressiveness = mean(aggressiveness, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(att >= 100) %>%
  transmute(player_id = player_gsis_id, position = "QB",
            efficiency_primary = cpoe_ngs, efficiency_secondary = aggressiveness,
            sample_n = att)

ngs_rush <- load_nextgen_stats(seasons = SEASON, stat_type = "rushing") %>%
  filter(season_type == "REG", player_position == "RB", week > 0) %>%
  group_by(player_gsis_id) %>%
  summarise(
    att          = sum(rush_attempts, na.rm = TRUE),
    ryoe_per_att = mean(rush_yards_over_expected_per_att, na.rm = TRUE),
    efficiency   = mean(efficiency, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(att >= 50) %>%
  transmute(player_id = player_gsis_id, position = "RB",
            efficiency_primary = ryoe_per_att, efficiency_secondary = efficiency,
            sample_n = att)

ngs_recv <- load_nextgen_stats(seasons = SEASON, stat_type = "receiving") %>%
  filter(season_type == "REG", player_position %in% c("WR", "TE"), week > 0) %>%
  group_by(player_gsis_id, player_position) %>%
  summarise(
    tgts       = sum(targets, na.rm = TRUE),
    avg_yac_oe = mean(avg_yac_above_expectation, na.rm = TRUE),
    avg_sep    = mean(avg_separation, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter((player_position == "WR" & tgts >= 30) | (player_position == "TE" & tgts >= 20)) %>%
  transmute(player_id = player_gsis_id, position = player_position,
            efficiency_primary = avg_yac_oe, efficiency_secondary = avg_sep,
            sample_n = tgts)

efficiency <- bind_rows(ngs_pass, ngs_rush, ngs_recv)

out_eff <- file.path(OUT_DIR, "breakout_efficiency_2025.csv")
write_csv(efficiency, out_eff)
cat("Wrote:", out_eff, "(", nrow(efficiency), "rows )\n\n")

# ── 2. Usage trend: back-half vs front-half of 2025 season ─────────────────
cat("Loading weekly snap counts + player stats for usage trend...\n")
snaps_wk <- load_snap_counts(SEASON) %>%
  filter(offense_snaps > 0) %>%
  mutate(half = ifelse(week <= 9, "front", "back")) %>%
  group_by(pfr_player_id, position, half) %>%
  summarise(snap_share = mean(offense_pct, na.rm = TRUE), .groups = "drop") %>%
  pivot_wider(names_from = half, values_from = snap_share, names_prefix = "snap_") %>%
  mutate(snap_share_trend = snap_back - snap_front) %>%
  select(pfr_player_id, position, snap_share_trend)

stats_wk <- load_player_stats(seasons = SEASON) %>%
  filter(!is.na(team), targets > 0) %>%
  mutate(half = ifelse(week <= 9, "front", "back")) %>%
  group_by(player_id, player_display_name, position, half) %>%
  summarise(target_share = weighted.mean(target_share, w = pmax(targets, 1), na.rm = TRUE),
            .groups = "drop") %>%
  pivot_wider(names_from = half, values_from = target_share, names_prefix = "ts_") %>%
  mutate(target_share_trend = ts_back - ts_front) %>%
  select(player_id, player_display_name, position, target_share_trend)

# Bridge pfr_player_id (snap counts) to gsis player_id (player_stats/everything
# else in this pipeline) via nflreadr's player id crosswalk - same convention
# problem mergeNflPhase1Features.R solves with name normalization, but here we
# have a real id crosswalk table available so use that instead (more robust
# than name matching across two different tables again).
ids <- load_players() %>% select(gsis_id, pfr_id) %>% filter(!is.na(pfr_id), !is.na(gsis_id))
snaps_wk <- snaps_wk %>% left_join(ids, by = c("pfr_player_id" = "pfr_id"))

usage_trend <- stats_wk %>%
  full_join(snaps_wk %>% select(gsis_id, position, snap_share_trend),
            by = c("player_id" = "gsis_id", "position")) %>%
  distinct(player_id, position, .keep_all = TRUE)

out_trend <- file.path(OUT_DIR, "breakout_usage_trend_2025.csv")
write_csv(usage_trend, out_trend)
cat("Wrote:", out_trend, "(", nrow(usage_trend), "rows )\n\n")

# ── 3. Team vacancy: who left before 2026 ───────────────────────────────────
cat("Loading 2025 phase1 usage + 2026 rosters for vacancy calc...\n")
phase1 <- read_csv(file.path(OUT_DIR, "nfl_phase1_features.csv"), show_col_types = FALSE) %>%
  filter(season == SEASON)

roster_2026 <- load_rosters(2026) %>%
  filter(status == "ACT") %>%
  distinct(gsis_id) %>%
  pull(gsis_id)

vacancy <- phase1 %>%
  mutate(still_here_2026 = player_id %in% roster_2026) %>%
  group_by(team) %>%
  summarise(
    target_share_vacated = sum(target_share[!still_here_2026 & position %in% c("WR","TE","RB")], na.rm = TRUE),
    rush_att_vacated      = sum(rush_attempts[!still_here_2026 & position == "RB"], na.rm = TRUE),
    team_rush_att_total    = sum(rush_attempts[position == "RB"], na.rm = TRUE),
    .groups = "drop"
  ) %>%
  mutate(rush_share_vacated = ifelse(team_rush_att_total > 0,
                                      rush_att_vacated / team_rush_att_total, 0))

out_vac <- file.path(OUT_DIR, "breakout_team_vacancy_2026.csv")
write_csv(vacancy, out_vac)
cat("Wrote:", out_vac, "(", nrow(vacancy), "rows )\n\n")

cat("=== Top 8 teams by target_share_vacated (biggest pass-game opportunity opening up) ===\n")
print(vacancy %>% arrange(desc(target_share_vacated)) %>% select(team, target_share_vacated, rush_share_vacated) %>% head(8))
