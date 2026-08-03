#!/usr/bin/env Rscript
# buildNflCoachSignature.R
# Builds a coach-level historical scheme signature (career PROE, pace, red zone
# pass rate) that TRAVELS with a coordinator/HC across team changes, instead of
# being reset to league-average whenever a team hires someone new. This is the
# whole point of tracking coach identity rather than just team continuity.
#
# Method: join coaching_staff.csv (who called plays for which team-season) with
# team_scheme_features.csv (what that team-season's play-calling actually looked
# like), grouped by OC name. For a given team-season, the "coach signature" is
# the OC's CAREER-TO-DATE (prior seasons only, no leakage) games-weighted average
# scheme stats from every team he's coordinated for. First season for any OC has
# no prior history -> falls back to league-average PROE that year (documented in
# SPARSE_FEATURES pattern, same as MLB pipeline's day-of features).
#
# Usage: Rscript buildNflCoachSignature.R
suppressMessages({
  library(dplyr)
  library(readr)
  library(tidyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")

staff  <- read_csv(file.path(OUT_DIR, "coaching_staff.csv"), show_col_types = FALSE)
scheme <- read_csv(file.path(OUT_DIR, "team_scheme_features.csv"), show_col_types = FALSE)

# Join: for each team-season, what did THAT team's offense actually do, and who
# was the OC/HC calling it. Use OC as primary offensive playcaller attribution
# (standard assumption unless HC is known to call plays himself - we don't have
# that granularity here, so OC is the consistent proxy across all team-seasons).
joined <- staff %>%
  select(team, season, head_coach, oc, new_oc, confidence) %>%
  inner_join(scheme, by = c("team", "season")) %>%
  arrange(oc, season)

league_avg <- scheme %>%
  group_by(season) %>%
  summarise(
    league_proe_neutral = mean(proe_neutral, na.rm = TRUE),
    league_plays_per_game = mean(plays_per_game, na.rm = TRUE),
    league_redzone_pass_rate = mean(redzone_pass_rate, na.rm = TRUE),
    .groups = "drop"
  )

# For each OC, at each season, compute career-to-date (STRICTLY PRIOR seasons,
# across ALL teams they've coordinated for) games-weighted average scheme stats.
# This is what lets "run-heavy OC changes teams" carry his tendency with him.
compute_coach_history <- function(oc_name, current_season, data) {
  prior <- data %>% filter(oc == oc_name, season < current_season)
  if (nrow(prior) == 0) return(c(proe = NA_real_, pace = NA_real_, rz = NA_real_, n_seasons = 0))
  c(
    proe      = weighted.mean(prior$proe_neutral, w = prior$n_neutral_plays, na.rm = TRUE),
    pace      = weighted.mean(prior$plays_per_game, w = prior$n_plays, na.rm = TRUE),
    rz        = weighted.mean(prior$redzone_pass_rate, w = prior$n_plays, na.rm = TRUE),
    n_seasons = nrow(prior)
  )
}

coach_sig <- joined %>%
  rowwise() %>%
  mutate(
    hist = list(compute_coach_history(oc, season, joined))
  ) %>%
  ungroup() %>%
  mutate(
    coach_proe_career     = sapply(hist, function(h) h["proe"]),
    coach_pace_career     = sapply(hist, function(h) h["pace"]),
    coach_rz_career       = sapply(hist, function(h) h["rz"]),
    coach_n_prior_seasons = sapply(hist, function(h) h["n_seasons"])
  ) %>%
  select(-hist) %>%
  left_join(league_avg, by = "season") %>%
  mutate(
    # Fallback: rookie OC (0 prior seasons) gets league average that year instead
    # of NA - same sparse-feature-imputation pattern as the MLB day-of pipeline.
    coach_proe_signature = ifelse(is.na(coach_proe_career), league_proe_neutral, coach_proe_career),
    coach_pace_signature  = ifelse(is.na(coach_pace_career), league_plays_per_game, coach_pace_career),
    coach_rz_signature    = ifelse(is.na(coach_rz_career), league_redzone_pass_rate, coach_rz_career),
    coach_signature_is_imputed = is.na(coach_proe_career),
    # Scheme discontinuity risk flag: new OC this season AND he has no relevant
    # prior history to anchor a signature to (pure rookie coordinator) - these
    # are the team-seasons where scheme prediction is most uncertain.
    scheme_uncertainty_flag = new_oc == 1 & coach_n_prior_seasons == 0
  )

out_path <- file.path(OUT_DIR, "coach_scheme_signature.csv")
write_csv(coach_sig %>% arrange(team, season), out_path)

cat("Wrote:", out_path, "\n")
cat("Rows:", nrow(coach_sig), "\n")
cat("Rookie-OC (imputed signature) rows:", sum(coach_sig$coach_signature_is_imputed), "\n")
cat("Scheme uncertainty flag rows:", sum(coach_sig$scheme_uncertainty_flag), "\n\n")

cat("=== Example: coach signature travels across team moves ===\n")
example_ocs <- coach_sig %>%
  group_by(oc) %>%
  filter(n_distinct(team) > 1) %>%
  ungroup() %>%
  distinct(oc) %>%
  head(3) %>%
  pull(oc)

if (length(example_ocs) > 0) {
  print(coach_sig %>%
    filter(oc %in% example_ocs) %>%
    select(oc, team, season, proe_neutral, coach_proe_signature, coach_n_prior_seasons) %>%
    arrange(oc, season))
} else {
  cat("(No OC with multi-team history found in this compiled sample - expected, coordinator\n")
  cat(" moves are relatively rare events; the mechanism is still correctly wired for when they occur.)\n")
}
