# btcModelDiagnostics.R
# BTC ML model diagnostics - navy theme, 5 panels
# Uses full prediction history (2014-present) for time-series panels.
# Val-only CSVs used for held-out accuracy metrics.
# Usage: Rscript r/btcModelDiagnostics.R [output_path]

for (pkg in c("ggplot2", "patchwork", "dplyr", "tidyr", "readr", "scales", "lubridate", "zoo")) {
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
  library(zoo)
})

# -- paths --
args     <- commandArgs(trailingOnly = TRUE)
OUT_PATH <- if (length(args) >= 1) args[1] else
              path.expand("~/discordBot/outputs/markets/btc_diagnostics.png")

FEAT_DIR <- path.expand("~/discordBot/outputs/features/markets")
dir.create(dirname(OUT_PATH), recursive = TRUE, showWarnings = FALSE)

# -- colour constants --
BG     <- "#02233F"
GRID   <- "#274066"
TXT    <- "white"
ACCENT <- "#f7931a"   # Bitcoin orange
GREEN  <- "#69f0ae"
RED    <- "#ef5350"
ORANGE <- "#ffa726"
YELLOW <- "#fff176"
CYAN   <- "#4fc3f7"
TRAIN_COL <- "#4a6a80"   # muted blue for in-sample region
VAL_COL   <- ACCENT      # orange for out-of-sample

# -- theme --
navy <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.4),
  panel.grid.minor  = element_line(color = "#1a3a5c", linewidth = 0.2),
  axis.text         = element_text(color = "#a0b8cc", size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = TXT,      size = 11, face = "bold", hjust = 0.5),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 8,  hjust = 0.5),
  plot.caption      = element_text(color = "#4a6a80", size = 7,  hjust = 1),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 8, face = "bold"),
  legend.background = element_rect(fill = BG),
  legend.text       = element_text(color = "#a0b8cc", size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = BG),
  plot.margin       = margin(8, 10, 8, 10)
)

# -- helpers --
safe_read <- function(fname) {
  p <- file.path(FEAT_DIR, fname)
  if (!file.exists(p)) { message("WARNING: not found: ", p); return(NULL) }
  suppressWarnings(read_csv(p, show_col_types = FALSE))
}

coerce_dates <- function(df) {
  if (is.null(df)) return(NULL)
  if ("date" %in% names(df)) df <- df %>% mutate(date = as.Date(date))
  df
}

# -- load CSVs --
# Full-history versions (train + val) for time-series panels
logistic_full <- coerce_dates(safe_read("eval_btc_next_dir_1d_logistic_full.csv"))
gbm5d_full    <- coerce_dates(safe_read("eval_btc_next_ret_5d_gbm_full.csv"))
gbm_dir_full  <- coerce_dates(safe_read("eval_btc_next_dir_1d_gbm_full.csv"))

# Val-only versions for held-out accuracy metrics
logistic_val  <- coerce_dates(safe_read("eval_btc_next_dir_1d_logistic.csv"))
gbm5d_val     <- coerce_dates(safe_read("eval_btc_next_ret_5d_gbm.csv"))

# Experiment log
log_df        <- coerce_dates(safe_read("btc_experiment_log_copy.csv"))
summary_df    <- coerce_dates(safe_read("eval_btc_experiment_summary.csv"))

# Fallback if full-history not yet generated: use val-only
if (is.null(logistic_full)) logistic_full <- logistic_val
if (is.null(gbm5d_full))    gbm5d_full    <- coerce_dates(safe_read("eval_btc_next_ret_5d_gbm.csv"))
if (is.null(gbm_dir_full))  gbm_dir_full  <- coerce_dates(safe_read("eval_btc_next_dir_1d_gbm.csv"))

has_data <- !is.null(logistic_full) || !is.null(gbm5d_full)

if (!has_data) {
  p_ph <- ggplot() +
    annotate("text", x=0.5, y=0.5,
             label="No BTC model eval data.\nRun buildBtcFeatures.py then trainBtcModel.py first.",
             color=TXT, size=6, hjust=0.5) +
    theme_void() + theme(plot.background=element_rect(fill=BG, color=NA))
  ggsave(OUT_PATH, p_ph, width=10, height=6, dpi=120, bg=BG)
  cat("[btcModelDiagnostics] No eval data - placeholder written.\n")
  quit(save="no", status=0)
}

# ============================================================
# PANEL 1: 5d GBM - full history predicted vs actual return
#          Train region shaded, val region highlighted
# ============================================================

if (!is.null(gbm5d_full) && "date" %in% names(gbm5d_full)) {

  # Find val cutoff
  val_start <- if ("split" %in% names(gbm5d_full)) {
    gbm5d_full %>% filter(split == "val") %>% pull(date) %>% min(na.rm=TRUE)
  } else {
    max(gbm5d_full$date, na.rm=TRUE) - 365
  }

  p1 <- gbm5d_full %>%
    filter(!is.na(predicted), !is.na(actual)) %>%
    pivot_longer(c(actual, predicted), names_to="series", values_to="value") %>%
    mutate(series = recode(series, actual="Actual 5d Return", predicted="Model Predicted")) %>%
    ggplot(aes(x=date, y=value, color=series)) +
    # Shade train region
    annotate("rect",
             xmin=min(gbm5d_full$date, na.rm=TRUE), xmax=as.Date(val_start),
             ymin=-Inf, ymax=Inf, fill=TRAIN_COL, alpha=0.12) +
    # Val region label
    annotate("rect",
             xmin=as.Date(val_start), xmax=max(gbm5d_full$date, na.rm=TRUE),
             ymin=-Inf, ymax=Inf, fill=VAL_COL, alpha=0.07) +
    annotate("text",
             x=as.Date(val_start) + 30, y=max(gbm5d_full$actual, na.rm=TRUE) * 0.85,
             label="val set", color=VAL_COL, size=2.8, hjust=0, fontface="italic") +
    geom_line(alpha=0.55, linewidth=0.55) +
    scale_color_manual(values=c("Actual 5d Return"=CYAN, "Model Predicted"=ACCENT), name=NULL) +
    geom_hline(yintercept=0, color=GRID, linewidth=0.5, linetype="dashed") +
    scale_x_date(date_labels="%Y", minor_breaks=waiver()) +
    labs(title="5-Day GBM: Full History Predicted vs Actual",
         subtitle="Train (shaded) + val (orange) | log returns",
         x=NULL, y="5d Return") +
    navy +
    theme(legend.position="bottom")
} else {
  p1 <- ggplot() + annotate("text", x=0.5, y=0.5, label="No 5d GBM data", color=TXT, size=5) +
        theme_void() + theme(plot.background=element_rect(fill=BG, color=NA))
}

# ============================================================
# PANEL 2: Predicted probability vs actual direction (logistic, full history)
#          Rolling 90-day accuracy line
# ============================================================

if (!is.null(logistic_full) && "prob_up" %in% names(logistic_full)) {

  val_start_l <- if ("split" %in% names(logistic_full)) {
    logistic_full %>% filter(split == "val") %>% pull(date) %>% min(na.rm=TRUE)
  } else {
    max(logistic_full$date, na.rm=TRUE) - 365
  }

  # Rolling 90-day accuracy (full history)
  roll_acc <- logistic_full %>%
    arrange(date) %>%
    filter(!is.na(prob_up), !is.na(actual)) %>%
    mutate(
      correct  = as.integer(as.integer(prob_up > 0.5) == as.integer(actual > 0.5)),
      roll_acc = zoo::rollmean(correct, k=90, fill=NA, align="right"),
      in_val   = date >= as.Date(val_start_l)
    )

  naive_up_full <- mean(logistic_full$actual > 0.5, na.rm=TRUE)
  val_acc <- if (!is.null(logistic_val)) mean(as.integer(logistic_val$prob_up > 0.5) == as.integer(logistic_val$actual > 0.5), na.rm=TRUE) else NA

  p2 <- roll_acc %>%
    filter(!is.na(roll_acc)) %>%
    ggplot(aes(x=date, y=roll_acc)) +
    annotate("rect",
             xmin=as.Date(val_start_l), xmax=max(roll_acc$date, na.rm=TRUE),
             ymin=-Inf, ymax=Inf, fill=VAL_COL, alpha=0.07) +
    geom_line(aes(color=in_val), linewidth=0.85) +
    scale_color_manual(values=c("FALSE"=CYAN, "TRUE"=ACCENT), guide="none") +
    geom_hline(yintercept=0.5, color=YELLOW, linetype="dashed", linewidth=0.6) +
    geom_hline(yintercept=naive_up_full, color=ORANGE, linetype="dotted", linewidth=0.6) +
    { if (!is.na(val_acc))
        geom_hline(yintercept=val_acc, color=GREEN, linetype="dashed", linewidth=0.6)
      else NULL } +
    { if (!is.na(val_acc))
        annotate("text", x=min(roll_acc$date[!is.na(roll_acc$roll_acc)]),
                 y=val_acc+0.015, label=paste0("val acc=", scales::percent(val_acc, .1)),
                 color=GREEN, size=2.6, hjust=0)
      else NULL } +
    scale_y_continuous(labels=scales::percent_format(), limits=c(0.25, 0.80)) +
    scale_x_date(date_labels="%Y", minor_breaks=waiver()) +
    labs(title="Rolling 90-Day Directional Accuracy (Full History)",
         subtitle="Logistic model | cyan=train, orange=val | yellow=50%, orange dotted=naive-up",
         x=NULL, y="Accuracy") +
    navy
} else {
  p2 <- ggplot() + annotate("text", x=0.5, y=0.5, label="No logistic eval data", color=TXT, size=5) +
        theme_void() + theme(plot.background=element_rect(fill=BG, color=NA))
}

# ============================================================
# PANEL 3: MVRV zones x directional accuracy (full history — val-only
#          only covers ~1yr so misses bear/euphoria zones)
# ============================================================

mvrv_src <- if (!is.null(logistic_full) && "btc_mvrv" %in% names(logistic_full)) {
  logistic_full
} else if (!is.null(logistic_val) && "btc_mvrv" %in% names(logistic_val)) {
  logistic_val
} else {
  NULL
}

if (!is.null(mvrv_src)) {
  zone_acc <- mvrv_src %>%
    filter(!is.na(btc_mvrv), !is.na(prob_up), !is.na(actual)) %>%
    mutate(
      correct   = as.integer(as.integer(prob_up > 0.5) == as.integer(actual > 0.5)),
      mvrv_zone = cut(btc_mvrv,
                      breaks = c(-Inf, 1, 2, 3.5, Inf),
                      labels = c("<1 Underval", "1-2 Fair", "2-3.5 Over", ">3.5 Euphoria"))
    ) %>%
    group_by(mvrv_zone) %>%
    summarise(avg_acc = mean(correct, na.rm = TRUE), n = n(), .groups = "drop") %>%
    filter(!is.na(mvrv_zone))

  # Label whether this is full history or val-only
  src_label <- if (!is.null(logistic_full) && "btc_mvrv" %in% names(logistic_full))
    "Full history" else "Val set only"

  p3 <- zone_acc %>%
    ggplot(aes(x = mvrv_zone, y = avg_acc, fill = avg_acc)) +
    geom_col(color = GRID, linewidth = 0.5) +
    geom_text(aes(label = paste0(scales::percent(avg_acc, .1), "\nn=", n)),
              color = TXT, size = 2.9, vjust = -0.3) +
    scale_fill_gradient2(low = RED, mid = YELLOW, high = GREEN, midpoint = 0.5, guide = "none") +
    geom_hline(yintercept = 0.5, color = YELLOW, linetype = "dashed", linewidth = 0.6) +
    scale_y_continuous(labels = scales::percent_format(), limits = c(0, 0.80)) +
    labs(title = "Accuracy by MVRV Zone",
         subtitle = paste0(src_label, " | does the model know the market cycle?"),
         x = "MVRV Zone", y = "Directional Accuracy") +
    navy +
    theme(axis.text.x = element_text(size = 7.5, angle = 10, hjust = 0.7))
} else {
  # Fallback: residual distribution
  resid_rows <- list()
  for (pair in list(
    list(logistic_val, "Logistic 1d"),
    list(gbm5d_val,    "GBM 5d Ret")
  )) {
    d <- pair[[1]]; lbl <- pair[[2]]
    if (!is.null(d) && "residual" %in% names(d))
      resid_rows[[length(resid_rows)+1]] <- d %>% transmute(residual, label=lbl)
  }
  if (length(resid_rows) > 0) {
    p3 <- bind_rows(resid_rows) %>%
      ggplot(aes(x=residual, color=label, fill=label)) +
      geom_density(alpha=0.15, linewidth=0.9) +
      scale_color_manual(values=c(ACCENT, CYAN), name=NULL) +
      scale_fill_manual( values=c(ACCENT, CYAN), name=NULL) +
      geom_vline(xintercept=0, color="white", linewidth=0.7, linetype="dashed") +
      labs(title="Residual Distribution (Val Set)", x="Residual", y="Density") +
      navy + theme(legend.position="bottom")
  } else {
    p3 <- ggplot() + annotate("text", x=0.5, y=0.5, label="No MVRV / residual data", color=TXT, size=5) +
          theme_void() + theme(plot.background=element_rect(fill=BG, color=NA))
  }
}

# ============================================================
# PANEL 4: Halving cycle position vs 5d predicted return (full)
# ============================================================

if (!is.null(gbm5d_full) && "btc_halving_cycle_pct" %in% names(gbm5d_full)) {
  cycle_df <- gbm5d_full %>%
    filter(!is.na(btc_halving_cycle_pct), !is.na(predicted)) %>%
    mutate(
      cycle_bin = cut(btc_halving_cycle_pct,
                      breaks=seq(0, 1, by=0.1),
                      include.lowest=TRUE,
                      labels=paste0(seq(0,90,10), "-", seq(10,100,10), "%"))
    ) %>%
    group_by(cycle_bin) %>%
    summarise(
      avg_pred_ret  = mean(predicted, na.rm=TRUE),
      avg_actual_ret= mean(actual,    na.rm=TRUE),
      n             = n(),
      .groups="drop"
    ) %>%
    filter(!is.na(cycle_bin)) %>%
    pivot_longer(c(avg_pred_ret, avg_actual_ret), names_to="series", values_to="value") %>%
    mutate(series=recode(series,
                         avg_pred_ret  ="Predicted",
                         avg_actual_ret="Actual"))

  p4 <- cycle_df %>%
    ggplot(aes(x=cycle_bin, y=value, fill=series)) +
    geom_col(position="dodge", color=GRID, linewidth=0.4) +
    scale_fill_manual(values=c("Predicted"=ACCENT, "Actual"=CYAN), name=NULL) +
    geom_hline(yintercept=0, color=YELLOW, linetype="dashed", linewidth=0.6) +
    labs(title="Avg 5-Day Return by Halving Cycle Position",
         subtitle="Full history | does the model capture cycle seasonality?",
         x="Cycle Position (% through halving epoch)", y="Avg 5d Log Return") +
    navy +
    theme(
      axis.text.x    = element_text(size=6.5, angle=45, hjust=1),
      legend.position= "bottom"
    )
} else {
  p4 <- ggplot() + annotate("text", x=0.5, y=0.5,
                              label="No halving cycle data", color=TXT, size=5) +
        theme_void() + theme(plot.background=element_rect(fill=BG, color=NA))
}

# ============================================================
# PANEL 5: Experiment log - dir accuracy by run
# ============================================================

if (!is.null(log_df) && nrow(log_df) > 0 && "val_dir_acc" %in% names(log_df)) {
  log_plot <- log_df %>%
    filter(!is.na(val_dir_acc)) %>%
    mutate(train_date=as.Date(train_date)) %>%
    arrange(train_date) %>%
    mutate(run_id=row_number())

  p5 <- log_plot %>%
    ggplot(aes(x=run_id, y=val_dir_acc,
               color=model_type, group=interaction(model_type, target))) +
    geom_line(linewidth=0.8) +
    geom_point(size=2.5) +
    scale_color_manual(
      values=c("ridge"=CYAN, "gbm"=ACCENT, "logistic"=GREEN),
      name=NULL
    ) +
    geom_hline(yintercept=0.5, color=YELLOW, linetype="dashed", linewidth=0.5) +
    scale_y_continuous(labels=scales::percent_format()) +
    labs(title="Val Directional Accuracy by Run",
         subtitle="All models | yellow = 50% baseline",
         x="Run #", y="Val Dir Accuracy") +
    navy + theme(legend.position="bottom")
} else {
  p5 <- ggplot() +
    annotate("text", x=0.5, y=0.5,
             label="Experiment log not found.\nRun trainBtcModel.py first.",
             color=TXT, size=5, hjust=0.5) +
    theme_void() + theme(plot.background=element_rect(fill=BG, color=NA))
}

# ============================================================
# Assemble
# ============================================================

layout <- "
AABB
CCDD
EEEE
"

combined <- p1 + p2 + p3 + p4 + p5 +
  plot_layout(design=layout) +
  plot_annotation(
    title    = "BTC ML Model Diagnostics",
    subtitle = paste("Full history 2014-present | Val:", format(Sys.Date()-365, "%Y-%m-%d"),
                     "to", format(Sys.Date(), "%Y-%m-%d")),
    caption  = "Source: CoinMetrics + blockchain.info + Alpaca | JHCV",
    theme    = theme(
      plot.background = element_rect(fill=BG, color=NA),
      plot.title      = element_text(color=TXT,       size=16, face="bold", hjust=0.5),
      plot.subtitle   = element_text(color="#7fa8c4",  size=10, hjust=0.5),
      plot.caption    = element_text(color="#4a6a80",  size=8,  hjust=1.0)
    )
  )

ggsave(OUT_PATH, combined, width=13, height=20, dpi=150, bg=BG)
cat(sprintf("[btcModelDiagnostics] Saved: %s\n", OUT_PATH))
