#!/usr/bin/env Rscript
# injuryRecoveryPlot.R
# Age-informed recovery check for 2025-injury-shortened players in the
# draftable pool (ADP<=200). Compares each player's ACTUAL 2025 pts/game
# (lag1_pts_per_game, real data - already a model feature) against what
# their pre-injury 2024 healthy-season rate * historical age-bucket
# recovery ratio (from the 185-row 2009-2025 nflreadr injury study) would
# imply. Big negative delta = actual rate undershot age-normal recovery
# expectations (red flag beyond what the model's lag1_pts_per_game already
# captures). Big positive delta = already outperforming recovery norms.
suppressMessages({
  library(readr); library(dplyr); library(ggplot2); library(forcats)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
OUT_PNG <- file.path(OUT_DIR, "injury_recovery_context_2026.png")

navy <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#274066", linewidth = 0.4),
  panel.grid.minor  = element_blank(),
  axis.text         = element_text(color = "white", size = 8),
  axis.title        = element_text(color = "white", size = 9),
  plot.title        = element_text(color = "white", size = 13, face = "bold", hjust = 0),
  plot.subtitle     = element_text(color = "white", size = 9,  hjust = 0),
  plot.caption      = element_text(color = "white", size = 7),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "white", size = 9, face = "bold"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "white", size = 8),
  legend.title      = element_text(color = "white", size = 8),
  legend.key        = element_rect(fill = "#02233F"),
  plot.margin       = margin(10, 16, 10, 12)
)

POS_COLORS <- c(QB = "#7B4CE0", RB = "#2E9E4E", WR = "#2E7BE0", TE = "#E0A02E")

df <- read_csv(file.path(OUT_DIR, "injury_recovery_context_2026.csv"), show_col_types = FALSE) %>%
  filter(!is.na(ppg_delta_recovery_vs_model)) %>%
  mutate(label = paste0(full_name, " (", position, ", ADP ", adp_overall, ")"))

p <- ggplot(df, aes(x = fct_reorder(label, ppg_delta_recovery_vs_model),
                     y = ppg_delta_recovery_vs_model, fill = position)) +
  geom_hline(yintercept = 0, color = "white", linewidth = 0.5) +
  geom_col(width = 0.7) +
  geom_text(aes(label = round(ppg_delta_recovery_vs_model, 1),
                hjust = ifelse(ppg_delta_recovery_vs_model >= 0, -0.15, 1.15)),
            color = "white", size = 2.9) +
  coord_flip(clip = "off") +
  scale_fill_manual(values = POS_COLORS) +
  scale_y_continuous(expand = expansion(mult = 0.18)) +
  labs(
    title = "2025 Injury-Shortened Players: Actual Rate vs. Age-Informed Recovery",
    subtitle = paste0(
      "Delta = actual 2025 pts/game minus (2024 healthy-baseline pts/game x historical age-bucket recovery ratio)\n",
      "Negative = underperformed age-normal recovery odds (risk flag beyond the model's raw rate feature) | ",
      "n=", nrow(df), " of 28 flagged players with a usable 2024 baseline"
    ),
    x = NULL, y = "Pts/Game Delta vs. Age-Informed Recovery Expectation", fill = "Position",
    caption = "Recovery ratios from nflreadr::load_injuries() 2009-2025 study (185 qualifying player-seasons, 4+ wks Out/Doubtful) | JHCV"
  ) +
  navy

ggsave(OUT_PNG, p, width = 11.5, height = 8.5, dpi = 150, bg = "#02233F")
cat("Wrote:", OUT_PNG, "\n")
