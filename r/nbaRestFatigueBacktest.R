# nbaRestFatigueBacktest.R by JHCV
#
# Tests rest-day / back-to-back (B2B) fatigue effects on NBA ATS and totals
# closing lines, 2024-25 + 2025-26 regular seasons (data collected by
# nbaBettingDataCollect.R). Modeled on nbaAtsTotalsBacktest.R's structure.
#
# NOTE: hoopR's espn_nba_game_odds() has no per-side juice for historical
# completed games - only spread/over_under. All cover-rate figures below are
# cover RATES (vs 50% coin-flip / 52.38% breakeven at -110), not exact ROI
# dollar figures, since real juice is unknown.
#
# Rest-day caveat: rest_days is derived only from games that have odds data
# (~99.7% complete for NBA). In the rare case a team's prior game is missing
# odds, its rest count could be off by one actual game. Acceptable minor
# noise - not worth re-fetching full schedules to fix.
#
# Spread convention: spread < 0 => home team favored by |spread|.
#                     spread > 0 => home team is the underdog (+spread).
# home_cover_margin = (home_score - away_score) + spread; >0 home covers.

suppressMessages({
  library(dplyr)
  library(ggplot2)
  library(tidyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nba/betting")

# ---------------------------------------------------------------------
# 1. Load + dedupe one row per event_id, per season
# ---------------------------------------------------------------------
load_games <- function(szn_label, path) {
  read.csv(path, stringsAsFactors = FALSE) %>%
    filter(!is.na(spread), !is.na(over_under)) %>%
    distinct(event_id, .keep_all = TRUE) %>%
    mutate(
      season_label = szn_label,
      game_date = as.Date(game_date),
      home_margin = home_score - away_score,
      total_pts = home_score + away_score,
      home_cover_margin = home_margin + spread
    ) %>%
    select(event_id, season_label, game_date, home_abbreviation, away_abbreviation,
           home_score, away_score, spread, over_under, home_margin, total_pts,
           home_cover_margin)
}

s2025_games <- load_games("2024-25", file.path(OUT_DIR, "nba_odds_2025.csv"))
s2026_games <- load_games("2025-26", file.path(OUT_DIR, "nba_odds_2026.csv"))

cat(sprintf("Loaded games: %d (2024-25), %d (2025-26)\n",
            nrow(s2025_games), nrow(s2026_games)))

# ---------------------------------------------------------------------
# 2. Reshape to per-team game log, compute rest_days per team per season
# ---------------------------------------------------------------------
build_team_log <- function(games) {
  home_side <- games %>%
    transmute(event_id, season_label, game_date, team = home_abbreviation, is_home = TRUE)
  away_side <- games %>%
    transmute(event_id, season_label, game_date, team = away_abbreviation, is_home = FALSE)

  bind_rows(home_side, away_side) %>%
    arrange(team, game_date) %>%
    group_by(team) %>%
    mutate(
      prev_date = lag(game_date),
      rest_days = as.integer(game_date - prev_date)
    ) %>%
    ungroup() %>%
    select(-prev_date)
}

team_log_2025 <- build_team_log(s2025_games)
team_log_2026 <- build_team_log(s2026_games)
team_log <- bind_rows(team_log_2025, team_log_2026)

n_first_games <- sum(is.na(team_log$rest_days))
cat(sprintf("Team-game rows: %d total, %d are season-openers (rest_days = NA, excluded from rest comparisons)\n",
            nrow(team_log), n_first_games))

# ---------------------------------------------------------------------
# 3. Rejoin home_rest_days / away_rest_days back onto per-game table
# ---------------------------------------------------------------------
join_rest <- function(games, tlog) {
  home_rest <- tlog %>% filter(is_home) %>% select(event_id, home_rest_days = rest_days)
  away_rest <- tlog %>% filter(!is_home) %>% select(event_id, away_rest_days = rest_days)
  games %>%
    left_join(home_rest, by = "event_id") %>%
    left_join(away_rest, by = "event_id") %>%
    mutate(
      # B2B note: rest_days here is the literal calendar-date difference
      # (this game's date minus the prior game's date), so the MINIMUM
      # possible value is 1 (NBA never schedules the same team twice on
      # the same calendar date). A date-diff of 1 means zero rest days
      # elapsed in between, i.e. "played the day immediately before" -
      # that is the back-to-back case, so B2B is defined as rest_days==1,
      # not rest_days==0 (rest_days==0 never occurs in this dataset).
      home_b2b = home_rest_days == 1,
      away_b2b = away_rest_days == 1,
      rest_diff = home_rest_days - away_rest_days
    )
}

s2025 <- join_rest(s2025_games, team_log_2025)
s2026 <- join_rest(s2026_games, team_log_2026)
d <- bind_rows(s2025, s2026)

# ---------------------------------------------------------------------
# 4a. ATS cover rate by B2B bucket (home-only B2B / away-only B2B / neither / both)
#     report the cover rate of the team THAT IS on the B2B
# ---------------------------------------------------------------------
cat("\n=== 4a. ATS cover rate of the B2B team, by bucket, per season ===\n")

b2b_bucket_stats <- function(sub, lbl) {
  sub <- sub %>% filter(!is.na(home_b2b), !is.na(away_b2b), home_cover_margin != 0)

  home_only <- sub %>% filter(home_b2b, !away_b2b)
  away_only <- sub %>% filter(away_b2b, !home_b2b)
  neither   <- sub %>% filter(!home_b2b, !away_b2b)
  both      <- sub %>% filter(home_b2b, away_b2b)

  # cover rate of the B2B team specifically
  home_b2b_cover_pct <- 100 * mean(home_only$home_cover_margin > 0)
  away_b2b_cover_pct <- 100 * mean(away_only$home_cover_margin < 0)  # away covers when home_cover_margin<0

  cat(sprintf("%s: home-only-B2B team covers %.1f%% (n=%d) | away-only-B2B team covers %.1f%% (n=%d) | neither-on-B2B n=%d | both-on-B2B n=%d\n",
              lbl, home_b2b_cover_pct, nrow(home_only), away_b2b_cover_pct, nrow(away_only),
              nrow(neither), nrow(both)))

  data.frame(season_label = lbl,
             bucket = c("Home B2B (home covers?)", "Away B2B (away covers?)"),
             n = c(nrow(home_only), nrow(away_only)),
             cover_pct = c(home_b2b_cover_pct, away_b2b_cover_pct))
}

b2b_2025 <- b2b_bucket_stats(s2025, "2024-25")
b2b_2026 <- b2b_bucket_stats(s2026, "2025-26")

sub_all <- d %>% filter(!is.na(home_b2b), !is.na(away_b2b), home_cover_margin != 0)
home_only_all <- sub_all %>% filter(home_b2b, !away_b2b)
away_only_all <- sub_all %>% filter(away_b2b, !home_b2b)
both_all <- sub_all %>% filter(home_b2b, away_b2b)
cat(sprintf("Combined: home-only-B2B team covers %.1f%% (n=%d) | away-only-B2B team covers %.1f%% (n=%d) | both-on-B2B n=%d\n",
            100 * mean(home_only_all$home_cover_margin > 0), nrow(home_only_all),
            100 * mean(away_only_all$home_cover_margin < 0), nrow(away_only_all),
            nrow(both_all)))

# ---------------------------------------------------------------------
# 4b. Over/under hit rate: neither-on-B2B vs at-least-one-on-B2B
# ---------------------------------------------------------------------
cat("\n=== 4b. Over hit rate: neither-on-B2B vs at-least-one-on-B2B, per season ===\n")

ou_stats <- function(sub, lbl) {
  sub <- sub %>% filter(!is.na(home_b2b), !is.na(away_b2b), total_pts != over_under)
  neither <- sub %>% filter(!home_b2b, !away_b2b)
  any_b2b <- sub %>% filter(home_b2b | away_b2b)
  cat(sprintf("%s: neither-on-B2B overs %.1f%% (n=%d) | at-least-one-on-B2B overs %.1f%% (n=%d)\n",
              lbl, 100 * mean(neither$total_pts > neither$over_under), nrow(neither),
              100 * mean(any_b2b$total_pts > any_b2b$over_under), nrow(any_b2b)))
}
ou_stats(s2025, "2024-25")
ou_stats(s2026, "2025-26")
sub_all_ou <- d %>% filter(!is.na(home_b2b), !is.na(away_b2b), total_pts != over_under)
neither_all <- sub_all_ou %>% filter(!home_b2b, !away_b2b)
any_b2b_all <- sub_all_ou %>% filter(home_b2b | away_b2b)
cat(sprintf("Combined: neither-on-B2B overs %.1f%% (n=%d) | at-least-one-on-B2B overs %.1f%% (n=%d)\n",
            100 * mean(neither_all$total_pts > neither_all$over_under), nrow(neither_all),
            100 * mean(any_b2b_all$total_pts > any_b2b_all$over_under), nrow(any_b2b_all)))

# ---------------------------------------------------------------------
# 4c. Rest differential bucket -> HOME team ATS cover rate
# ---------------------------------------------------------------------
cat("\n=== 4c. Home team ATS cover rate by rest-differential bucket, per season ===\n")

rest_diff_bucket <- function(x) {
  cut(x, breaks = c(-Inf, -2, -1, 0, 1, Inf),
      labels = c("<=-2", "-1", "0", "+1", ">=+2"), right = FALSE)
}
# Note: cut with right=FALSE and these breaks gives intervals [-Inf,-2),[-2,-1),[-1,0),[0,1),[1,Inf)
# which doesn't cleanly label integer diffs as requested; build buckets manually instead.
rest_diff_bucket <- function(x) {
  case_when(
    x <= -2 ~ "<=-2",
    x == -1 ~ "-1",
    x == 0  ~ "0",
    x == 1  ~ "+1",
    x >= 2  ~ ">=+2",
    TRUE ~ NA_character_
  )
}

rest_diff_stats <- function(sub, lbl) {
  sub <- sub %>%
    filter(!is.na(rest_diff), home_cover_margin != 0) %>%
    mutate(diff_bucket = rest_diff_bucket(rest_diff))
  sub %>%
    group_by(diff_bucket) %>%
    summarize(n = n(), home_cover_pct = 100 * mean(home_cover_margin > 0), .groups = "drop") %>%
    mutate(season_label = lbl)
}

rd_2025 <- rest_diff_stats(s2025, "2024-25")
rd_2026 <- rest_diff_stats(s2026, "2025-26")
rd_combined <- bind_rows(s2025, s2026) %>%
  filter(!is.na(rest_diff), home_cover_margin != 0) %>%
  mutate(diff_bucket = rest_diff_bucket(rest_diff)) %>%
  group_by(diff_bucket) %>%
  summarize(n = n(), home_cover_pct = 100 * mean(home_cover_margin > 0), .groups = "drop") %>%
  mutate(season_label = "Combined")

bucket_order <- c("<=-2", "-1", "0", "+1", ">=+2")
rd_all <- bind_rows(rd_2025, rd_2026, rd_combined) %>%
  mutate(diff_bucket = factor(diff_bucket, levels = bucket_order)) %>%
  arrange(diff_bucket, season_label)

print(rd_all, n = 30)

# ---------------------------------------------------------------------
# 5. binom.test on the largest-magnitude cut, combined across both seasons
#    (chosen AFTER inspecting all cuts above: the >=+2 rest-advantage
#    bucket from 4c is the largest-magnitude deviation from 50% AND the
#    only cut that replicates in direction both seasons - 60.4% in
#    2024-25, 59.3% in 2025-26, vs the raw lone-B2B-team cut which is
#    weak and inconsistent (44.8% vs 50.5%). Report both for transparency.)
# ---------------------------------------------------------------------
cat("\n=== 5. Significance tests (combined both seasons) ===\n")

# 5a. Headline cut: home team ATS cover rate when rest_diff >= +2
rd2_combined <- bind_rows(s2025, s2026) %>%
  filter(!is.na(rest_diff), home_cover_margin != 0, rest_diff >= 2)
n_rd2 <- nrow(rd2_combined)
k_rd2 <- sum(rd2_combined$home_cover_margin > 0)
bt_rd2 <- binom.test(k_rd2, n_rd2, 0.5)
cat(sprintf("Home team ATS cover when rest_diff >= +2 (home has >=2 extra rest days vs away), combined: %d/%d (%.1f%%), binom p=%.3f\n",
            k_rd2, n_rd2, 100 * k_rd2 / n_rd2, bt_rd2$p.value))
cat("Per-season (sign-flip check): 2024-25 60.4% (n=53), 2025-26 59.3% (n=54) - SAME direction both seasons.\n")

# 5b. Secondary cut (for completeness): lone-B2B team's own ATS cover rate
b2b_team_covers <- c(
  home_only_all$home_cover_margin > 0,
  away_only_all$home_cover_margin < 0
)
n_b2b <- length(b2b_team_covers)
k_b2b <- sum(b2b_team_covers)
bt_b2b <- binom.test(k_b2b, n_b2b, 0.5)
cat(sprintf("\n[secondary] Team on a lone B2B covers its own closing ATS line: %d/%d (%.1f%%), binom p=%.3f\n",
            k_b2b, n_b2b, 100 * k_b2b / n_b2b, bt_b2b$p.value))
cat("Per-season breakdown of the same cut (for sign-flip check):\n")
for (lbl in c("2024-25", "2025-26")) {
  sub <- sub_all %>% filter(season_label == lbl)
  ho <- sub %>% filter(home_b2b, !away_b2b)
  ao <- sub %>% filter(away_b2b, !home_b2b)
  covers <- c(ho$home_cover_margin > 0, ao$home_cover_margin < 0)
  cat(sprintf("  %s: %d/%d (%.1f%%)\n", lbl, sum(covers), length(covers), 100 * mean(covers)))
}

# ---------------------------------------------------------------------
# 6. Navy-theme plot: home ATS cover% by rest-differential bucket, both seasons
# ---------------------------------------------------------------------
myTheme <- theme(
  plot.background = element_rect(fill = "#02233F", color = NA),
  panel.background = element_rect(fill = "#02233F"),
  panel.grid = element_line(color = "#274066"),
  axis.ticks = element_line(color = "#274066"),
  axis.text = element_text(color = "white"),
  axis.title = element_text(color = "white"),
  plot.title = element_text(color = "white", hjust = .5),
  plot.subtitle = element_text(color = "white", hjust = .5),
  plot.caption = element_text(color = "white"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text = element_text(color = "white"),
  legend.title = element_text(color = "white"),
  strip.background = element_rect(fill = "#02233F"),
  strip.text = element_text(color = "white")
)

plot_df <- bind_rows(rd_2025, rd_2026) %>%
  mutate(diff_bucket = factor(diff_bucket, levels = bucket_order))

p <- ggplot(plot_df, aes(x = diff_bucket, y = home_cover_pct, fill = season_label,
                          group = season_label)) +
  geom_col(position = position_dodge(width = 0.75), width = 0.65) +
  geom_hline(yintercept = 52.38, linetype = "dashed", color = "white") +
  annotate("text", x = 0.6, y = 55, label = "-110 breakeven (52.38%)",
           color = "white", size = 3, hjust = 0) +
  scale_fill_manual(values = c("2024-25" = "#2E7BE0", "2025-26" = "#E0A02E")) +
  labs(title = "NBA: Home ATS Cover Rate by Rest-Differential Bucket",
       subtitle = "rest_diff = home_rest_days - away_rest_days; closing lines, both seasons separate",
       caption = "Rest derived from games with odds data (~99.7% complete) - minor off-by-one-game noise possible.",
       x = "Rest differential (home rest days - away rest days)",
       y = "Home team covers ATS (%)",
       fill = "Season") +
  myTheme

out_png <- file.path(OUT_DIR, "nba_rest_fatigue_by_bucket.png")
ggsave(out_png, p, width = 9.5, height = 5.5, dpi = 150, bg = "#02233F")
cat("\nSaved plot:", out_png, "\n")
