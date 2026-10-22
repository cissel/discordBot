# nhlMoneylineBacktest.R by JHCV
#
# Backtests classic NHL moneyline/puck-line/totals trends against 2 seasons
# of closing odds collected by nhlBettingDataCollect.R (fastRhockey ESPN
# odds - DraftKings/ESPN BET as available). Unlike the NBA collector, this
# data includes REAL moneylines + over/under juice, so ROI figures here are
# the actual historical price, not an assumed -110.
#
# Tests, per season (not pooled - see caveat below):
#   1. Flat-$1 ROI betting every home ML / away ML / dog ML / favorite ML
#   2. Puck line (spread) home cover rate
#   3. Over/under hit rate
#
# IMPORTANT: results are reported PER SEASON, not combined, because the
# whole point of this test is checking whether a season-1 pattern replicates
# in season 2 - pooling would hide a sign flip between seasons.

suppressMessages({
  library(dplyr)
  library(ggplot2)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nhl/betting")

profit_per_dollar <- function(ml, win) {
  payout <- ifelse(ml > 0, ml / 100, 100 / abs(ml))
  ifelse(win, payout, -1)
}

load_season <- function(szn_label, path) {
  read.csv(path) %>%
    filter(!is.na(home_team_moneyline), !is.na(away_team_moneyline)) %>%
    distinct(event_id, .keep_all = TRUE) %>%
    mutate(
      season_label = szn_label,
      home_win = home_score > away_score,
      home_dog = home_team_favorite == FALSE,
      profit_home_ml = profit_per_dollar(home_team_moneyline, home_win),
      profit_away_ml = profit_per_dollar(away_team_moneyline, !home_win),
      home_margin = home_score - away_score,
      total_pts = home_score + away_score
    )
}

s2025 <- load_season("2024-25", file.path(OUT_DIR, "nhl_odds_2025.csv"))
s2026 <- load_season("2025-26", file.path(OUT_DIR, "nhl_odds_2026.csv"))

results <- list()
for (s in list(s2025, s2026)) {
  lbl <- s$season_label[1]
  dog_profit <- ifelse(s$home_dog, s$profit_home_ml, s$profit_away_ml)
  fav_profit <- ifelse(!s$home_dog, s$profit_home_ml, s$profit_away_ml)

  pl <- s %>% filter(!is.na(spread)) %>% mutate(cover_margin = home_margin + spread) %>%
    filter(cover_margin != 0)
  ou <- s %>% filter(!is.na(over_under), total_pts != over_under)

  results[[lbl]] <- data.frame(
    season = lbl, n = nrow(s),
    home_ml_roi = 100 * mean(s$profit_home_ml),
    away_ml_roi = 100 * mean(s$profit_away_ml),
    dog_ml_roi = 100 * mean(dog_profit),
    fav_ml_roi = 100 * mean(fav_profit),
    puckline_home_cover_pct = 100 * mean(pl$cover_margin > 0),
    over_pct = 100 * mean(ou$total_pts > ou$over_under)
  )
}
res <- bind_rows(results)
cat("=== NHL betting trends, PER SEASON (no pooling - checking replication) ===\n")
print(res, row.names = FALSE)

cat("\nInterpretation: any row where sign/magnitude flips between seasons is\n")
cat("noise, not a strategy - see chat writeup for which trends did and didn't replicate.\n")

# --- Navy-theme plot: ROI by bet type, both seasons side by side ---
myTheme <- theme(
  plot.background = element_rect(fill = "#02233F", color = NA),
  panel.background = element_rect(fill = "#02233F"),
  panel.grid = element_line(color = "#274066"),
  axis.ticks = element_line(color = "#274066"),
  axis.text = element_text(color = "white"),
  axis.title = element_text(color = "white"),
  plot.title = element_text(color = "white", hjust = .5),
  plot.subtitle = element_text(color = "white", hjust = .5),
  legend.background = element_rect(fill = "#02233F"),
  legend.text = element_text(color = "white"),
  legend.title = element_text(color = "white")
)

plot_df <- res %>%
  select(season, home_ml_roi, away_ml_roi, dog_ml_roi, fav_ml_roi) %>%
  tidyr::pivot_longer(-season, names_to = "bet_type", values_to = "roi") %>%
  mutate(bet_type = recode(bet_type,
    home_ml_roi = "Home ML", away_ml_roi = "Away ML",
    dog_ml_roi = "Dog ML", fav_ml_roi = "Favorite ML"))

p <- ggplot(plot_df, aes(x = bet_type, y = roi, fill = season)) +
  geom_col(position = position_dodge(width = 0.7), width = 0.6) +
  geom_hline(yintercept = 0, color = "white") +
  scale_fill_manual(values = c("2024-25" = "#2E7BE0", "2025-26" = "#E0A02E")) +
  labs(title = "NHL: Flat-$1 ROI by Bet Type",
       subtitle = "No trend replicates across both seasons - market looks efficient here",
       x = NULL, y = "ROI (%)", fill = "Season") +
  myTheme

out_png <- file.path(OUT_DIR, "nhl_ml_roi_by_season.png")
ggsave(out_png, p, width = 9, height = 5.5, dpi = 150, bg = "#02233F")
cat("\nSaved plot:", out_png, "\n")
