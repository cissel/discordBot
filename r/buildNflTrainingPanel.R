#!/usr/bin/env Rscript
# buildNflTrainingPanel.R
# Builds the Phase 2 training panel: predicts season T fantasy points using
# only information KNOWN BEFORE season T starts -
#   - player's role/usage from season T-1 (target share, snap share, volume)
#   - player's age + aging-curve position curve for season T
#   - season T's coaching situation (coach scheme signature is already built
#     from strictly-prior seasons, new_hc/new_oc flags, scheme_uncertainty_flag)
#   - draft capital / experience (static)
# Target: room40_pts, room40_pts_per_game in season T.
#
# This mirrors the MLB pipeline's leakage-prevention convention (shift(1)
# before rolling) - here the "rolling window" is just "last season", so we
# lag every usage feature by one season before joining to the target season.
suppressMessages({
  library(dplyr)
  library(readr)
  library(tidyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")

career <- read_csv(file.path(OUT_DIR, "career_panel.csv"), show_col_types = FALSE)
phase1_raw <- read_csv(file.path(OUT_DIR, "nfl_phase1_features.csv"), show_col_types = FALSE)
aging  <- read_csv(file.path(OUT_DIR, "aging_curves.csv"), show_col_types = FALSE)

# Mid-season trades produce >1 team row per player-season (~3.6% of skill-position
# rows, e.g. Adrian Peterson SEA->TEN 2021). Collapse to one row per player-season:
# take the team where they logged the most offensive snaps that year (their primary
# team), so usage/coach features attach cleanly with no fan-out in later joins.
phase1 <- phase1_raw %>%
  mutate(.snaps_rank = coalesce(games_with_snaps, 0)) %>%
  group_by(player_id, season) %>%
  slice_max(.snaps_rank, n = 1, with_ties = FALSE) %>%
  ungroup() %>%
  select(-.snaps_rank)

# ---- lagged usage features: season T-1 role -> attached to season T row ----
usage_lag <- phase1 %>%
  select(player_id, position, season, targets, receptions, rec_yards, air_yards,
         rush_attempts, target_share, air_yards_share, games_with_snaps, snap_share_avg) %>%
  mutate(season = season + 1) %>%  # shift forward: this becomes "last year's role" for season+1
  rename_with(~paste0("lag1_", .), -c(player_id, position, season))

# ---- lagged fantasy output: season T-1 room40_pts / pts_per_game (baseline signal) ----
output_lag <- career %>%
  select(player_id, position, season, room40_pts, pts_per_game, games) %>%
  mutate(season = season + 1) %>%
  rename(lag1_room40_pts = room40_pts, lag1_pts_per_game = pts_per_game, lag1_games = games)

# ---- team-change flag: did this player change teams from the PRIOR season? ----
# Motivation (found 2026-08 during a live-draft-tool review flagging
# Wan'Dale Robinson as a suspiciously overvalued recommendation): usage
# features (lag1_target_share, lag1_snap_share_avg) are carried forward at
# face value regardless of whether the player is still on the SAME team
# that earned them that role. A player who earned a 32% target share as
# their old team's clear WR1 does not automatically keep that role after
# changing teams into a crowded depth chart - but nothing in the model
# previously captured that. Empirically verified this is a REAL, material
# effect (not just this one player): among WR/TE with lag1_target_share >
# 0.20, team-changers saw their share erode much further next season
# (24.7% -> 19.6%) than players who stayed (25.7% -> 23.6%), and scored
# meaningfully fewer points per game (9.5 vs 12.2 pts/game). Let the model
# LEARN the right discount empirically via this flag + its interaction
# with lag1_target_share, rather than hand-picking a penalty multiplier.
team_change <- phase1 %>%
  select(player_id, season, team) %>%
  distinct() %>%
  arrange(player_id, season) %>%
  group_by(player_id) %>%
  mutate(prev_team = lag(team)) %>%
  ungroup() %>%
  mutate(team_changed = !is.na(prev_team) & team != prev_team) %>%
  select(player_id, season, team_changed)
# NOTE: no season-shift here, unlike usage_lag/output_lag above. team_changed
# is already computed AT the season where the move took effect (team[S] vs
# team[S-1]), which is exactly the panel's target/output season - joining
# this directly (not shifted) correctly answers "did this player change
# teams going into the season we're predicting."

# ---- season T coaching situation (already leakage-safe: signature built from
#      strictly-prior seasons at build time) ----
coach_now <- phase1 %>%
  select(player_id, season, new_oc, coach_proe_signature, coach_pace_signature,
         coach_rz_signature, coach_n_prior_seasons, scheme_uncertainty_flag) %>%
  distinct()

# ---- aging curve lookup: position + rounded age -> combined_index ----
aging_lookup <- aging %>% select(position, age, combined_index)

panel <- career %>%
  select(player_id, player_display_name, position, season, games, room40_pts,
         pts_per_game, age, draft_number, years_exp) %>%
  inner_join(usage_lag, by = c("player_id", "position", "season")) %>%
  left_join(output_lag, by = c("player_id", "position", "season")) %>%
  left_join(team_change, by = c("player_id", "season")) %>%
  left_join(coach_now, by = c("player_id", "season")) %>%
  mutate(age_round = round(age)) %>%
  left_join(aging_lookup, by = c("position", "age_round" = "age")) %>%
  select(-age_round) %>%
  filter(games >= 1, season >= 2019, season <= 2025) %>%
  # team_changed unknown only for a player's very first tracked season (no
  # prior team to compare against) - treat as "no change" (0), the neutral/
  # majority-class default, rather than dropping these rows.
  mutate(team_changed = coalesce(team_changed, FALSE)) %>%
  # interaction feature: the team-change PENALTY should scale with how much
  # role there was to lose. A change with a tiny lag1_target_share has
  # little to erode; a change with a 32% target share (an entrenched WR1
  # role) has a lot. Let the model learn the discount slope directly rather
  # than hand-picking a flat penalty.
  mutate(team_changed_x_target_share = as.numeric(team_changed) * lag1_target_share) %>%
  arrange(player_id, season)

cat("Training panel rows:", nrow(panel), "\n")
cat("Season range:", min(panel$season), "-", max(panel$season), "\n")
cat("Position breakdown:\n")
print(table(panel$position))
cat("\nMissing coach_proe_signature (no coach match):", sum(is.na(panel$coach_proe_signature)), "\n")
cat("Missing lag1_room40_pts (rookie/no prior season role data):", sum(is.na(panel$lag1_room40_pts)), "\n")
cat("Missing combined_index (aging curve, age out of 20-39 range):", sum(is.na(panel$combined_index)), "\n")
cat("team_changed rate:", round(mean(panel$team_changed), 4), "\n")

out_path <- file.path(OUT_DIR, "nfl_training_panel.csv")
write_csv(panel, out_path)
cat("\nWrote:", out_path, "\n")
