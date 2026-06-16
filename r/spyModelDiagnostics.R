# spyModelDiagnostics.R
# SPY ML model diagnostics - navy theme, 5 panels
# Usage: Rscript r/spyModelDiagnostics.R [output_path]
# Default output: outputs/markets/spy_diagnostics.png

for (pkg in c("ggplot2", "patchwork", "dplyr", "tidyr", "readr", "scales", "lubridate")) {
  if (!requireNamespace(pkg, quietly = TRUE))
    install.packages(pkg, repos = "https://cran.rstudio.com/", quiet = TRUE)
}

suppressPackageStartupMessages({
  library(ggplot2)
  library(patchwork)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(scales)
  library(lubridate)
})

# ── paths ──────────────────────────────────────────────────────────────────────
args     <- commandArgs(trailingOnly = TRUE)
OUT_PATH <- if (length(args) >= 1) args[1] else
              path.expand("~/discordBot/outputs/markets/spy_diagnostics.png")

FEAT_DIR <- path.expand("~/discordBot/outputs/features/markets")

dir.create(dirname(OUT_PATH), recursive = TRUE, showWarnings = FALSE)

# ── colour constants ───────────────────────────────────────────────────────────
BG      <- "#02233F"
GRID    <- "#274066"
TXT     <- "white"
ACCENT  <- "#4fc3f7"
GREEN   <- "#69f0ae"
RED     <- "#ef5350"
ORANGE  <- "#ffa726"
YELLOW  <- "#fff176"

# ── theme ──────────────────────────────────────────────────────────────────────
navy <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.4),
  panel.grid.minor  = element_line(color = "#1a3a5c", linewidth = 0.2),
  axis.text         = element_text(color = "#a0b8cc", size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = TXT,     size = 11, face = "bold", hjust = 0.5),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 8,  hjust = 0.5),
  plot.caption      = element_text(color = "#4a6a80", size = 7,  hjust = 0.5),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 8, face = "bold"),
  legend.background = element_rect(fill = BG),
  legend.text       = element_text(color = "#a0b8cc", size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = BG),
  plot.margin       = margin(8, 10, 8, 10)
)

# ── helpers ────────────────────────────────────────────────────────────────────
safe_read <- function(fname) {
  p <- file.path(FEAT_DIR, fname)
  if (!file.exists(p)) {
    message("WARNING: file not found: ", p)
    return(NULL)
  }
  suppressWarnings(read_csv(p, show_col_types = FALSE))
}

# ── load eval CSVs ─────────────────────────────────────────────────────────────
logistic_df <- safe_read("eval_spy_next_dir_1d_logistic.csv")
gbm_dir_df  <- safe_read("eval_spy_next_dir_1d_gbm.csv")
gbm_ret_df  <- safe_read("eval_spy_next_ret_5d_gbm.csv")
summary_df  <- safe_read("eval_spy_experiment_summary.csv")
log_df      <- safe_read("spy_experiment_log_copy.csv")

# Coerce date columns
coerce_dates <- function(df) {
  if (is.null(df)) return(NULL)
  if ("date" %in% names(df)) df <- df %>% mutate(date = as.Date(date))
  df
}
logistic_df <- coerce_dates(logistic_df)
gbm_dir_df  <- coerce_dates(gbm_dir_df)
gbm_ret_df  <- coerce_dates(gbm_ret_df)

# ── PANEL 1: Predicted Probability vs Outcome (Logistic) ──────────────────────
make_p1 <- function(df) {
  if (is.null(df)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5, label = "Data not found",
                 color = TXT, size = 5) +
        labs(title = "1d Direction - Logistic | Val Set") +
        navy
    )
  }

  df <- df %>%
    mutate(
      outcome = factor(actual, levels = c(0, 1),
                       labels = c("Down (0)", "Up (1)"))
    )

  ggplot(df, aes(x = predicted, y = actual)) +
    geom_vline(xintercept = 0.50, color = YELLOW, linewidth = 0.6,
               linetype = "dashed") +
    geom_point(aes(color = outcome), alpha = 0.45, size = 1.4) +
    geom_smooth(method = "loess", span = 1.0, se = FALSE,
                color = TXT, linewidth = 0.9) +
    scale_color_manual(
      values = c("Down (0)" = RED, "Up (1)" = GREEN),
      name   = "Actual"
    ) +
    scale_x_continuous(labels = percent_format(accuracy = 1)) +
    scale_y_continuous(breaks = c(0, 1)) +
    labs(
      title = "1d Direction - Logistic | Val Set",
      x     = "Predicted P(UP)",
      y     = "Actual Outcome"
    ) +
    navy +
    theme(legend.position = "right")
}

# ── PANEL 2: Calibration Curve (Logistic) ──────────────────────────────────────
make_p2 <- function(df) {
  if (is.null(df)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5, label = "Data not found",
                 color = TXT, size = 5) +
        labs(title = "Probability Calibration") +
        navy
    )
  }

  calib <- df %>%
    mutate(
      bucket = cut(predicted, breaks = seq(0, 1, by = 0.1),
                   include.lowest = TRUE, right = TRUE),
      midpoint = as.numeric(sub(".*,(.*)]", "\\1",
                                as.character(bucket))) - 0.05
    ) %>%
    group_by(bucket, midpoint) %>%
    summarise(
      obs_freq = mean(actual, na.rm = TRUE),
      n        = n(),
      .groups  = "drop"
    ) %>%
    filter(!is.na(midpoint))

  ggplot(calib, aes(x = midpoint, y = obs_freq)) +
    geom_abline(slope = 1, intercept = 0, color = TXT, linewidth = 0.7,
                linetype = "dashed") +
    geom_point(aes(size = n), color = ACCENT, alpha = 0.85) +
    geom_line(color = ACCENT, linewidth = 0.8) +
    scale_size_continuous(name = "n", range = c(2, 8)) +
    scale_x_continuous(labels = percent_format(accuracy = 1),
                       limits = c(0, 1)) +
    scale_y_continuous(labels = percent_format(accuracy = 1),
                       limits = c(0, 1)) +
    labs(
      title = "Probability Calibration",
      x     = "Predicted P(UP)",
      y     = "Observed Frequency"
    ) +
    navy +
    theme(legend.position = "right")
}

# ── PANEL 3: 5-Day Return - Actual vs Predicted (GBM regressor) ───────────────
make_p3 <- function(df) {
  if (is.null(df)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5, label = "Data not found",
                 color = TXT, size = 5) +
        labs(title = "5d Return - GBM | Actual vs Predicted") +
        navy
    )
  }

  df <- df %>% filter(!is.na(predicted) & !is.na(actual) & !is.na(vix_level))

  ggplot(df, aes(x = predicted, y = actual)) +
    geom_hline(yintercept = 0, color = YELLOW, linewidth = 0.5,
               linetype = "dashed") +
    geom_vline(xintercept = 0, color = YELLOW, linewidth = 0.5,
               linetype = "dashed") +
    geom_point(aes(color = vix_level), alpha = 0.55, size = 1.5) +
    geom_smooth(method = "loess", se = FALSE, color = TXT,
                linewidth = 0.9, span = 0.7) +
    scale_color_gradient2(
      low      = ACCENT,
      mid      = YELLOW,
      high     = RED,
      midpoint = median(df$vix_level, na.rm = TRUE),
      name     = "VIX"
    ) +
    scale_x_continuous(labels = percent_format(accuracy = 0.1)) +
    scale_y_continuous(labels = percent_format(accuracy = 0.1)) +
    labs(
      title = "5d Return - GBM | Actual vs Predicted",
      x     = "Predicted Return",
      y     = "Actual Return"
    ) +
    navy +
    theme(legend.position = "right")
}

# ── PANEL 4: Rolling 21-Day Directional Accuracy (Logistic) ────────────────────
make_p4 <- function(df) {
  if (is.null(df)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5, label = "Data not found",
                 color = TXT, size = 5) +
        labs(title = "Rolling 21-Day Directional Accuracy") +
        navy
    )
  }

  df <- df %>%
    arrange(date) %>%
    mutate(
      correct    = as.integer(round(predicted) == actual),
      roll_acc   = stats::filter(correct, rep(1 / 21, 21), sides = 1)
    ) %>%
    filter(!is.na(roll_acc))

  best_val_acc <- 0.548

  ggplot(df, aes(x = date)) +
    geom_ribbon(
      aes(
        ymin = pmin(roll_acc, 0.50),
        ymax = pmax(roll_acc, 0.50),
        fill = roll_acc >= 0.50
      ),
      alpha = 0.3
    ) +
    geom_line(aes(y = roll_acc), color = ACCENT, linewidth = 0.9) +
    geom_hline(yintercept = 0.50, color = YELLOW, linewidth = 0.6,
               linetype = "dashed") +
    geom_hline(yintercept = best_val_acc, color = GREEN, linewidth = 0.5,
               linetype = "dashed") +
    annotate("text", x = min(df$date), y = 0.503, label = "Coin flip",
             color = YELLOW, size = 2.8, hjust = 0, vjust = 0) +
    annotate("text", x = min(df$date), y = best_val_acc + 0.003,
             label = "Best val acc", color = GREEN, size = 2.8,
             hjust = 0, vjust = 0) +
    scale_fill_manual(values = c("FALSE" = RED, "TRUE" = GREEN),
                      guide = "none") +
    scale_y_continuous(labels = percent_format(accuracy = 1)) +
    scale_x_date(date_labels = "%b %Y") +
    labs(
      title = "Rolling 21-Day Directional Accuracy",
      x     = "Date",
      y     = "Accuracy"
    ) +
    navy
}

# ── PANEL 5: Experiment Log - Accuracy Over Runs ────────────────────────────────
make_p5 <- function(df) {
  if (is.null(df)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5, label = "Data not found",
                 color = TXT, size = 5) +
        labs(title = "Val Accuracy by Training Run") +
        navy
    )
  }

  # Filter and label logistic 1d and gbm 5d models
  log_filt <- df %>%
    mutate(run_n = row_number()) %>%
    filter(
      (grepl("next_dir_1d", target, ignore.case = TRUE)  &
         grepl("logistic", model_type, ignore.case = TRUE)) |
      (grepl("next_ret_5d", target, ignore.case = TRUE)  &
         grepl("gbm", model_type, ignore.case = TRUE))
    ) %>%
    mutate(
      model_label = case_when(
        grepl("logistic", model_type, ignore.case = TRUE) ~ "Logistic (1d Dir)",
        TRUE                                               ~ "GBM (5d Ret)"
      )
    ) %>%
    group_by(model_label) %>%
    mutate(run_idx = row_number()) %>%
    ungroup()

  if (nrow(log_filt) == 0) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5,
                 label = "No matching experiment runs found",
                 color = TXT, size = 5) +
        labs(title = "Val Accuracy by Training Run") +
        navy
    )
  }

  # Use val_dir_acc for logistic, val_spearman for GBM regressor
  plot_df <- log_filt %>%
    mutate(
      metric = case_when(
        model_label == "Logistic (1d Dir)" ~ as.numeric(val_dir_acc),
        TRUE                               ~ as.numeric(val_spearman)
      ),
      metric_label = case_when(
        model_label == "Logistic (1d Dir)" ~ "Dir Accuracy",
        TRUE                               ~ "Spearman"
      )
    ) %>%
    filter(!is.na(metric))

  best_lines <- plot_df %>%
    group_by(model_label) %>%
    summarise(best = max(metric, na.rm = TRUE), .groups = "drop")

  ggplot(plot_df, aes(x = run_idx, y = metric,
                      color = model_label, group = model_label)) +
    geom_hline(
      data = best_lines,
      aes(yintercept = best, color = model_label),
      linetype = "dashed", linewidth = 0.5, alpha = 0.7
    ) +
    geom_line(linewidth = 1.0) +
    geom_point(size = 2.5) +
    facet_wrap(~model_label, scales = "free_y", ncol = 2) +
    scale_color_manual(
      values = c("Logistic (1d Dir)" = ACCENT, "GBM (5d Ret)" = ORANGE),
      name   = NULL
    ) +
    scale_x_continuous(breaks = pretty_breaks()) +
    scale_y_continuous(labels = number_format(accuracy = 0.001)) +
    labs(
      title = "Val Accuracy by Training Run",
      x     = "Run",
      y     = NULL
    ) +
    navy +
    theme(legend.position = "none",
          strip.text = element_text(color = "white", size = 8, face = "bold"))
}

# ── build panels ───────────────────────────────────────────────────────────────
p1 <- make_p1(logistic_df)
p2 <- make_p2(logistic_df)
p3 <- make_p3(gbm_ret_df)
p4 <- make_p4(logistic_df)
p5 <- make_p5(log_df)

# ── assemble with patchwork ────────────────────────────────────────────────────
final <- (p1 | p2) / (p3 | p4) / p5 +
  plot_annotation(
    title    = "SPY ML Model Diagnostics",
    subtitle = paste0("Val set: last 252 trading days | Generated: ", Sys.Date()),
    caption  = "Source: SPY ML Model | JHCV",
    theme = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title      = element_text(color = TXT,       size = 16, face = "bold",
                                     hjust = 0.5, margin = margin(b = 4)),
      plot.subtitle   = element_text(color = "#7fa8c4", size = 9,
                                     hjust = 0.5, margin = margin(b = 8)),
      plot.caption    = element_text(color = "#4a6a80", size = 8,  hjust = 0.5)
    )
  )

# ── save ───────────────────────────────────────────────────────────────────────
ggsave(OUT_PATH, plot = final, width = 14, height = 18, dpi = 150, bg = BG)
message("Saved: ", OUT_PATH)
