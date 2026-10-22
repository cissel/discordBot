# nbaAtsTotalsBacktest.R by JHCV
#
# Backtests classic ATS/totals trends against 2 seasons of NBA closing lines
# collected by nbaBettingDataCollect.R (ESPN odds via hoopR, DraftKings/ESPN
# BET as available - whichever provider ESPN had for that game).
#
# NOTE: hoopR's espn_nba_game_odds() does not expose per-side juice
# (over/under odds, spread odds) for historical completed games - only the
# line itself. All profit/ROI figures below assume standard -110 both sides,
# which is the industry default but NOT the book's actual historical price.
# Treat ROI numbers as directional, not exact dollar figures.
#
# Tests, per season and combined:
#   1. Home underdog ATS vs home favorite ATS
#   2. Favorite ATS cover rate by spread-size bucket (0-3, 3-6, 6-9, 9-12, 12+)
#   3. Over/under hit rate

suppressMessages({
  library(dplyr)
  library(ggplot2)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nba/betting")

load_season <- function(szn_label, path) {
  d <- read.csv(path) %>%
    filter(!is.na(spread), !is.na(over_under)) %>%
    distinct(event_id, .keep_all = TRUE) %>%
    mutate(
      season_label = szn_label,
      home_margin = home_score - away_score,
      total_pts = home_score + away_score,
      home_cover_margin = home_margin + spread,
      home_dog = spread > 0,
      home_fav = spread < 0,
      bucket = cut(abs(spread), breaks = c(0, 3, 6, 9, 12, 100),
                   labels = c("0-3", "3-6", "6-9", "9-12", "12+"))
    )
  d
}

s2025 <- load_season("2024-25", file.path(OUT_DIR, "nba_odds_2025.csv"))
s2026 <- load_season("2025-26", file.path(OUT_DIR, "nba_odds_2026.csv"))
d <- bind_rows(s2025, s2026)

cat(sprintf("Loaded %d games (%d 2024-25, %d 2025-26)\n", nrow(d), nrow(s2025), nrow(s2026)))

# --- Bucket-level favorite cover rate, per season + combined ---
bucket_stats <- d %>%
  filter(home_cover_margin != 0) %>%
  mutate(fav_covers = ifelse(home_fav, home_cover_margin > 0, home_cover_margin < 0)) %>%
  group_by(season_label, bucket) %>%
  summarize(n = n(), fav_cover_pct = mean(fav_covers) * 100, .groups = "drop")

combined_bucket <- d %>%
  filter(home_cover_margin != 0) %>%
  mutate(fav_covers = ifelse(home_fav, home_cover_margin > 0, home_cover_margin < 0)) %>%
  group_by(bucket) %>%
  summarize(n = n(), fav_cover_pct = mean(fav_covers) * 100, .groups = "drop") %>%
  mutate(season_label = "Combined")

cat("\n=== Favorite ATS cover rate by spread bucket ===\n")
print(bind_rows(bucket_stats, combined_bucket) %>% arrange(bucket, season_label), n = 30)

# significance check on the 0-3 bucket specifically (the interesting one)
b03 <- combined_bucket %>% filter(bucket == "0-3")
bt <- binom.test(round(b03$n * b03$fav_cover_pct / 100), b03$n, 0.5)
cat(sprintf("\nSmall favorites (0-3 pts) ATS, combined 2 seasons: %d/%d covers (%.1f%%), binom p=%.3f\n",
            round(b03$n * b03$fav_cover_pct / 100), b03$n, b03$fav_cover_pct, bt$p.value))

# --- Home dog vs home fav ATS, overs ---
cat("\n=== Home dog / home fav ATS + totals, by season ===\n")
for (lbl in c("2024-25", "2025-26")) {
  sub <- d %>% filter(season_label == lbl, home_cover_margin != 0)
  dog <- sub %>% filter(home_dog)
  fav <- sub %>% filter(home_fav)
  ou <- d %>% filter(season_label == lbl, total_pts != over_under)
  cat(sprintf("%s: home dog ATS %.1f%% (n=%d) | home fav ATS %.1f%% (n=%d) | overs %.1f%% (n=%d)\n",
              lbl, 100 * mean(dog$home_cover_margin > 0), nrow(dog),
              100 * mean(fav$home_cover_margin > 0), nrow(fav),
              100 * mean(ou$total_pts > ou$over_under), nrow(ou)))
}

# --- Navy-theme plot: favorite cover% by spread bucket, both seasons + combined ---
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
  legend.title = element_text(color = "white"),
  strip.background = element_rect(fill = "#02233F"),
  strip.text = element_text(color = "white")
)

plot_df <- bind_rows(bucket_stats, combined_bucket) %>% filter(!is.na(bucket))
plot_df$season_label <- factor(plot_df$season_label, levels = c("2024-25", "2025-26", "Combined"))

p <- ggplot(plot_df, aes(x = bucket, y = fav_cover_pct, fill = season_label,
                          group = season_label)) +
  geom_col(position = position_dodge(width = 0.75), width = 0.65) +
  geom_hline(yintercept = 52.38, linetype = "dashed", color = "white") +
  annotate("text", x = 0.9, y = 54.5, label = "-110 breakeven (52.38%)",
           color = "white", size = 3, hjust = 0) +
  scale_fill_manual(values = c("2024-25" = "#2E7BE0", "2025-26" = "#E0A02E",
                                "Combined" = "#2E9E4E")) +
  labs(title = "NBA: Favorite ATS Cover Rate by Spread Size",
       subtitle = "2024-25 + 2025-26 regular seasons, closing lines (assumed -110)",
       x = "Spread (points favorite is laying)", y = "Favorite covers (%)",
       fill = "Season") +
  myTheme

out_png <- file.path(OUT_DIR, "nba_fav_ats_by_bucket.png")
ggsave(out_png, p, width = 9, height = 5.5, dpi = 150, bg = "#02233F")
cat("\nSaved plot:", out_png, "\n")
