#!/usr/bin/env Rscript
# spyPerformancePlot.R - live forward track record chart for the SPY paper trading system
# Reads:  outputs/markets/spy_performance_daily.csv (from spyPerformance.py)
#         outputs/markets/spy_performance.json      (summary stats)
# Writes: outputs/markets/spy_performance.png
# Usage:  Rscript r/spyPerformancePlot.R

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(patchwork)
  library(scales)
  library(jsonlite)
})

setwd("/home/jhcv/discordBot")

daily_csv <- "outputs/markets/spy_performance_daily.csv"
json_path <- "outputs/markets/spy_performance.json"
out_png   <- "outputs/markets/spy_performance.png"

# ── navy theme ────────────────────────────────────────────────────────────────
navy_bg    <- "#0d1b2a"
navy_panel <- "#112240"
navy_grid  <- "#1e3a5f"
txt_white  <- "#e8eaf6"
txt_muted  <- "#90a4ae"
col_strat  <- "#4fc3f7"   # light blue - strategy
col_bh     <- "#ef5350"   # red - buy & hold
col_broker <- "#26a69a"   # teal - actual broker equity
col_gold   <- "#FFD700"

navy_theme <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace% theme(
    plot.background   = element_rect(fill = navy_bg,    colour = NA),
    panel.background  = element_rect(fill = navy_panel, colour = NA),
    panel.grid.major  = element_line(colour = navy_grid, linewidth = 0.3),
    panel.grid.minor  = element_blank(),
    text              = element_text(colour = txt_white),
    axis.text         = element_text(colour = txt_muted, size = 8),
    axis.title        = element_text(colour = txt_white, size = 9),
    plot.title        = element_text(colour = txt_white, face = "bold", size = 12, hjust = 0),
    plot.subtitle     = element_text(colour = txt_muted, size = 8, hjust = 0),
    legend.background = element_blank(),
    legend.key        = element_blank(),
    legend.text       = element_text(colour = txt_white, size = 8),
    legend.title      = element_blank(),
    legend.position   = "top",
    strip.text        = element_text(colour = txt_white, face = "bold", size = 9)
  )
}

# ── load data ─────────────────────────────────────────────────────────────────
if (!file.exists(daily_csv)) {
  stop("no performance CSV - run spyPerformance.py first")
}
df <- read.csv(daily_csv, stringsAsFactors = FALSE)
sm <- if (file.exists(json_path)) fromJSON(json_path) else list()

df <- df |>
  mutate(date = as.Date(date)) |>
  filter(!is.na(date))

resolved <- df |> filter(strat_equity != "" & !is.na(strat_equity)) |>
  mutate(strat_equity = as.numeric(strat_equity),
         bh_equity    = as.numeric(bh_equity),
         hit          = suppressWarnings(as.numeric(hit)))

if (nrow(resolved) < 2) {
  # Not enough data for a real chart - render a placeholder so the command still works
  p <- ggplot() +
    annotate("text", x = 0, y = 0.2, label = "SPY Paper Trading - Forward Track Record",
             colour = txt_white, size = 6, fontface = "bold") +
    annotate("text", x = 0, y = -0.1,
             label = sprintf("%d day(s) logged, %d resolved - come back after a few more sessions",
                             nrow(df), nrow(resolved)),
             colour = txt_muted, size = 4) +
    annotate("text", x = 0, y = -0.35,
             label = "The autopilot logs one row per trading day at 4:15 PM ET",
             colour = txt_muted, size = 3.5) +
    xlim(-1, 1) + ylim(-1, 1) +
    navy_theme() +
    theme(axis.text = element_blank(), axis.title = element_blank(),
          panel.grid.major = element_blank())
  ggsave(out_png, p, width = 10, height = 5.5, dpi = 110, bg = navy_bg)
  cat("ok:", normalizePath(out_png), "\n")
  quit(status = 0)
}

# ── panel 1: equity curves ────────────────────────────────────────────────────
eq_long <- resolved |>
  select(date, Strategy = strat_equity, `SPY Buy & Hold` = bh_equity) |>
  pivot_longer(-date, names_to = "series", values_to = "equity")

# broker equity (actual Alpaca account), rebased to 100 if present
if ("broker_equity" %in% names(resolved)) {
  brk <- resolved |>
    mutate(broker_equity = suppressWarnings(as.numeric(broker_equity))) |>
    filter(!is.na(broker_equity))
  if (nrow(brk) >= 2) {
    brk <- brk |> mutate(equity = 100 * broker_equity / broker_equity[1],
                         series = "Broker (actual)")
    eq_long <- bind_rows(eq_long, brk |> select(date, series, equity))
  }
}

live_sh <- if (!is.null(sm$live_sharpe)) sprintf("live Sharpe %.2f", sm$live_sharpe) else NULL
bh_sh   <- if (!is.null(sm$bh_sharpe))   sprintf("B&H Sharpe %.2f", sm$bh_sharpe) else NULL
wf_sh   <- if (!is.null(sm$wfcv_sharpe)) sprintf("WFCV backtest %.2f", sm$wfcv_sharpe) else NULL
sub1    <- paste(c(live_sh, bh_sh, wf_sh), collapse = "  |  ")

p1 <- ggplot(eq_long, aes(date, equity, colour = series)) +
  geom_hline(yintercept = 100, colour = navy_grid, linewidth = 0.4) +
  geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c("Strategy" = col_strat, "SPY Buy & Hold" = col_bh,
                                 "Broker (actual)" = col_broker)) +
  labs(title = "SPY Paper Trading - Forward Track Record",
       subtitle = sub1, x = NULL, y = "Equity (start = 100)") +
  navy_theme()

# ── panel 2: rolling 20d hit rate vs backtest expectation ─────────────────────
hit_df <- resolved |> filter(!is.na(hit))
p2 <- NULL
if (nrow(hit_df) >= 5) {
  k <- 20
  hit_df <- hit_df |>
    mutate(roll_hit = sapply(seq_len(n()), function(i) {
      lo <- max(1, i - k + 1); mean(hit[lo:i])
    }))
  exp_hit <- if (!is.null(sm$expected_hit) && !is.na(sm$expected_hit)) sm$expected_hit else NA
  p2 <- ggplot(hit_df, aes(date, roll_hit)) +
    geom_hline(yintercept = 0.5, colour = txt_muted, linetype = "dotted", linewidth = 0.4) +
    { if (!is.na(exp_hit)) geom_hline(yintercept = exp_hit, colour = col_gold,
                                      linetype = "dashed", linewidth = 0.5) } +
    geom_line(colour = col_strat, linewidth = 0.8) +
    geom_point(colour = col_strat, size = 0.8) +
    scale_y_continuous(labels = percent_format(accuracy = 1), limits = c(0, 1)) +
    labs(title = "Rolling Hit Rate (20d window)",
         subtitle = if (!is.na(exp_hit)) sprintf("gold dashed = WFCV expected %.0f%%", exp_hit * 100) else NULL,
         x = NULL, y = "Hit rate") +
    navy_theme()
}

# ── panel 3: drawdown ─────────────────────────────────────────────────────────
dd_df <- resolved |>
  mutate(peak = cummax(strat_equity),
         dd   = strat_equity / peak - 1)
p3 <- ggplot(dd_df, aes(date, dd)) +
  geom_area(fill = col_bh, alpha = 0.35) +
  geom_line(colour = col_bh, linewidth = 0.6) +
  scale_y_continuous(labels = percent_format()) +
  labs(title = "Strategy Drawdown", x = NULL, y = "Drawdown") +
  navy_theme()

# ── compose ───────────────────────────────────────────────────────────────────
if (!is.null(p2)) {
  combined <- p1 / (p2 | p3) + plot_layout(heights = c(1.5, 1))
} else {
  combined <- p1 / p3 + plot_layout(heights = c(1.6, 1))
}
combined <- combined & theme(plot.background = element_rect(fill = navy_bg, colour = NA))

ggsave(out_png, combined, width = 11, height = 7.5, dpi = 110, bg = navy_bg)
cat("ok:", normalizePath(out_png), "\n")
