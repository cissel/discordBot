#!/usr/bin/env Rscript
# mergeNflPhase1Features.R
# Final merge: player-season skill-position features (target share, snap share)
# joined with their team-season's coach scheme signature (PROE/pace/red-zone,
# attributed to the coordinator's career identity, not just team continuity).
# This is the complete Phase 1 feature table for the NFL moneyball project -
# ready to feed Phase 2 (projection model).
suppressMessages({
  library(dplyr)
  library(readr)
  library(stringr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")

snap_share   <- read_csv(file.path(OUT_DIR, "player_snap_share.csv"), show_col_types = FALSE)
target_share <- read_csv(file.path(OUT_DIR, "player_target_share.csv"), show_col_types = FALSE)
coach_sig    <- read_csv(file.path(OUT_DIR, "coach_scheme_signature.csv"), show_col_types = FALSE)

# NAME NORMALIZATION: snap_share is sourced from PFR (via nflreadr snap
# counts, "player" col e.g. "D.K. Metcalf") and target_share is sourced from
# nflverse play-by-play rosters ("player_display_name" col e.g. "DK Metcalf",
# with suffix conventions like "Chris Godwin Jr." vs snap's "Chris Godwin").
# An EXACT string join here silently splits one real player into two
# incomplete rows whenever the two sources format a name differently -
# confirmed 2026-07-29: 153 player-seasons across 2018-2025 affected (28 in
# 2025 alone - DK Metcalf, Chris Godwin, DJ Moore, Deebo Samuel, Michael
# Pittman among them), each showing up as a target_share-only row (no snap
# data) AND a separate snap_share-only row (no target data) instead of one
# complete row. Both get silently dropped downstream by buildNfl2026Projections.py's
# core-feature dropna(), so those players vanish from the VORP board entirely
# with no error or warning. Fix: join on a normalized name key (strip
# periods, strip Jr./Sr./II/III/IV suffixes, lowercase) instead of the raw
# string, in addition to team/season/position.
normalize_name <- function(x) {
  x <- str_to_lower(x)
  x <- str_remove_all(x, "\\.")                              # "d.k." -> "dk"
  x <- str_remove(x, "\\s+(jr|sr|ii|iii|iv|v)\\.?\\s*$")      # strip suffix
  x <- str_squish(x)
  x
}

target_share <- target_share %>% mutate(.norm_name = normalize_name(player_display_name))
snap_share   <- snap_share   %>% mutate(.norm_name = normalize_name(player))

# Player base: union of anyone appearing in either snap or target data
# (snap covers all positions, target covers pass-catchers + volume-relevant skill positions)
players <- target_share %>%
  select(player_display_name, player_id, team, season, position, .norm_name,
         targets, receptions, rec_yards, air_yards, rush_attempts,
         target_share, air_yards_share) %>%
  full_join(
    snap_share %>%
      select(.norm_name, team, season, position,
             games_with_snaps, snap_share_avg, snap_share_max),
    by = c(".norm_name", "team", "season", "position")
  ) %>%
  select(-.norm_name)

merged <- players %>%
  left_join(
    coach_sig %>%
      select(team, season, head_coach, oc, new_oc,
             proe_neutral, plays_per_game, redzone_pass_rate,
             coach_proe_signature, coach_pace_signature, coach_rz_signature,
             coach_n_prior_seasons, coach_signature_is_imputed, scheme_uncertainty_flag,
             confidence),
    by = c("team", "season")
  )

out_path <- file.path(OUT_DIR, "nfl_phase1_features.csv")
write_csv(merged %>% arrange(team, season, desc(target_share)), out_path)

cat("Wrote:", out_path, "\n")
cat("Total rows:", nrow(merged), "\n")
cat("Rows with coach signature attached:", sum(!is.na(merged$coach_proe_signature)), "\n")
cat("Distinct players:", n_distinct(merged$player_display_name), "\n")
cat("Season range:", min(merged$season), "-", max(merged$season), "\n")
cat("Rows with BOTH target_share and snap_share_avg populated:",
    sum(!is.na(merged$target_share) & !is.na(merged$snap_share_avg)), "\n\n")

cat("=== Sanity check: 2024 top-8 WR/TE by target share, joined with team scheme ===\n")
check <- merged %>%
  filter(season == 2024, position %in% c("WR","TE"), targets >= 60) %>%
  arrange(desc(target_share)) %>%
  select(player_display_name, team, position, target_share, snap_share_avg,
         coach_proe_signature, scheme_uncertainty_flag) %>%
  head(8)
print(check)

cat("\n=== Verification: previously name-mismatch-split players now complete ===\n")
verify_names <- c("DK Metcalf", "Chris Godwin Jr.", "DJ Moore", "Deebo Samuel Sr.", "Michael Pittman")
verify <- merged %>%
  filter(season == 2025, player_display_name %in% verify_names) %>%
  select(player_display_name, team, targets, games_with_snaps, snap_share_avg)
print(verify)

