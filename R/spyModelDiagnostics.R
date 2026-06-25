#!/usr/bin/env Rscript
# spyModelDiagnostics.R
# Usage: Rscript R/spyModelDiagnostics.R <out_png>
# Layout (5 rows):
#   Row 1: p1 (logistic scatter) | p2 (calibration)
#   Row 2: p3 (GBM 5d scatter)  | p4 (blend model viz)
#   Row 3: p_rolling             (rolling 63d acc - full width)
#   Row 4: p5                    (val dir acc by training run - full width)
#   Row 5: p6                    (WFCV cumul PnL - full width)

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(patchwork)
  library(scales)
  library(lubridate)
  library(zoo)
})

args    <- commandArgs(trailingOnly = TRUE)
out_png <- if (length(args) >= 1) args[1] else "outputs/markets/spy_diagnostics.png"

setwd("/home/jhcv/discordBot")

# ── palette / theme ────────────────────────────────────────────────────────────
navy_bg    <- "#0d1b2a"
navy_panel <- "#112240"
navy_grid  <- "#1e3a5f"
txt_white  <- "#e8eaf6"
txt_muted  <- "#90a4ae"
col_up     <- "#66bb6a"   # green
col_dn     <- "#ef5350"   # red
col_blue   <- "#4fc3f7"   # logistic / strategy
col_orange <- "#ffa726"   # GBM ensemble
col_teal   <- "#26a69a"
col_gold   <- "#ffd54f"

navy_theme <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace% theme(
    plot.background   = element_rect(fill = navy_bg,    colour = NA),
    panel.background  = element_rect(fill = navy_panel, colour = NA),
    panel.grid.major  = element_line(colour = navy_grid, linewidth = 0.3),
    panel.grid.minor  = element_blank(),
    text              = element_text(colour = txt_white),
    axis.text         = element_text(colour = txt_muted, size = 8),
    axis.title        = element_text(colour = txt_white, size = 9),
    plot.title        = element_text(colour = txt_white, face = "bold", size = 11),
    plot.subtitle     = element_text(colour = txt_muted, size = 7),
    legend.background = element_blank(),
    legend.key        = element_blank(),
    legend.text       = element_text(colour = txt_white, size = 8),
    strip.text        = element_text(colour = txt_white, face = "bold", size = 9)
  )
}

# ── data paths ─────────────────────────────────────────────────────────────────
base_dir  <- "outputs/features/markets"
log_path  <- file.path(base_dir, "spy_experiment_log_copy.csv")
log_dir   <- "models/meta"

logistic_csv     <- file.path(base_dir, "eval_spy_next_dir_1d_logistic.csv")
gbm_ens_csv      <- file.path(base_dir, "eval_spy_next_dir_1d_gbm_ensemble.csv")
gbm_5d_csv       <- file.path(base_dir, "eval_spy_next_ret_5d_gbm.csv")

wfcv_csv <- file.path(base_dir, "spy_wfcv_pnl.csv")   # may not exist

# ── load data ─────────────────────────────────────────────────────────────────
log_df <- if (file.exists(log_path)) {
  read.csv(log_path, stringsAsFactors = FALSE)
} else {
  data.frame()
}

logistic_df  <- if (file.exists(logistic_csv))  read.csv(logistic_csv,  stringsAsFactors=FALSE) else data.frame()
gbm_ens_df   <- if (file.exists(gbm_ens_csv))   read.csv(gbm_ens_csv,   stringsAsFactors=FALSE) else data.frame()
gbm_5d_df    <- if (file.exists(gbm_5d_csv))    read.csv(gbm_5d_csv,    stringsAsFactors=FALSE) else data.frame()

if (nrow(logistic_df) > 0) logistic_df$date  <- as.Date(logistic_df$date)
if (nrow(gbm_ens_df)  > 0) gbm_ens_df$date   <- as.Date(gbm_ens_df$date)
if (nrow(gbm_5d_df)   > 0) gbm_5d_df$date    <- as.Date(gbm_5d_df$date)

today_str <- format(Sys.Date(), "%Y-%m-%d")

# ── p1: logistic 1d direction scatter ──────────────────────────────────────────
make_p1 <- function() {
  if (nrow(logistic_df) == 0) {
    return(ggplot() + labs(title = "1d Direction - Logistic | No data") + navy_theme())
  }
  df <- logistic_df %>%
    mutate(actual_lbl = ifelse(actual == 1, "Up (1)", "Down (0)"))

  # smooth LOESS line
  sm <- df %>%
    arrange(prob_up) %>%
    mutate(sm = predict(loess(actual ~ prob_up, data = ., span = 0.5), .))

  ggplot(df, aes(x = prob_up, y = actual, colour = actual_lbl)) +
    geom_jitter(height = 0.03, alpha = 0.55, size = 1.1) +
    geom_line(data = sm, aes(x = prob_up, y = sm), colour = txt_white,
              linewidth = 0.9, inherit.aes = FALSE) +
    geom_vline(xintercept = 0.53, colour = col_gold, linetype = "dashed", linewidth = 0.6) +
    scale_colour_manual(values = c("Down (0)" = col_dn, "Up (1)" = col_up), name = "Actual") +
    scale_x_continuous(labels = percent_format(1)) +
    labs(title = "1d Direction - Logistic | Val Set",
         x = "Predicted P(UP)", y = "Actual Outcome") +
    navy_theme()
}

# ── p2: probability calibration ────────────────────────────────────────────────
make_p2 <- function() {
  if (nrow(logistic_df) == 0) {
    return(ggplot() + labs(title = "Probability Calibration | No data") + navy_theme())
  }
  df <- logistic_df %>%
    mutate(bin = cut(prob_up, breaks = seq(0, 1, 0.1), include.lowest = TRUE)) %>%
    group_by(bin) %>%
    summarise(obs_freq = mean(actual == 1), pred_mid = mean(prob_up), n = n(), .groups = "drop") %>%
    filter(!is.na(bin))

  ggplot(df, aes(x = pred_mid, y = obs_freq, size = n)) +
    geom_abline(slope = 1, intercept = 0, colour = txt_muted, linetype = "dashed") +
    geom_line(colour = col_blue, linewidth = 0.8) +
    geom_point(colour = col_blue) +
    scale_x_continuous(labels = percent_format(1), limits = c(0, 1)) +
    scale_y_continuous(labels = percent_format(1), limits = c(0, 1)) +
    scale_size_continuous(range = c(2, 8), name = "n") +
    labs(title = "Probability Calibration",
         x = "Predicted P(UP)", y = "Observed Frequency") +
    navy_theme()
}

# ── p3: GBM 5d return scatter ──────────────────────────────────────────────────
make_p3 <- function() {
  if (nrow(gbm_5d_df) == 0) {
    return(ggplot() + labs(title = "5d Return - GBM | No data") + navy_theme())
  }
  df <- gbm_5d_df %>%
    filter(is.finite(actual), is.finite(predicted))

  vix_col <- if ("vix_level" %in% names(df)) df$vix_level else rep(20, nrow(df))

  sm <- df %>%
    arrange(predicted) %>%
    mutate(sm = predict(loess(actual ~ predicted, data = ., span = 0.6), .))

  ggplot(df, aes(x = predicted, y = actual, colour = vix_col)) +
    geom_point(alpha = 0.5, size = 1.1) +
    geom_line(data = sm, aes(x = predicted, y = sm), colour = txt_white,
              linewidth = 0.9, inherit.aes = FALSE) +
    geom_hline(yintercept = 0, colour = txt_muted, linetype = "dashed", linewidth = 0.5) +
    geom_vline(xintercept = 0, colour = txt_muted, linetype = "dashed", linewidth = 0.5) +
    scale_colour_gradient(low = col_gold, high = col_dn, name = "VIX") +
    scale_x_continuous(labels = percent_format(1)) +
    scale_y_continuous(labels = percent_format(1)) +
    labs(title = "5d Return - GBM | Actual vs Predicted",
         x = "Predicted Return", y = "Actual Return") +
    navy_theme()
}

# ── p4: blend model visualization ──────────────────────────────────────────────
make_p4_blend <- function() {
  if (nrow(logistic_df) == 0 || nrow(gbm_ens_df) == 0) {
    return(ggplot() + labs(title = "Blend Model | No data") + navy_theme())
  }

  # merge on date
  df <- logistic_df %>%
    select(date, actual, prob_up_log = prob_up, vol_regime) %>%
    inner_join(gbm_ens_df %>% select(date, prob_up_gbm = prob_up), by = "date") %>%
    mutate(
      blend      = (prob_up_log + prob_up_gbm) / 2,
      outcome    = ifelse(actual == 1, "Up", "Down"),
      # decile bin
      decile     = cut(blend, breaks = quantile(blend, probs = seq(0, 1, 0.1)),
                       include.lowest = TRUE, labels = FALSE)
    )

  # --- sub-plot A: density by outcome ---
  # We'll do a manual density-like strip using geom_density + facets not available in patchwork sub-sub
  # Use a single violin / density overlay approach

  # Decile calibration: actual up-rate by decile bin
  cal <- df %>%
    group_by(decile) %>%
    summarise(
      up_rate  = mean(actual == 1),
      mid      = mean(blend),
      n        = n(),
      .groups  = "drop"
    )

  # Precision/coverage by threshold (from 0.45 to 0.75)
  thresholds <- seq(0.45, 0.75, by = 0.01)
  pc <- lapply(thresholds, function(t) {
    long_side <- df %>% filter(blend >= t)
    prec <- if (nrow(long_side) > 0) mean(long_side$actual == 1) else NA
    cov  <- nrow(long_side) / nrow(df)
    data.frame(threshold = t, precision = prec, coverage = cov)
  })
  pc_df <- bind_rows(pc)

  # Main panel: density by outcome + decile calibration as second y
  # We do two geom layers on one panel, with decile bars + density ribbons

  # density data per outcome
  dens_up <- density(df$blend[df$actual == 1], adjust = 1.2, from = 0.2, to = 0.85)
  dens_dn <- density(df$blend[df$actual == 0], adjust = 1.2, from = 0.2, to = 0.85)

  dens_df <- bind_rows(
    data.frame(blend = dens_up$x, density = dens_up$y / max(dens_up$y), outcome = "Up"),
    data.frame(blend = dens_dn$x, density = dens_dn$y / max(dens_dn$y), outcome = "Down")
  )

  p_dens <- ggplot() +
    # density ribbons (normalized to [0,1])
    geom_ribbon(data = dens_df %>% filter(outcome == "Up"),
                aes(x = blend, ymin = 0, ymax = density),
                fill = col_up, alpha = 0.35) +
    geom_ribbon(data = dens_df %>% filter(outcome == "Down"),
                aes(x = blend, ymin = 0, ymax = density),
                fill = col_dn, alpha = 0.35) +
    geom_line(data = dens_df, aes(x = blend, y = density, colour = outcome),
              linewidth = 0.7) +
    # decile up-rate dots (scale to normalized [0,1] range)
    geom_point(data = cal, aes(x = mid, y = up_rate),
               colour = col_gold, size = 2.5, shape = 21, fill = col_gold) +
    geom_line(data = cal, aes(x = mid, y = up_rate),
              colour = col_gold, linewidth = 0.6, linetype = "dotted") +
    # threshold lines
    geom_vline(xintercept = 0.51, colour = col_gold,  linetype = "dashed", linewidth = 0.55) +
    geom_vline(xintercept = 0.53, colour = "#e0e0e0", linetype = "dashed", linewidth = 0.55) +
    geom_hline(yintercept = 0.50, colour = txt_muted, linetype = "dotted", linewidth = 0.4) +
    annotate("text", x = 0.51, y = 0.97, label = "t=0.51\nchop", colour = col_gold,
             size = 2.5, hjust = 1.05, vjust = 1) +
    annotate("text", x = 0.53, y = 0.97, label = "t=0.53\nbull/bear", colour = "#e0e0e0",
             size = 2.5, hjust = -0.05, vjust = 1) +
    scale_colour_manual(values = c("Up" = col_up, "Down" = col_dn), name = NULL) +
    scale_x_continuous(labels = function(x) paste0(round(x * 100), "%"), limits = c(0.2, 0.85)) +
    scale_y_continuous(labels = function(x) paste0(round(x * 100), "%"), limits = c(0, 1.05)) +
    labs(title = "Blend Model (50/50 Logistic + GBM Ensemble)",
         subtitle = "Density by outcome (shaded) | Decile up-rate (gold dots) | Thresholds (dashed)",
         x = "Blend P(UP)", y = "Norm. Density / Up-rate") +
    navy_theme() +
    theme(legend.position = c(0.05, 0.92),
          legend.direction = "horizontal")

  p_dens
}

# ── p_rolling: rolling 63-day directional accuracy ─────────────────────────────
make_p_rolling <- function() {
  if (nrow(logistic_df) == 0) {
    return(ggplot() + labs(title = "Rolling Accuracy | No data") + navy_theme())
  }

  win <- 63  # ~3 months

  # blend: merge logistic + gbm_ensemble
  blend_df <- if (nrow(gbm_ens_df) > 0) {
    logistic_df %>%
      select(date, actual, prob_up_log = prob_up) %>%
      inner_join(gbm_ens_df %>% select(date, prob_up_gbm = prob_up), by = "date") %>%
      mutate(blend = (prob_up_log + prob_up_gbm) / 2,
             correct_blend = as.integer((blend > 0.5) == (actual == 1)))
  } else {
    logistic_df %>%
      mutate(blend = prob_up, correct_blend = as.integer((blend > 0.5) == (actual == 1)))
  }

  blend_df <- blend_df %>%
    arrange(date) %>%
    mutate(
      roll_acc = zoo::rollmean(correct_blend, k = win, fill = NA, align = "right")
    )

  best_val <- max(blend_df$roll_acc, na.rm = TRUE)
  coin_flip <- 0.50

  ggplot(blend_df, aes(x = date)) +
    # green ribbon above 50%
    geom_ribbon(aes(ymin = pmax(pmin(roll_acc, 1), 0.50),
                    ymax = pmax(roll_acc, 0.50)),
                fill = col_up, alpha = 0.45) +
    # red ribbon below 50%
    geom_ribbon(aes(ymin = pmin(pmax(roll_acc, 0), 0.50),
                    ymax = pmin(roll_acc, 0.50)),
                fill = col_dn, alpha = 0.45) +
    geom_line(aes(y = roll_acc), colour = col_blue, linewidth = 0.8) +
    geom_hline(yintercept = coin_flip, colour = txt_muted, linetype = "dotted", linewidth = 0.5) +
    geom_hline(yintercept = best_val, colour = col_teal, linetype = "dashed", linewidth = 0.5) +
    annotate("text", x = min(blend_df$date, na.rm=TRUE),
             y = best_val + 0.005, label = sprintf("Best val acc %.1f%%", best_val * 100),
             colour = col_teal, size = 2.8, hjust = 0) +
    annotate("text", x = min(blend_df$date, na.rm=TRUE),
             y = coin_flip - 0.008, label = "Coin flip",
             colour = txt_muted, size = 2.8, hjust = 0) +
    scale_x_date(date_breaks = "3 months", date_labels = "%b %Y") +
    scale_y_continuous(labels = function(x) paste0(round(x * 100), "%"),
                       limits = c(0.35, 0.85)) +
    labs(title = sprintf("Rolling %d-Day Blend Directional Accuracy", win),
         x = "Date", y = "Accuracy") +
    navy_theme()
}

# ── p5: val dir accuracy by training run ───────────────────────────────────────
make_p5 <- function() {
  if (nrow(log_df) == 0) {
    return(ggplot() + labs(title = "Val Dir Acc by Training Run | No log") + navy_theme())
  }

  n_runs <- 41  # keep last 41 rows per model for aligned x-axis

  log_dir_df <- log_df %>%
    filter(model_type %in% c("logistic", "gbm_ensemble"),
           !is.na(val_dir_acc)) %>%
    group_by(model_type) %>%
    slice_tail(n = n_runs) %>%
    mutate(run_n = row_number()) %>%
    ungroup()

  if (nrow(log_dir_df) == 0) {
    return(ggplot() + labs(title = "Val Dir Acc | No qualifying rows") + navy_theme())
  }

  best_log <- log_dir_df %>% filter(model_type == "logistic") %>%
    summarise(best = max(val_dir_acc, na.rm = TRUE)) %>% pull(best)
  best_gbm <- log_dir_df %>% filter(model_type == "gbm_ensemble") %>%
    summarise(best = max(val_dir_acc, na.rm = TRUE)) %>% pull(best)

  ggplot(log_dir_df, aes(x = run_n, y = val_dir_acc,
                          colour = model_type, group = model_type)) +
    geom_hline(yintercept = best_log, colour = col_blue,   linetype = "dashed", linewidth = 0.5) +
    geom_hline(yintercept = best_gbm, colour = col_orange, linetype = "dashed", linewidth = 0.5) +
    geom_line(linewidth = 0.7) +
    geom_point(size = 2) +
    scale_colour_manual(values = c("logistic" = col_blue, "gbm_ensemble" = col_orange),
                        labels = c("logistic" = "Logistic (1d Dir)",
                                   "gbm_ensemble" = "GBM Ensemble (1d Dir)"),
                        name = NULL) +
    scale_y_continuous(labels = function(x) paste0(round(x * 100, 1), "%")) +
    scale_x_continuous(breaks = seq(0, n_runs, by = 5)) +
    labs(title = "Val Dir Accuracy by Training Run (aligned, same x-axis)",
         subtitle = "Logistic (blue) vs GBM Ensemble (orange) - last 41 shared runs - dashed = best per model",
         x = "Training Run", y = "Val Dir Accuracy") +
    navy_theme() +
    theme(legend.position = "top")
}

# ── p6: WFCV cumulative PnL ────────────────────────────────────────────────────
make_p6 <- function() {
  # Try to read from log
  if (nrow(log_df) == 0) {
    return(ggplot() + labs(title = "WFCV PnL | No log") + navy_theme())
  }

  wfcv_file <- file.path(base_dir, "spy_wfcv_pnl.csv")
  if (!file.exists(wfcv_file)) {
    return(ggplot() + labs(title = "WFCV Cumul PnL | spy_wfcv_pnl.csv not found") + navy_theme())
  }

  pnl <- read.csv(wfcv_file, stringsAsFactors = FALSE)
  pnl$date <- as.Date(pnl$date)

  # Normalise column names: support both strategy_ret and strat_ret
  if ("strat_ret" %in% names(pnl) && !"strategy_ret" %in% names(pnl)) {
    pnl <- pnl %>% rename(strategy_ret = strat_ret)
  }
  if (!all(c("date", "strategy_ret", "bh_ret") %in% names(pnl))) {
    return(ggplot() + labs(title = sprintf("WFCV PnL | cols: %s", paste(names(pnl), collapse=","))) + navy_theme())
  }

  # Use pre-computed cumulative if present, else compute
  pnl <- pnl %>%
    arrange(date) %>%
    mutate(
      strat_cumul = if ("cum_strat" %in% names(.)) cum_strat - 1
                    else cumprod(1 + replace_na(strategy_ret, 0)) - 1,
      bh_cumul    = if ("cum_bh" %in% names(.)) cum_bh - 1
                    else cumprod(1 + replace_na(bh_ret, 0)) - 1
    )

  # fold lines
  fold_lines <- if ("fold" %in% names(pnl)) {
    pnl %>% group_by(fold) %>% summarise(fold_start = min(date), .groups = "drop")
  } else data.frame()

  # Latest log row for subtitle
  last_row <- log_df %>%
    filter(model_type == "logistic", !is.na(wfcv_dir_acc_mean)) %>%
    slice_tail(n = 1)
  sharpe_lbl <- if (nrow(last_row) > 0) {
    wfcv_acc  <- if ("wfcv_dir_acc_mean" %in% names(last_row)) sprintf("WFCV %.1f%%", last_row$wfcv_dir_acc_mean * 100) else ""
    hold_acc  <- if ("holdout_dir_acc"   %in% names(last_row) && !is.na(last_row$holdout_dir_acc))
                   sprintf("Holdout %.1f%% (n=%s)", last_row$holdout_dir_acc * 100,
                           ifelse("holdout_n_rows" %in% names(last_row), last_row$holdout_n_rows, "?")) else ""
    paste(c(wfcv_acc, hold_acc), collapse = " | ")
  } else ""

  p <- ggplot(pnl, aes(x = date)) +
    geom_line(aes(y = strat_cumul), colour = col_blue,  linewidth = 0.8) +
    geom_line(aes(y = bh_cumul),    colour = "#90a4ae", linewidth = 0.7, linetype = "solid") +
    scale_x_date(date_breaks = "1 year", date_labels = "%Y") +
    scale_y_continuous(labels = function(x) paste0(round(x * 100), "%")) +
    labs(title = "WFCV Cumulative PnL vs Buy & Hold",
         subtitle = sharpe_lbl,
         x = "Date", y = "Cumulative Return") +
    navy_theme()

  if (nrow(fold_lines) > 0) {
    p <- p + geom_vline(data = fold_lines,
                        aes(xintercept = fold_start),
                        colour = col_gold, linetype = "dashed", linewidth = 0.4, alpha = 0.7) +
      geom_text(data = fold_lines,
                aes(x = fold_start, y = max(pnl$strat_cumul, na.rm = TRUE) * 0.95,
                    label = paste0("F", fold)),
                colour = col_gold, size = 2.5, hjust = -0.2)
  }

  # drawdown sub-panel if present
  if ("drawdown" %in% names(pnl)) {
    p_dd <- ggplot(pnl, aes(x = date)) +
      geom_ribbon(aes(ymin = drawdown, ymax = 0), fill = col_dn, alpha = 0.55) +
      geom_line(aes(y = drawdown), colour = col_dn, linewidth = 0.5) +
      scale_x_date(date_breaks = "1 year", date_labels = "%Y") +
      scale_y_continuous(labels = function(x) paste0(round(x * 100, 1), "%")) +
      labs(x = "Date", y = "Drawdown",
           caption = "Source: SPY ML Model Library") +
      navy_theme()

    return(p / p_dd + plot_layout(heights = c(3, 1)))
  }

  p
}

# ── assemble ───────────────────────────────────────────────────────────────────
p1       <- make_p1()
p2       <- make_p2()
p3       <- make_p3()
p4       <- make_p4_blend()
p_roll   <- make_p_rolling()
p5       <- make_p5()
p6       <- make_p6()

layout <- (
  (p1 | p2) /
  (p3 | p4) /
  p_roll /
  p5 /
  p6
) +
  plot_layout(heights = c(2.2, 2.2, 1.4, 1.6, 2.5)) +
  plot_annotation(
    title    = "SPY ML Model Diagnostics",
    subtitle = sprintf("Val set: last 252 trading days | Generated: %s", today_str),
    theme    = theme(
      plot.background = element_rect(fill = navy_bg, colour = NA),
      plot.title      = element_text(colour = txt_white, face = "bold", size = 15, hjust = 0.5),
      plot.subtitle   = element_text(colour = txt_muted, size = 9,  hjust = 0.5)
    )
  )

# ── save ───────────────────────────────────────────────────────────────────────
dir.create(dirname(out_png), recursive = TRUE, showWarnings = FALSE)
ggsave(out_png, layout, width = 14, height = 22, dpi = 140, bg = navy_bg)
cat(sprintf("[spyModelDiagnostics] saved -> %s\n", out_png))
