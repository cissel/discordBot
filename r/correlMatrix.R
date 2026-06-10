#!/usr/bin/env Rscript
# correlMatrix.R - Pearson correlation matrix heatmap for macro + market series
#
# Usage:
#   Rscript r/correlMatrix.R [output_path]
#
# Defaults output to ~/discordBot/outputs/markets/correlMatrix.png

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(tidyr)
})

# ── Args & paths ──────────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
out_path <- if (length(args) >= 1) args[1] else
  file.path(path.expand("~"), "discordBot", "outputs", "markets", "correlMatrix.png")

DAYS <- if (length(args) >= 2) as.integer(args[2]) else 756

dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

cache_dir   <- file.path(path.expand("~"), "discordBot", "outputs", "markets", "cache")
markets_dir <- file.path(path.expand("~"), "discordBot", "outputs", "markets")

# ── Theme ─────────────────────────────────────────────────────────────────────
BG   <- "#02233F"
TXT  <- "white"
GRAY <- "white"

# ── Helper: parse date column ─────────────────────────────────────────────────
parse_date_col <- function(df) {
  # Normalise column names to lowercase for detection
  nms <- names(df)
  date_col <- nms[tolower(nms) == "date"][1]
  if (is.na(date_col)) stop("No date column found")
  df[[date_col]] <- as.Date(substr(as.character(df[[date_col]]), 1, 10))
  df <- df %>% rename(date = all_of(date_col))
  df
}

# ── Load series into list(date, value) frames ─────────────────────────────────
series_list <- list()

# -- 1. SPY (from SPY_max_bars.csv - already has lowercase 'close') ----
spy_path <- file.path(markets_dir, "SPY_max_bars.csv")
if (file.exists(spy_path)) {
  tryCatch({
    df <- read_csv(spy_path, show_col_types = FALSE)
    df <- parse_date_col(df)
    # column might be 'close' or 'Close'
    close_col <- names(df)[tolower(names(df)) == "close"][1]
    df <- df %>%
      arrange(date) %>%
      transmute(date, value = as.numeric(.data[[close_col]])) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(log(value)))) %>%
      filter(!is.na(value))
    series_list[["SPY"]] <- df
    message("  [ok] SPY (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] SPY: ", e$message))
} else {
  message("  [skip] SPY_max_bars.csv not found")
}

# -- 2. BTC (from BTC_max_bars.csv) ----
btc_path <- file.path(markets_dir, "BTC_max_bars.csv")
if (file.exists(btc_path)) {
  tryCatch({
    df <- read_csv(btc_path, show_col_types = FALSE)
    df <- parse_date_col(df)
    close_col <- names(df)[tolower(names(df)) == "close"][1]
    df <- df %>%
      arrange(date) %>%
      transmute(date, value = as.numeric(.data[[close_col]])) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(log(value)))) %>%
      filter(!is.na(value))
    series_list[["BTC"]] <- df
    message("  [ok] BTC (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] BTC: ", e$message))
} else {
  message("  [skip] BTC_max_bars.csv not found")
}

# -- 3. ETH (from ETH_max_bars.csv) ----
eth_path <- file.path(markets_dir, "ETH_max_bars.csv")
if (file.exists(eth_path)) {
  tryCatch({
    df <- read_csv(eth_path, show_col_types = FALSE)
    df <- parse_date_col(df)
    close_col <- names(df)[tolower(names(df)) == "close"][1]
    df <- df %>%
      arrange(date) %>%
      transmute(date, value = as.numeric(.data[[close_col]])) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(log(value)))) %>%
      filter(!is.na(value))
    series_list[["ETH"]] <- df
    message("  [ok] ETH (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] ETH: ", e$message))
} else {
  message("  [skip] ETH_max_bars.csv not found")
}

# -- 4. GLD (from GLD_max_bars.csv if present, else GLD.csv in cache) ----
gld_max_path   <- file.path(markets_dir, "GLD_max_bars.csv")
gld_cache_path <- file.path(cache_dir, "GLD.csv")
gld_loaded <- FALSE

if (file.exists(gld_max_path)) {
  tryCatch({
    df <- read_csv(gld_max_path, show_col_types = FALSE)
    df <- parse_date_col(df)
    close_col <- names(df)[tolower(names(df)) == "close"][1]
    df <- df %>%
      arrange(date) %>%
      transmute(date, value = as.numeric(.data[[close_col]])) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(log(value)))) %>%
      filter(!is.na(value))
    series_list[["GLD"]] <- df
    gld_loaded <- TRUE
    message("  [ok] GLD from GLD_max_bars.csv (", nrow(df), " rows)")
  }, error = function(e) message("  [warn] GLD_max_bars.csv failed: ", e$message))
}

if (!gld_loaded && file.exists(gld_cache_path)) {
  tryCatch({
    df <- read_csv(gld_cache_path, show_col_types = FALSE)
    # GLD.csv has columns: date, GLD_ret (already log returns)
    df <- parse_date_col(df)
    ret_col <- names(df)[tolower(names(df)) != "date"][1]
    df <- df %>%
      arrange(date) %>%
      transmute(date, value = as.numeric(.data[[ret_col]])) %>%
      filter(!is.na(value))
    series_list[["GLD"]] <- df
    message("  [ok] GLD from cache/GLD.csv (", nrow(df), " rows, pre-computed returns)")
  }, error = function(e) message("  [skip] GLD: ", e$message))
}

# -- 5. DGS10 - 10Y Treasury yield (first differences) ----
dgs10_path <- file.path(cache_dir, "MACRO_DGS10.csv")
if (file.exists(dgs10_path)) {
  tryCatch({
    df <- read_csv(dgs10_path, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE) %>%
      arrange(date) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(value))) %>%
      filter(!is.na(value))
    series_list[["DGS10"]] <- df
    message("  [ok] DGS10 (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] DGS10: ", e$message))
} else {
  message("  [skip] MACRO_DGS10.csv not found")
}

# -- 6. DFII10 - 10Y real yield (first differences) ----
dfii10_path <- file.path(cache_dir, "MACRO_DFII10.csv")
if (file.exists(dfii10_path)) {
  tryCatch({
    df <- read_csv(dfii10_path, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE) %>%
      arrange(date) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(value))) %>%
      filter(!is.na(value))
    series_list[["DFII10"]] <- df
    message("  [ok] DFII10 (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] DFII10: ", e$message))
} else {
  message("  [skip] MACRO_DFII10.csv not found")
}

# -- 7. CPI - YoY change (12-period lag difference of log) ----
# Monthly series: compute YoY then forward-fill to daily so the inner join
# does not collapse to only monthly observation dates.
cpi_path <- file.path(cache_dir, "MACRO_CPIAUCSL.csv")
if (file.exists(cpi_path)) {
  tryCatch({
    df <- read_csv(cpi_path, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE) %>%
      arrange(date) %>%
      filter(!is.na(value))
    n <- nrow(df)
    if (n > 12) {
      df <- df %>%
        mutate(value = c(rep(NA_real_, 12), diff(log(value), lag = 12))) %>%
        filter(!is.na(value))
      # Forward-fill monthly values to every calendar day
      all_days <- data.frame(date = seq(min(df$date), max(df$date) + 31, by = "day"))
      df <- all_days %>%
        left_join(df, by = "date") %>%
        arrange(date) %>%
        tidyr::fill(value, .direction = "down") %>%
        filter(!is.na(value))
      series_list[["CPIAUCSL"]] <- df
      message("  [ok] CPIAUCSL YoY daily-filled (", nrow(df), " rows)")
    } else {
      message("  [skip] CPIAUCSL: too few rows")
    }
  }, error = function(e) message("  [skip] CPIAUCSL: ", e$message))
} else {
  message("  [skip] MACRO_CPIAUCSL.csv not found")
}

# -- 8. Fed Funds rate (first differences; try DFEDTARU first, then FEDFUNDS) ----
ff_loaded <- FALSE
for (ff_series in c("DFEDTARU", "FEDFUNDS")) {
  ff_path <- file.path(cache_dir, paste0("MACRO_", ff_series, ".csv"))
  if (!ff_loaded && file.exists(ff_path)) {
    tryCatch({
      df <- read_csv(ff_path, col_types = cols(date = col_date(), value = col_double()),
                     show_col_types = FALSE) %>%
        arrange(date) %>%
        filter(!is.na(value)) %>%
        mutate(value = c(NA_real_, diff(value))) %>%
        filter(!is.na(value))
      series_list[["FEDFUNDS"]] <- df
      ff_loaded <- TRUE
      message("  [ok] FEDFUNDS from ", ff_series, " (", nrow(df), " rows)")
    }, error = function(e) message("  [warn] ", ff_series, ": ", e$message))
  }
}
if (!ff_loaded) message("  [skip] No Fed Funds file found")

# -- 9. DXY (DTWEXBGS) - first differences ----
dxy_path <- file.path(cache_dir, "MACRO_DTWEXBGS.csv")
if (file.exists(dxy_path)) {
  tryCatch({
    df <- read_csv(dxy_path, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE) %>%
      arrange(date) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(log(value)))) %>%
      filter(!is.na(value))
    series_list[["DTWEXBGS"]] <- df
    message("  [ok] DXY/DTWEXBGS (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] DTWEXBGS: ", e$message))
} else {
  message("  [skip] MACRO_DTWEXBGS.csv not found")
}

# -- 10. M2 money supply - YoY change (try M2SL monthly, then WM2NS weekly) ----
# Both are low-frequency: forward-fill to daily before joining.
m2_loaded <- FALSE

m2sl_path <- file.path(cache_dir, "MACRO_M2SL.csv")
if (!m2_loaded && file.exists(m2sl_path)) {
  tryCatch({
    df <- read_csv(m2sl_path, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE) %>%
      arrange(date) %>%
      filter(!is.na(value))
    n <- nrow(df)
    if (n > 12) {
      df <- df %>%
        mutate(value = c(rep(NA_real_, 12), diff(log(value), lag = 12))) %>%
        filter(!is.na(value))
      # Forward-fill to daily
      all_days <- data.frame(date = seq(min(df$date), max(df$date) + 31, by = "day"))
      df <- all_days %>%
        left_join(df, by = "date") %>%
        arrange(date) %>%
        tidyr::fill(value, .direction = "down") %>%
        filter(!is.na(value))
      series_list[["M2"]] <- df
      m2_loaded <- TRUE
      message("  [ok] M2 from M2SL YoY daily-filled (", nrow(df), " rows)")
    }
  }, error = function(e) message("  [warn] M2SL: ", e$message))
}

wm2_path <- file.path(cache_dir, "MACRO_WM2NS.csv")
if (!m2_loaded && file.exists(wm2_path)) {
  tryCatch({
    df <- read_csv(wm2_path, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE) %>%
      arrange(date) %>%
      filter(!is.na(value))
    n <- nrow(df)
    if (n > 52) {
      df <- df %>%
        mutate(value = c(rep(NA_real_, 52), diff(log(value), lag = 52))) %>%
        filter(!is.na(value))
      # Forward-fill to daily
      all_days <- data.frame(date = seq(min(df$date), max(df$date) + 7, by = "day"))
      df <- all_days %>%
        left_join(df, by = "date") %>%
        arrange(date) %>%
        tidyr::fill(value, .direction = "down") %>%
        filter(!is.na(value))
      series_list[["M2"]] <- df
      m2_loaded <- TRUE
      message("  [ok] M2 from WM2NS YoY daily-filled (", nrow(df), " rows)")
    }
  }, error = function(e) message("  [warn] WM2NS: ", e$message))
}
if (!m2_loaded) message("  [skip] No M2 file found")

# -- 11. VIX (VIXCLS) - log returns ----
vix_path <- file.path(cache_dir, "VIX.csv")
if (file.exists(vix_path)) {
  tryCatch({
    df <- read_csv(vix_path, show_col_types = FALSE)
    df <- parse_date_col(df)
    val_col <- names(df)[tolower(names(df)) != "date"][1]
    df <- df %>%
      arrange(date) %>%
      transmute(date, value = as.numeric(.data[[val_col]])) %>%
      filter(!is.na(value)) %>%
      mutate(value = c(NA_real_, diff(log(value)))) %>%
      filter(!is.na(value))
    series_list[["VIXCLS"]] <- df
    message("  [ok] VIX (", nrow(df), " rows)")
  }, error = function(e) message("  [skip] VIX: ", e$message))
} else {
  message("  [skip] VIX.csv not found")
}

# ── Validate: need at least 3 series ─────────────────────────────────────────
n_series <- length(series_list)
message(sprintf("\n[correlMatrix] Loaded %d series: %s", n_series,
                paste(names(series_list), collapse = ", ")))

if (n_series < 3) {
  stop(sprintf(
    "Only %d series loaded. Need at least 3 to plot a correlation matrix.",
    n_series
  ))
}

# ── Merge on common dates (inner join) ────────────────────────────────────────
merged <- series_list[[1]] %>% rename(!!names(series_list)[1] := value)
for (nm in names(series_list)[-1]) {
  merged <- inner_join(merged,
                       series_list[[nm]] %>% rename(!!nm := value),
                       by = "date")
}

merged <- merged %>% arrange(date)
message(sprintf("[correlMatrix] Merged: %d common dates", nrow(merged)))

# Filter to last DAYS trading days
if (nrow(merged) > DAYS) {
  merged <- tail(merged, DAYS)
}

date_min <- min(merged$date)
date_max <- max(merged$date)
message(sprintf("[correlMatrix] Date range: %s to %s (%d rows)",
                date_min, date_max, nrow(merged)))

# ── Compute correlation matrix ────────────────────────────────────────────────
mat_data <- merged %>% select(-date) %>% as.matrix()
corr_mat  <- cor(mat_data, use = "pairwise.complete.obs", method = "pearson")

# ── Hierarchical clustering reorder ──────────────────────────────────────────
dist_mat  <- as.dist(1 - abs(corr_mat))
hc        <- hclust(dist_mat, method = "average")
var_order <- rownames(corr_mat)[hc$order]

# ── Variable labels ───────────────────────────────────────────────────────────
label_map <- c(
  SPY      = "S&P 500",
  BTC      = "Bitcoin",
  ETH      = "Ethereum",
  DGS10    = "10Y Yield",
  DFII10   = "Real Yield",
  CPIAUCSL = "CPI (YoY)",
  FEDFUNDS = "Fed Funds",
  DTWEXBGS = "DXY",
  M2       = "M2 Supply",
  VIXCLS   = "VIX",
  GLD      = "Gold"
)

get_label <- function(k) {
  # Vectorised: works on a character vector
  ifelse(k %in% names(label_map), label_map[k], k)
}

# ── Build long data frame for ggplot ─────────────────────────────────────────
corr_df <- as.data.frame(corr_mat)
corr_df$var1 <- rownames(corr_df)

corr_long <- corr_df %>%
  pivot_longer(-var1, names_to = "var2", values_to = "corr") %>%
  mutate(
    var1  = factor(var1,  levels = var_order),
    var2  = factor(var2,  levels = var_order),
    label = sprintf("%.2f", round(corr, 2)),
    # Text color: white for saturated cells, dark for near-zero
    txt_col = ifelse(abs(corr) > 0.45, "white", "#02233F")
  )

# Readable axis labels in the factor levels
lbl_levels <- sapply(var_order, get_label)
corr_long <- corr_long %>%
  mutate(
    var1 = factor(get_label(as.character(var1)),
                  levels = lbl_levels),
    var2 = factor(get_label(as.character(var2)),
                  levels = lbl_levels)
  )

# ── Build subtitle ────────────────────────────────────────────────────────────
subtitle_str <- sprintf("%s to %s  |  n = %d trading days",
                        format(date_min, "%b %d, %Y"),
                        format(date_max, "%b %d, %Y"),
                        nrow(merged))

# ── Plot ──────────────────────────────────────────────────────────────────────
n_vars   <- length(var_order)
cell_sz  <- 0.92   # relative tile size to leave tiny gap
txt_size <- if (n_vars <= 7) 4.0 else if (n_vars <= 10) 3.4 else 2.8

p <- ggplot(corr_long, aes(x = var2, y = var1, fill = corr)) +
  geom_tile(width = cell_sz, height = cell_sz, color = NA) +
  geom_text(aes(label = label, color = txt_col), size = txt_size,
            fontface = "bold", show.legend = FALSE) +
  scale_fill_gradientn(
    colours = c("#cc2222", "#7a1515", "#2a0808",
                "#02233F",
                "#082a12", "#157a2a", "#22cc44"),
    values  = rescale(c(-1, -0.6, -0.25, 0, 0.25, 0.6, 1)),
    limits  = c(-1, 1),
    name    = "Pearson r",
    guide   = guide_colorbar(
      barwidth  = unit(0.5, "cm"),
      barheight = unit(4,   "cm"),
      title.theme   = element_text(color = TXT, size = 7, hjust = 0.5),
      label.theme   = element_text(color = GRAY, size = 6.5),
      frame.colour  = "#30363d",
      ticks.colour  = "#30363d"
    )
  ) +
  scale_color_identity() +
  scale_x_discrete(position = "bottom") +
  scale_y_discrete(limits = rev) +
  coord_fixed() +
  labs(
    title    = paste0("Market Correlation Matrix (", round(DAYS/365, 1), "Y daily returns)"),
    subtitle = subtitle_str,
    caption  = "Source: Alpaca/FRED | JHCV"
  ) +
  theme_minimal(base_size = 10) +
  theme(
    plot.background  = element_rect(fill = BG, color = NA),
    panel.background = element_rect(fill = BG, color = NA),
    panel.grid       = element_blank(),
    panel.border     = element_blank(),
    axis.ticks       = element_blank(),
    axis.title       = element_blank(),
    axis.text.x      = element_text(color = TXT, size = 8.5,
                                    angle = 35, hjust = 1, vjust = 1),
    axis.text.y      = element_text(color = TXT, size = 8.5, hjust = 1),
    plot.title       = element_text(color = TXT,  size = 12, face = "bold",
                                    hjust = 0.5, margin = margin(b = 4)),
    plot.subtitle    = element_text(color = GRAY, size = 8,
                                    hjust = 0.5, margin = margin(b = 8)),
    plot.caption     = element_text(color = GRAY, size = 6.5, hjust = 1),
    plot.margin      = margin(14, 14, 10, 14),
    legend.background = element_rect(fill = BG, color = NA),
    legend.key        = element_rect(fill = BG, color = NA),
    legend.text       = element_text(color = GRAY, size = 6.5),
    legend.title      = element_text(color = TXT,  size = 7.5)
  )

# ── Save ──────────────────────────────────────────────────────────────────────
ggsave(out_path, plot = p, width = 900, height = 900, units = "px",
       dpi = 150, bg = BG)

message(sprintf("[correlMatrix] Saved -> %s", out_path))
cat(sprintf("OUTPUT_PATH:%s\n", out_path))
