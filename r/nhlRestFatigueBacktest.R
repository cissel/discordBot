# nhlRestFatigueBacktest.R by JHCV
#
# Tests rest/back-to-back (B2B) fatigue effects on NHL moneyline, puck-line
# (spread), and totals markets using closing lines from nhlBettingDataCollect.R
# (2024-25 + 2025-26 seasons). Modeled on nhlMoneylineBacktest.R's structure.
#
# Method:
#   1. Dedupe each season's odds file to one row per event_id.
#   2. Reshape to a per-team game log (home + away rows), compute each team's
#      rest_days = days since that team's immediately preceding game. First
#      game of the season per team -> rest_days = NA, excluded from rest-based
#      comparisons (but the game itself stays in via the other side if known).
#   3. Rejoin home_rest_days / away_rest_days onto the per-game table.
#   4. B2B = rest_days == 0. Tests, PER SEASON (never pooled-only):
#      a. ML ROI betting AGAINST the B2B team vs. a neither-B2B baseline, plus
#         puck-line cover rate for the B2B team itself.
#      b. Over/under hit rate: neither-B2B vs. at-least-one-B2B games.
#      c. rest_differential (home_rest - away_rest) bucketed; home ML ROI and
#         home puck-line cover rate per bucket.
#   5. binom.test on the single largest-magnitude cut, combined across seasons
#      (exactly like nbaAtsTotalsBacktest.R's small-favorites check).
#
# CAVEAT 1 (completeness): the collector's own logs report 100% odds coverage
# for both NHL seasons, so every team's rest-day chain is fully known except
# each team's literal season-opening game (correctly NA'd out below) - unlike
# NBA, missing-odds gaps are a non-issue here.
#
# CAVEAT 2 (timezone, found while building this): game_date in the source CSV
# is an ISO UTC timestamp ("2024-10-12T23:00Z"). Taking the UTC calendar date
# directly is WRONG for some games - e.g. an early-afternoon US game can be
# stamped "...T00:00Z", which is actually the previous US evening, so two real
# calendar days apart games can collide onto the same raw UTC date and produce
# a spurious 0-day gap. Verified directly: naive UTC dates produced 28 (2024-25)
# / similar spurious same-day collisions; converting to America/New_York local
# time before taking the date eliminates them entirely (confirmed table of
# day-gaps has zero 0s after the fix). All date math below uses the
# TZ-corrected local date.
#
# CAVEAT 3 (rest_days convention): "rest_days" here follows normal sports
# usage - 0 means NO day off between starts (i.e. played on back-to-back
# calendar days), 1 means a single day off, etc. This is (raw calendar-day gap
# - 1), NOT the raw gap itself, so that B2B cleanly means rest_days == 0 as
# specified in the task brief.

suppressMessages({
  library(dplyr)
  library(tidyr)
  library(ggplot2)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nhl/betting")

profit_per_dollar <- function(ml, win) {
  payout <- ifelse(ml > 0, ml / 100, 100 / abs(ml))
  ifelse(win, payout, -1)
}

# --- Load + dedupe one season, add rest days via a per-team game log ---
load_season <- function(szn_label, path) {
  g <- read.csv(path) %>%
    filter(!is.na(home_team_moneyline), !is.na(away_team_moneyline)) %>%
    distinct(event_id, .keep_all = TRUE) %>%
    mutate(
      season_label = szn_label,
      game_dt = as.POSIXct(game_date, format = "%Y-%m-%dT%H:%MZ", tz = "UTC"),
      game_date_d = as.Date(format(game_dt, tz = "America/New_York")),
      home_win = home_score > away_score,
      home_margin = home_score - away_score,
      total_pts = home_score + away_score
    )

  # Reshape to one row per team per game
  home_rows <- g %>% transmute(event_id, team = home_abbreviation, game_date_d, side = "home")
  away_rows <- g %>% transmute(event_id, team = away_abbreviation, game_date_d, side = "away")
  team_log <- bind_rows(home_rows, away_rows) %>%
    arrange(team, game_date_d) %>%
    group_by(team) %>%
    # raw_gap = calendar days since team's last game; rest_days = raw_gap - 1
    # so that 0 means "no day off" (true back-to-back), per CAVEAT 3 above.
    mutate(raw_gap = as.numeric(game_date_d - lag(game_date_d)),
           rest_days = raw_gap - 1) %>%
    ungroup()

  home_rest <- team_log %>% filter(side == "home") %>% select(event_id, home_rest_days = rest_days)
  away_rest <- team_log %>% filter(side == "away") %>% select(event_id, away_rest_days = rest_days)

  g <- g %>%
    left_join(home_rest, by = "event_id") %>%
    left_join(away_rest, by = "event_id") %>%
    mutate(
      home_b2b = home_rest_days == 0,
      away_b2b = away_rest_days == 0,
      rest_differential = home_rest_days - away_rest_days
    )
  g
}

s2025 <- load_season("2024-25", file.path(OUT_DIR, "nhl_odds_2025.csv"))
s2026 <- load_season("2025-26", file.path(OUT_DIR, "nhl_odds_2026.csv"))
seasons <- list(s2025, s2026)

cat(sprintf("Loaded %d games (%d 2024-25, %d 2025-26)\n",
            nrow(s2025) + nrow(s2026), nrow(s2025), nrow(s2026)))
for (s in seasons) {
  n_na_either <- sum(is.na(s$home_rest_days) | is.na(s$away_rest_days))
  cat(sprintf("  %s: %d/%d games with at least one side's rest_days unknown (season openers)\n",
              s$season_label[1], n_na_either, nrow(s)))
}

# ======================================================================
# 4a. ML ROI betting AGAINST the B2B team, vs. neither-on-B2B baseline.
#     Puck-line cover rate for the B2B team itself.
# ======================================================================
cat("\n=== 4a. Moneyline: fade the B2B team, vs. neither-B2B baseline ===\n")
res_4a <- list()
for (s in seasons) {
  lbl <- s$season_label[1]
  d <- s %>% filter(!is.na(home_rest_days), !is.na(away_rest_days))

  one_b2b <- d %>% filter(home_b2b | away_b2b, !(home_b2b & away_b2b))
  neither_b2b <- d %>% filter(!home_b2b, !away_b2b)

  # profit from betting AGAINST whichever team is on the B2B (only one side)
  fade_profit <- with(one_b2b, ifelse(
    home_b2b,
    profit_per_dollar(away_team_moneyline, !home_win),   # bet away (opponent of home B2B team)
    profit_per_dollar(home_team_moneyline, home_win)      # bet home (opponent of away B2B team)
  ))
  # baseline: neither team rested-disadvantaged - bet home ML as a neutral reference
  baseline_profit <- profit_per_dollar(neither_b2b$home_team_moneyline, neither_b2b$home_win)

  # puck-line cover rate for the B2B team itself (single-B2B games only)
  pl <- one_b2b %>% filter(!is.na(spread)) %>%
    mutate(cover_margin = home_margin + spread) %>%
    filter(cover_margin != 0)
  b2b_team_covers <- with(pl, ifelse(home_b2b, cover_margin > 0, cover_margin < 0))

  res_4a[[lbl]] <- data.frame(
    season = lbl,
    n_one_b2b = nrow(one_b2b),
    fade_b2b_roi = 100 * mean(fade_profit),
    n_neither_b2b = nrow(neither_b2b),
    baseline_home_ml_roi = 100 * mean(baseline_profit),
    n_pl_b2b = length(b2b_team_covers),
    b2b_team_cover_pct = 100 * mean(b2b_team_covers)
  )
}
res_4a <- bind_rows(res_4a)
print(res_4a, row.names = FALSE)

# ======================================================================
# 4b. Over/under hit rate: neither-B2B vs at-least-one-B2B
# ======================================================================
cat("\n=== 4b. Totals: neither-on-B2B vs at-least-one-on-B2B ===\n")
res_4b <- list()
for (s in seasons) {
  lbl <- s$season_label[1]
  d <- s %>% filter(!is.na(home_rest_days), !is.na(away_rest_days), !is.na(over_under),
                     total_pts != over_under)
  neither <- d %>% filter(!home_b2b, !away_b2b)
  at_least_one <- d %>% filter(home_b2b | away_b2b)

  res_4b[[lbl]] <- data.frame(
    season = lbl,
    n_neither = nrow(neither), over_pct_neither = 100 * mean(neither$total_pts > neither$over_under),
    n_at_least_one = nrow(at_least_one), over_pct_b2b = 100 * mean(at_least_one$total_pts > at_least_one$over_under)
  )
}
res_4b <- bind_rows(res_4b)
print(res_4b, row.names = FALSE)

# ======================================================================
# 4c. rest_differential bucket -> home ML ROI + home puck-line cover rate
# ======================================================================
cat("\n=== 4c. Home ML ROI + puck-line cover by rest_differential bucket ===\n")
bucket_rest_diff <- function(x) {
  cut(x, breaks = c(-100, -2, -1, 0, 1, 100),
      labels = c("<=-2", "-1", "0", "+1", ">=+2"), right = TRUE)
}

res_4c <- list()
for (s in seasons) {
  lbl <- s$season_label[1]
  d <- s %>% filter(!is.na(rest_differential)) %>%
    mutate(bucket = bucket_rest_diff(rest_differential))

  ml_by_bucket <- d %>%
    group_by(bucket) %>%
    summarize(n = n(), home_ml_roi = 100 * mean(profit_per_dollar(home_team_moneyline, home_win)),
              .groups = "drop")

  pl_by_bucket <- d %>%
    filter(!is.na(spread)) %>%
    mutate(cover_margin = home_margin + spread) %>%
    filter(cover_margin != 0) %>%
    group_by(bucket) %>%
    summarize(n_pl = n(), home_cover_pct = 100 * mean(cover_margin > 0), .groups = "drop")

  merged <- ml_by_bucket %>% left_join(pl_by_bucket, by = "bucket") %>% mutate(season = lbl)
  res_4c[[lbl]] <- merged
}
res_4c <- bind_rows(res_4c) %>% select(season, bucket, n, home_ml_roi, n_pl, home_cover_pct)
print(res_4c, n = 30, row.names = FALSE)

# ======================================================================
# 5. Significance test on the single largest-magnitude cut, combined across
#    seasons. Candidate: B2B team puck-line cover rate (does the market fail
#    to price fatigue into the spread?) vs. the no-vig 50% null.
# ======================================================================
cat("\n=== 5. Significance check: B2B team puck-line cover rate, combined ===\n")
combined_b2b_pl_n <- sum(res_4a$n_pl_b2b)
combined_b2b_pl_covers <- sum(round(res_4a$n_pl_b2b * res_4a$b2b_team_cover_pct / 100))
bt_pl <- binom.test(combined_b2b_pl_covers, combined_b2b_pl_n, 0.5)
cat(sprintf("B2B team covers its own closing puck line, combined 2 seasons: %d/%d (%.1f%%), binom p=%.3f\n",
            combined_b2b_pl_covers, combined_b2b_pl_n,
            100 * combined_b2b_pl_covers / combined_b2b_pl_n, bt_pl$p.value))

cat("\n=== 5b. Fade-the-B2B-team: straight-up win rate (descriptive) + ACTUAL ROI t-test ===\n")
# Build the combined fade vectors directly (not just the summary %).
# NOTE: a straight-up win-rate test against a 50% null is NOT a fair test of
# betting edge here, because moneylines already price in team quality/home-ice
# asymmetry - a disadvantaged (B2B) team being favored to lose straight-up is
# expected and already reflected in its price. The test that actually matters
# economically is whether FLAT-STAKE PROFIT (which nets out the odds) is above
# zero, so we also run a one-sample t-test on the real per-bet profit vector.
combined_fade_wins <- 0
combined_fade_n <- 0
combined_fade_profit <- c()
for (s in seasons) {
  d <- s %>% filter(!is.na(home_rest_days), !is.na(away_rest_days), home_b2b | away_b2b, !(home_b2b & away_b2b))
  fade_win <- with(d, ifelse(home_b2b, !home_win, home_win))
  fade_profit <- with(d, ifelse(
    home_b2b,
    profit_per_dollar(away_team_moneyline, !home_win),
    profit_per_dollar(home_team_moneyline, home_win)
  ))
  combined_fade_wins <- combined_fade_wins + sum(fade_win)
  combined_fade_n <- combined_fade_n + length(fade_win)
  combined_fade_profit <- c(combined_fade_profit, fade_profit)
}
bt_ml <- binom.test(combined_fade_wins, combined_fade_n, 0.5)
cat(sprintf("Fade the B2B team straight-up win rate (descriptive only), combined: %d/%d (%.1f%%), binom p=%.3f\n",
            combined_fade_wins, combined_fade_n, 100 * combined_fade_wins / combined_fade_n, bt_ml$p.value))
cat("  (High win rate here is expected and already priced into the odds - NOT itself a betting edge.)\n")

tt_roi <- t.test(combined_fade_profit, mu = 0)
cat(sprintf("Fade the B2B team flat-$1 ROI, combined 2 seasons: mean=%.2f%% per bet (n=%d), t-test p=%.3f\n",
            100 * mean(combined_fade_profit), length(combined_fade_profit), tt_roi$p.value))

cat("\nInterpretation: any cut where sign/magnitude flips between seasons is\n")
cat("noise, not a strategy - see chat writeup for which cuts did and didn't replicate.\n")

# ======================================================================
# Navy-theme plot: moneyline ROI by rest-differential bucket, both seasons
# ======================================================================
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
  legend.title = element_text(color = "white")
)

plot_df <- res_4c %>% filter(!is.na(bucket))
plot_df$bucket <- factor(plot_df$bucket, levels = c("<=-2", "-1", "0", "+1", ">=+2"))
plot_df$season <- factor(plot_df$season, levels = c("2024-25", "2025-26"))

p <- ggplot(plot_df, aes(x = bucket, y = home_ml_roi, fill = season)) +
  geom_col(position = position_dodge(width = 0.7), width = 0.6) +
  geom_hline(yintercept = 0, color = "white") +
  scale_fill_manual(values = c("2024-25" = "#2E7BE0", "2025-26" = "#E0A02E")) +
  labs(title = "NHL: Home Moneyline ROI by Rest-Differential Bucket",
       subtitle = "rest_differential = home_rest_days - away_rest_days, closing lines",
       x = "Rest differential (home - away, days)", y = "Home ML ROI (%)", fill = "Season",
       caption = "Flat $1 bets on home moneyline; 2024-25 + 2025-26 regular seasons") +
  myTheme

out_png <- file.path(OUT_DIR, "nhl_rest_fatigue_by_bucket.png")
ggsave(out_png, p, width = 9, height = 5.5, dpi = 150, bg = "#02233F")
cat("\nSaved plot:", out_png, "\n")
