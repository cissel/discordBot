#!/usr/bin/env Rscript
# spyBacktestPlot.R  — navy theme backtest chart, called after every run
# Usage: Rscript R/spyBacktestPlot.R <run_label> <log_file> <out_png>
# Args:  run_label  e.g. "Run 34"
#        log_file   e.g. /tmp/spy_train34.log
#        out_png    e.g. outputs/markets/spy_backtest_run34.png

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(patchwork)
  library(scales)
  library(lubridate)
})

# ── args ──────────────────────────────────────────────────────────────────────
args      <- commandArgs(trailingOnly = TRUE)
run_label <- if (length(args) >= 1) args[1] else "Run ??"
log_file  <- if (length(args) >= 2) args[2] else "/tmp/spy_train34.log"
out_png   <- if (length(args) >= 3) args[3] else "outputs/markets/spy_backtest_latest.png"

setwd("/home/jhcv/discordBot")

# ── navy theme ─────────────────────────────────────────────────────────────────
navy_bg    <- "#0d1b2a"
navy_panel <- "#112240"
navy_grid  <- "#1e3a5f"
txt_white  <- "#e8eaf6"
txt_muted  <- "#90a4ae"
col_strat  <- "#4fc3f7"   # light blue
col_bh     <- "#ef5350"   # red-ish
col_kelly  <- "#ab47bc"   # purple
col_best   <- "#26a69a"   # teal

navy_theme <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace% theme(
    plot.background   = element_rect(fill = navy_bg,    colour = NA),
    panel.background  = element_rect(fill = navy_panel, colour = NA),
    panel.grid.major  = element_line(colour = navy_grid, linewidth = 0.3),
    panel.grid.minor  = element_blank(),
    text              = element_text(colour = txt_white),
    axis.text         = element_text(colour = txt_muted, size = 8),
    axis.title        = element_text(colour = txt_white, size = 9),
    plot.title        = element_text(colour = txt_white, face = "bold", size = 12),
    plot.subtitle     = element_text(colour = txt_muted, size = 8),
    legend.background = element_blank(),
    legend.key        = element_blank(),
    legend.text       = element_text(colour = txt_white, size = 9),
    strip.text        = element_text(colour = txt_white, face = "bold", size = 9)
  )
}

# ── parse log ─────────────────────────────────────────────────────────────────
log_lines <- readLines(log_file, warn = FALSE)

parse_val <- function(pattern, lines, group = 1, default = NA_real_) {
  hits <- grep(pattern, lines, value = TRUE)
  if (!length(hits)) return(default)
  m <- regmatches(hits[1], regexpr(pattern, hits[1], perl = TRUE))
  cap <- regmatches(hits[1], regexec(pattern, hits[1], perl = TRUE))[[1]]
  if (length(cap) > group) as.numeric(cap[group + 1]) else default
}

# WFCV
logistic_acc  <- parse_val("Logistic 1d Dir\\s+([0-9.]+)\\s+", log_lines)
gbm_acc       <- parse_val("GBM Ensemble 1d\\s+([0-9.]+)\\s+", log_lines)
wfcv_sharpe   <- parse_val("Strategy:.*Sharpe=([0-9.]+)", log_lines)
wfcv_total    <- parse_val("Strategy:.*total=([0-9.]+)%", log_lines)
bh_sharpe     <- parse_val("B&H:.*Sharpe=([0-9.]+)", log_lines)
bh_total      <- parse_val("B&H:.*total=([0-9.]+)%", log_lines)

# Holdout
holdout_acc   <- parse_val("HOLDOUT.*acc=([0-9.]+)", log_lines)
holdout_auc   <- parse_val("HOLDOUT.*AUC=([0-9.]+)", log_lines)

# Backtest strategies
parse_strategy <- function(label_pat, lines) {
  hits <- grep(label_pat, lines, value = TRUE)
  if (!length(hits)) return(list(sharpe=NA, ret=NA, dd=NA))
  l <- hits[1]
  sh <- as.numeric(sub(".*Sharpe=([0-9.-]+).*", "\\1", l))
  rt <- as.numeric(sub(".*ret=([0-9.-]+)%.*", "\\1", l))
  dd <- as.numeric(sub(".*dd=(-[0-9.]+)%.*", "\\1", l))
  list(sharpe=sh, ret=rt, dd=dd)
}

s_half54  <- parse_strategy("Half-bear, t=0\\.54", log_lines)
s_best    <- parse_strategy("Best threshold", log_lines)
s_kelly   <- parse_strategy("Frac-Kelly\\(50%\\)", log_lines)
s_zero    <- parse_strategy("Zero-bear", log_lines)
s_kill    <- parse_strategy("Kill\\(bear", log_lines)

best_t    <- parse_val("Best threshold: ([0-9.]+)", log_lines)

strategies <- tibble(
  label   = c(
    sprintf("Best (t=%.2f)", ifelse(is.na(best_t), 0.60, best_t)),
    "Half-bear (t=0.54)",
    "Zero-bear (t=0.54)",
    "Kill switch (t=0.53)",
    "Kelly(50%) half-bear",
    "SPY Buy & Hold"
  ),
  sharpe  = c(s_best$sharpe, s_half54$sharpe, s_zero$sharpe, s_kill$sharpe, s_kelly$sharpe, bh_sharpe),
  ann_ret = c(s_best$ret,    s_half54$ret,    s_zero$ret,    s_kill$ret,    s_kelly$ret,    parse_val("B&H:.*ann=([0-9.]+)%", log_lines)),
  max_dd  = c(s_best$dd,     s_half54$dd,     s_zero$dd,     s_kill$dd,     s_kelly$dd,     parse_val("B&H:.*MaxDD=(-[0-9.]+)%", log_lines)),
  cat     = c("strat","strat","strat","strat","kelly","bh")
) %>%
  mutate(label = factor(label, levels = rev(label)))

cat_cols <- c(strat = col_strat, bh = col_bh, kelly = col_kelly)

# ── 1. WFCV cumulative curve ───────────────────────────────────────────────────
pnl_path <- "outputs/features/markets/spy_wfcv_pnl.csv"
pnl <- tryCatch(
  read.csv(pnl_path, stringsAsFactors = FALSE) %>%
    mutate(date = as.Date(date)),
  error = function(e) NULL
)

fold_bounds <- if (!is.null(pnl)) {
  pnl %>% group_by(fold) %>%
    summarise(start = min(date), end = max(date), .groups = "drop")
} else NULL

p_curve <- if (!is.null(pnl)) {
  pnl_long <- pnl %>%
    select(date, fold, strat_ret, bh_ret) %>%
    rename(`Strategy` = strat_ret, `SPY B&H` = bh_ret) %>%
    pivot_longer(c(Strategy, `SPY B&H`), names_to = "series", values_to = "ret") %>%
    group_by(series) %>% arrange(date) %>%
    mutate(cum_pct = (cumprod(1 + ret) - 1) * 100) %>% ungroup()

  ggplot(pnl_long, aes(x = date, y = cum_pct, colour = series)) +
    geom_vline(data = fold_bounds[-1,], aes(xintercept = as.numeric(start)),
               colour = navy_grid, linetype = "dashed", linewidth = 0.4, inherit.aes = FALSE) +
    geom_text(data = fold_bounds,
              aes(x = start + (end - start)/2, y = Inf, label = paste0("F", fold)),
              inherit.aes = FALSE, colour = txt_muted, vjust = 1.6, size = 2.8) +
    geom_line(linewidth = 0.9, alpha = 0.92) +
    geom_hline(yintercept = 0, colour = txt_muted, linewidth = 0.3) +
    scale_colour_manual(values = c("Strategy" = col_strat, "SPY B&H" = col_bh)) +
    scale_y_continuous(labels = function(x) paste0(x, "%")) +
    scale_x_date(date_breaks = "6 months", date_labels = "%b '%y") +
    labs(
      title    = sprintf("WFCV Cumulative Returns - %s", run_label),
      subtitle = sprintf(
        "Strategy: %.1f%% total | Sharpe %.3f    B&H: %.1f%% total | Sharpe %.3f    (5bps tx, half-bear, t=0.54)",
        ifelse(is.na(wfcv_total), 0, wfcv_total),
        ifelse(is.na(wfcv_sharpe), 0, wfcv_sharpe),
        ifelse(is.na(bh_total), 0, bh_total),
        ifelse(is.na(bh_sharpe), 0, bh_sharpe)
      ),
      x = NULL, y = "Cumulative Return (%)", colour = NULL
    ) +
    navy_theme()
} else {
  ggplot() + labs(title = "WFCV PnL CSV not found") + navy_theme()
}

# ── 2. Fold-level acc heatmap ──────────────────────────────────────────────────
# ── fold heatmap data — parse directly by line number anchor ─────────────────
parse_fold_accs <- function(model_pat, lines) {
  hits <- grep(model_pat, lines, fixed = FALSE)
  if (!length(hits)) return(rep(NA_real_, 5))
  start <- hits[length(hits)]
  accs <- c()
  for (i in seq(start + 1, min(start + 20, length(lines)))) {
    l <- lines[i]
    if (grepl("fold [1-5]", l, fixed = FALSE) && grepl("acc=", l, fixed = TRUE)) {
      v <- as.numeric(regmatches(l, regexpr("[0-9]+\\.[0-9]+", l)))
      if (!is.na(v)) accs <- c(accs, v)
    } else if (length(accs) > 0) break
  }
  # ensure exactly 5 elements
  if (length(accs) > 5) accs <- accs[1:5]
  if (length(accs) < 5) accs <- c(accs, rep(NA_real_, 5 - length(accs)))
  accs
}

log_accs <- parse_fold_accs("Logistic 1d Dir.*dir_acc", log_lines)
gbm_accs <- parse_fold_accs("GBM Ensemble 1d.*dir_acc", log_lines)

fold_df <- bind_rows(
  data.frame(fold = paste0("F", 1:5), model = "Logistic", acc = log_accs, stringsAsFactors = FALSE),
  data.frame(fold = paste0("F", 1:5), model = "GBM",      acc = gbm_accs, stringsAsFactors = FALSE)
) %>% mutate(
  fold  = factor(fold, levels = paste0("F", 1:5)),
  model = factor(model, levels = c("GBM","Logistic")),
  label = ifelse(is.na(acc), "?", sprintf("%.1f%%", acc * 100))
)

p_heat <- ggplot(fold_df, aes(x = fold, y = model, fill = acc)) +
  geom_tile(colour = navy_bg, linewidth = 0.8) +
  geom_text(aes(label = label), colour = txt_white, size = 3.2, fontface = "bold") +
  scale_fill_gradient2(
    low = "#b71c1c", mid = "#37474f", high = "#00897b",
    midpoint = 0.53, na.value = navy_grid,
    labels = percent_format(accuracy = 1),
    name = "Dir Acc"
  ) +
  labs(title = "Fold Dir Acc by Model", x = NULL, y = NULL) +
  navy_theme() +
  theme(legend.position = "right",
        panel.grid = element_blank(),
        axis.text  = element_text(colour = txt_white, size = 9))

# ── 3. Strategy Sharpe bars ────────────────────────────────────────────────────
p_sharpe <- ggplot(strategies %>% filter(!is.na(sharpe)),
                   aes(x = label, y = sharpe, fill = cat)) +
  geom_col(width = 0.65, alpha = 0.88) +
  geom_text(aes(label = sprintf("%.3f", sharpe),
                hjust = ifelse(sharpe >= 0, -0.1, 1.1)),
            colour = txt_white, size = 3.0) +
  coord_flip(clip = "off") +
  scale_fill_manual(values = cat_cols, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.25))) +
  labs(title = "Sharpe (val set)", x = NULL, y = "Sharpe") +
  navy_theme()

# ── 4. Ann Return bars ────────────────────────────────────────────────────────
p_ret <- ggplot(strategies %>% filter(!is.na(ann_ret)),
                aes(x = label, y = ann_ret, fill = cat)) +
  geom_col(width = 0.65, alpha = 0.88) +
  geom_text(aes(label = sprintf("%.1f%%", ann_ret),
                hjust = ifelse(ann_ret >= 0, -0.1, 1.1)),
            colour = txt_white, size = 3.0) +
  coord_flip(clip = "off") +
  scale_fill_manual(values = cat_cols, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.25)),
                     labels = function(x) paste0(x, "%")) +
  labs(title = "Ann Return (val set)", x = NULL, y = "Ann Ret %") +
  navy_theme()

# ── 5. Holdout + WFCV summary scoreboard ──────────────────────────────────────
score_df <- tibble(
  metric = c("WFCV Logistic", "WFCV GBM", "WFCV Sharpe", "Holdout Acc", "Holdout AUC",
             "Kelly(50%) Sharpe", "B&H Sharpe"),
  value  = c(
    ifelse(is.na(logistic_acc), NA, sprintf("%.2f%%", logistic_acc * 100)),
    ifelse(is.na(gbm_acc),      NA, sprintf("%.2f%%", gbm_acc * 100)),
    ifelse(is.na(wfcv_sharpe),  NA, sprintf("%.3f",   wfcv_sharpe)),
    ifelse(is.na(holdout_acc),  NA, sprintf("%.2f%%", holdout_acc * 100)),
    ifelse(is.na(holdout_auc),  NA, sprintf("%.4f",   holdout_auc)),
    ifelse(is.na(s_kelly$sharpe), NA, sprintf("%.3f", s_kelly$sharpe)),
    ifelse(is.na(bh_sharpe),    NA, sprintf("%.3f",   bh_sharpe))
  ),
  highlight = c(FALSE, TRUE, FALSE, TRUE, FALSE, TRUE, FALSE)
)

p_score <- ggplot(score_df, aes(x = 0, y = rev(seq_len(nrow(score_df))),
                                 label = paste0(metric, ":  ", value))) +
  geom_text(aes(colour = highlight), hjust = 0, size = 3.5, fontface = "bold") +
  scale_colour_manual(values = c("FALSE" = txt_muted, "TRUE" = col_kelly), guide = "none") +
  scale_x_continuous(limits = c(0, 1)) +
  labs(title = sprintf("%s Scorecard", run_label), x = NULL, y = NULL) +
  navy_theme() +
  theme(axis.text = element_blank(), panel.grid = element_blank(),
        axis.ticks = element_blank())

# ── assemble ──────────────────────────────────────────────────────────────────
top    <- p_curve
mid    <- p_heat | p_score
bottom <- p_sharpe | p_ret

final <- (top / mid / bottom) +
  plot_layout(heights = c(1.8, 0.9, 1.0)) +
  plot_annotation(
    caption = sprintf(
      "%s | WFCV 2018-2023 (5 folds) | Holdout 2024+ locked | 5bps tx cost | blue=strategy, red=B&H, purple=Kelly",
      run_label
    ),
    theme = theme(
      plot.background = element_rect(fill = navy_bg, colour = NA),
      plot.caption    = element_text(colour = "#546e7a", size = 7)
    )
  )

dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)
ggsave(out_png, final, width = 14, height = 10, dpi = 150, bg = navy_bg)
cat(sprintf("[spyBacktestPlot] saved -> %s\n", out_png))
