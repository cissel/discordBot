#!/usr/bin/env Rscript
# spyReturnsByStrategy.R
# Run 31 - cumulative returns by strategy (WFCV curve + sizing strategy comparison)
# Usage: Rscript R/spyReturnsByStrategy.R

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(scales)
  library(patchwork)
  library(lubridate)
  library(grid)
  library(gridExtra)
})

# ── paths ──────────────────────────────────────────────────────────────────────
pnl_path <- "outputs/features/markets/spy_wfcv_pnl.csv"
out_path  <- "outputs/markets/spy_returns_by_strategy_run31.png"

setwd("/home/jhcv/discordBot")

# ── 1. WFCV cumulative curve ───────────────────────────────────────────────────
pnl <- read.csv(pnl_path, stringsAsFactors = FALSE) %>%
  mutate(date = as.Date(date))

# rebase each fold to 1.0 so folds chain properly (they already do via cum_strat)
# derive per-strategy daily returns for the 5 sizing modes we want to visualise
# We only have strat_ret (t=0.54 half-bear) and bh_ret in the CSV.
# Reconstruct the other two visible strategies:
#   zero_bear:   same as strat but any bear day = 0 (approximation)
#   kelly_25:    already at 25% Kelly - strat_ret is full-size; scale down
# Since true per-day sizing flags aren't in CSV, we show: strat (t=0.54) + B&H

pnl_long <- pnl %>%
  select(date, fold, strat_ret, bh_ret) %>%
  rename(`Strategy (t=0.54)` = strat_ret, `SPY Buy & Hold` = bh_ret) %>%
  pivot_longer(cols = c(`Strategy (t=0.54)`, `SPY Buy & Hold`),
               names_to = "series", values_to = "daily_ret") %>%
  group_by(series) %>%
  arrange(date) %>%
  mutate(cum_ret = cumprod(1 + daily_ret) - 1) %>%
  ungroup()

# fold boundaries for shading
fold_bounds <- pnl %>%
  group_by(fold) %>%
  summarise(start = min(date), end = max(date), .groups = "drop")

# alternating fold shading
fold_rects <- fold_bounds %>%
  mutate(alpha = ifelse(fold %% 2 == 0, 0.06, 0.0))

# colours
cols <- c("Strategy (t=0.54)" = "#00C8FF", "SPY Buy & Hold" = "#FF6B35")

# WFCV total stats (from log)
strat_total  <- 0.977
bh_total     <- 1.112
strat_sharpe <- 1.091
bh_sharpe    <- 0.934

p1 <- ggplot(pnl_long, aes(x = date, y = cum_ret * 100, colour = series)) +
  # fold shading
  geom_rect(data = fold_rects %>% filter(alpha > 0),
            aes(xmin = start, xmax = end, ymin = -Inf, ymax = Inf),
            inherit.aes = FALSE, fill = "white", alpha = 0.04) +
  # fold labels
  geom_text(data = fold_bounds,
            aes(x = start + (end - start)/2, y = Inf,
                label = paste0("F", fold)),
            inherit.aes = FALSE, vjust = 1.5, size = 2.8,
            colour = "#888888", fontface = "plain") +
  # fold dividers
  geom_vline(data = fold_bounds[-1, ], aes(xintercept = as.numeric(start)),
             inherit.aes = FALSE, colour = "#555555", linetype = "dashed",
             linewidth = 0.35, alpha = 0.5) +
  geom_line(linewidth = 0.9, alpha = 0.92) +
  geom_hline(yintercept = 0, colour = "#666666", linewidth = 0.3) +
  scale_colour_manual(values = cols) +
  scale_y_continuous(labels = function(x) paste0(x, "%")) +
  scale_x_date(date_breaks = "6 months", date_labels = "%b %y") +
  labs(
    title = "WFCV Cumulative Returns - Run 31",
    subtitle = sprintf(
      "Strategy: %.1f%% total | Sharpe %.3f    B&H: %.1f%% total | Sharpe %.3f    (5bps tx cost, t=0.54, half-bear sizing)",
      strat_total * 100, strat_sharpe, bh_total * 100, bh_sharpe
    ),
    x = NULL, y = "Cumulative Return (%)",
    colour = NULL
  ) +
  theme_minimal(base_size = 11) +
  theme(
    plot.background  = element_rect(fill = "#1a1a2e", colour = NA),
    panel.background = element_rect(fill = "#16213e", colour = NA),
    panel.grid.major = element_line(colour = "#2a2a4a", linewidth = 0.3),
    panel.grid.minor = element_blank(),
    text             = element_text(colour = "#e0e0e0"),
    axis.text        = element_text(colour = "#aaaaaa", size = 8),
    axis.title       = element_text(colour = "#cccccc", size = 9),
    plot.title       = element_text(colour = "#ffffff", face = "bold", size = 13),
    plot.subtitle    = element_text(colour = "#aaaaaa", size = 8),
    legend.position  = "top",
    legend.text      = element_text(colour = "#e0e0e0", size = 9),
    legend.background = element_blank(),
    legend.key        = element_blank()
  )

# ── 2. Sizing strategy comparison bar chart ────────────────────────────────────
strategies <- tribble(
  ~strategy,                   ~sharpe, ~ann_ret, ~max_dd,
  "Best (t=0.58)",              2.846,   40.6,    -2.4,
  "Half-bear (t=0.54)",         2.646,   27.5,    -3.3,
  "Half-bear (t=0.53)",         2.282,   24.4,    -3.5,
  "Zero-bear (t=0.54)",         2.288,   18.8,    -3.3,
  "Kill switch (t=0.53)",       1.976,   16.8,    -4.6,
  "Frac-Kelly 25% half-bear",   2.867,    3.4,    -0.4,
  "SPY Buy & Hold",             1.333,   24.9,    -9.6
)

strategies <- strategies %>%
  mutate(strategy = factor(strategy, levels = rev(strategy)),
         is_bh = strategy == "SPY Buy & Hold")

bar_col   <- "#00C8FF"
bh_col    <- "#FF6B35"
kelly_col <- "#9B59B6"

# Sharpe panel
p2a <- ggplot(strategies, aes(x = strategy, y = sharpe,
                               fill = ifelse(is_bh, "bh",
                                      ifelse(grepl("Kelly", strategy), "kelly", "strat")))) +
  geom_col(width = 0.65, alpha = 0.85) +
  geom_text(aes(label = sprintf("%.3f", sharpe),
                hjust = ifelse(sharpe >= 0, -0.1, 1.1)),
            colour = "#ffffff", size = 3.0) +
  coord_flip(clip = "off") +
  scale_fill_manual(values = c("strat" = bar_col, "bh" = bh_col, "kelly" = kelly_col),
                    guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.2))) +
  labs(title = "Sharpe Ratio (val set)", x = NULL, y = "Sharpe") +
  theme_minimal(base_size = 10) +
  theme(
    plot.background  = element_rect(fill = "#1a1a2e", colour = NA),
    panel.background = element_rect(fill = "#16213e", colour = NA),
    panel.grid.major.y = element_blank(),
    panel.grid.major.x = element_line(colour = "#2a2a4a", linewidth = 0.3),
    panel.grid.minor = element_blank(),
    text             = element_text(colour = "#e0e0e0"),
    axis.text        = element_text(colour = "#cccccc", size = 8),
    axis.title       = element_text(colour = "#cccccc", size = 9),
    plot.title       = element_text(colour = "#ffffff", face = "bold", size = 10)
  )

# Ann ret panel
p2b <- ggplot(strategies, aes(x = strategy, y = ann_ret,
                               fill = ifelse(is_bh, "bh",
                                      ifelse(grepl("Kelly", strategy), "kelly", "strat")))) +
  geom_col(width = 0.65, alpha = 0.85) +
  geom_text(aes(label = sprintf("%.1f%%", ann_ret),
                hjust = ifelse(ann_ret >= 0, -0.1, 1.1)),
            colour = "#ffffff", size = 3.0) +
  coord_flip(clip = "off") +
  scale_fill_manual(values = c("strat" = bar_col, "bh" = bh_col, "kelly" = kelly_col),
                    guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.25)),
                     labels = function(x) paste0(x, "%")) +
  labs(title = "Ann. Return (val set)", x = NULL, y = "Ann Return (%)") +
  theme_minimal(base_size = 10) +
  theme(
    plot.background  = element_rect(fill = "#1a1a2e", colour = NA),
    panel.background = element_rect(fill = "#16213e", colour = NA),
    panel.grid.major.y = element_blank(),
    panel.grid.major.x = element_line(colour = "#2a2a4a", linewidth = 0.3),
    panel.grid.minor = element_blank(),
    text             = element_text(colour = "#e0e0e0"),
    axis.text        = element_text(colour = "#cccccc", size = 8),
    axis.title       = element_text(colour = "#cccccc", size = 9),
    plot.title       = element_text(colour = "#ffffff", face = "bold", size = 10)
  )

# MaxDD panel
p2c <- ggplot(strategies, aes(x = strategy, y = max_dd,
                               fill = ifelse(is_bh, "bh",
                                      ifelse(grepl("Kelly", strategy), "kelly", "strat")))) +
  geom_col(width = 0.65, alpha = 0.85) +
  geom_text(aes(label = sprintf("%.1f%%", max_dd),
                hjust = 1.1),
            colour = "#ffffff", size = 3.0) +
  coord_flip(clip = "off") +
  scale_fill_manual(values = c("strat" = bar_col, "bh" = bh_col, "kelly" = kelly_col),
                    guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.25, 0.05)),
                     labels = function(x) paste0(x, "%")) +
  labs(title = "Max Drawdown (val set)", x = NULL, y = "Max DD (%)") +
  theme_minimal(base_size = 10) +
  theme(
    plot.background  = element_rect(fill = "#1a1a2e", colour = NA),
    panel.background = element_rect(fill = "#16213e", colour = NA),
    panel.grid.major.y = element_blank(),
    panel.grid.major.x = element_line(colour = "#2a2a4a", linewidth = 0.3),
    panel.grid.minor = element_blank(),
    text             = element_text(colour = "#e0e0e0"),
    axis.text        = element_text(colour = "#cccccc", size = 8),
    axis.title       = element_text(colour = "#cccccc", size = 9),
    plot.title       = element_text(colour = "#ffffff", face = "bold", size = 10)
  )

# ── 3. Assemble ───────────────────────────────────────────────────────────────
bottom <- p2a | p2b | p2c

final <- (p1 / bottom) +
  plot_layout(heights = c(1.6, 1)) +
  plot_annotation(
    caption = "Run 31 - WFCV 2018-2023 (5 folds) | Val set = full training data | Holdout 2024+ locked | blue=strategy, orange=B&H, purple=Kelly",
    theme = theme(
      plot.background = element_rect(fill = "#1a1a2e", colour = NA),
      plot.caption    = element_text(colour = "#666688", size = 7.5)
    )
  )

ggsave(out_path, final, width = 14, height = 9, dpi = 150, bg = "#1a1a2e")
cat(sprintf("[spyReturnsByStrategy] saved -> %s\n", out_path))
