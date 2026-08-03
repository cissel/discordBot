#!/usr/bin/env Rscript
# fetchNflRookieData.R
# Rookie prior data: draft capital + combine athletic testing + landing-spot
# opportunity, for players with NO NFL usage history (can't use the normal
# lag1-usage Phase 2 model). Covers 2015-2026 draft classes for training +
# scoring the 2026 rookie class.
#
# NOTE ON DATA SOURCE: originally planned to use final college-season box
# score stats (College Dominator Rating, breakout age - the strongest known
# WR predictor per public research) via cfbfastR/CollegeFootballData.com, but
# CFBD now requires a free API key for registration James didn't want to set
# up mid-session. WORKED AROUND with nflreadr::load_combine() (NFL Scouting
# Combine athletic testing: 40-yard dash, vertical, broad jump, bench, cone,
# shuttle) instead - this is an established substitute used in real fantasy
# analytics (RotoViz Speed Score, PlayerProfiler Burst Score) when box score
# data isn't available. Coverage: 74-99% depending on metric, no API key
# needed. Revisit with real college dominator/breakout-age data if James
# registers for a CFBD key later - it's the stronger WR signal per public
# research, this combine-based version is the honest fallback.
#
# Usage: Rscript fetchNflRookieData.R
suppressMessages({
  library(nflreadr)
  library(dplyr)
  library(tidyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

SEASONS <- 2015:2026

cat("Fetching draft picks", min(SEASONS), "-", max(SEASONS), "...\n")
draft <- load_draft_picks(SEASONS)
cat("  ->", nrow(draft), "draft picks\n")

cat("Fetching combine data...\n")
combine <- load_combine(SEASONS)
cat("  ->", nrow(combine), "combine entries\n")

# Speed Score (Bill Barnwell's formula, standard in the industry): normalizes
# 40-time by weight since a fast 220lb RB is more impressive than a fast
# 190lb WR at the same time. Higher = better explosiveness-adjusted speed.
# Burst Score (PlayerProfiler convention): vertical + broad_jump, explosiveness.
combine <- combine %>%
  mutate(
    speed_score = ifelse(!is.na(forty) & !is.na(wt) & forty > 0,
                          (wt * 200) / (forty^4), NA_real_),
    burst_score = ifelse(!is.na(vertical) & !is.na(broad_jump),
                          vertical + (broad_jump / 12), NA_real_)  # broad_jump in inches -> feet
  )

# Join draft capital with combine by cfb_id / cfb_player_id (both nflverse
# sources use the same PFR-derived college-football ID scheme).
# CRITICAL: many players have NA cfb_player_id/cfb_id (didn't attend combine
# or ID wasn't captured) - a plain left_join() treats NA == NA as a match,
# causing a massive cross-product fan-out (every NA-id draft pick joins to
# EVERY NA-id combine row). Fix: only join rows where BOTH ids are non-NA,
# then bind back the non-matchable rows with combine columns left NA.
draft_with_id <- draft %>%
  filter(position %in% c("QB", "RB", "WR", "TE")) %>%
  select(season, round, pick, gsis_id, pfr_player_name, position, college, cfb_player_id)

combine_dedup <- combine %>%
  filter(!is.na(cfb_id)) %>%
  distinct(cfb_id, .keep_all = TRUE) %>%  # 2 known dupe cfb_ids (multi-position combine entries) - keep first
  select(cfb_id, forty, bench, vertical, broad_jump, cone, shuttle,
         speed_score, burst_score, ht, wt)

has_id <- draft_with_id %>% filter(!is.na(cfb_player_id))
no_id  <- draft_with_id %>% filter(is.na(cfb_player_id))

matched <- has_id %>% left_join(combine_dedup, by = c("cfb_player_id" = "cfb_id"))
unmatched <- no_id %>%
  mutate(forty = NA_real_, bench = NA_real_, vertical = NA_real_, broad_jump = NA_real_,
         cone = NA_real_, shuttle = NA_real_, speed_score = NA_real_, burst_score = NA_real_,
         ht = NA_character_, wt = NA_real_)

rookie_base <- bind_rows(matched, unmatched)

cat("\nRookie base rows:", nrow(rookie_base), "\n")
cat("Combine match rate:", sprintf("%.1f%%", 100 * mean(!is.na(rookie_base$forty))), "\n")
cat("Position breakdown:\n")
print(table(rookie_base$position))

out_path <- file.path(OUT_DIR, "rookie_draft_combine.csv")
write.csv(rookie_base, out_path, row.names = FALSE)
cat("\nWrote:", out_path, "\n")
