#!/usr/bin/env Rscript
# btcMinerCapitulation.R
#
# Replicates the bitbo.io "Miner Capitulation" chart.
# Y-axis: % change of each Bitcoin difficulty adjustment epoch
# X-axis: Date (BTC price line in background)
# Colored bands: green (0 to -5%), yellow (-5 to -10%), red (< -10%)
# Each epoch dot sits at (date_of_adjustment, pct_change)
#
# Data sources:
#   - blockchain.info API: daily difficulty (free, full history)
#   - CoinMetrics community API: PriceUSD, BlkCnt (for halvings)
#
# Usage: Rscript btcMinerCapitulation.R [output.png]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(httr)
  library(jsonlite)
})

# ── paths ─────────────────────────────────────────────────────────────────────
args      <- commandArgs(trailingOnly = TRUE)
out_png   <- if (length(args) >= 1) args[1] else
               path.expand("~/discordBot/outputs/markets/btcMinerCap.png")
cache_dir <- path.expand("~/discordBot/outputs/markets/cache")
diff_csv  <- file.path(cache_dir, "BTC_difficulty_daily.csv")
price_csv <- file.path(cache_dir, "BTC_miner_cap_daily.csv")
dir.create(cache_dir,        showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)

# ── fetch / cache: difficulty ─────────────────────────────────────────────────
need_diff <- TRUE
if (file.exists(diff_csv)) {
  age_h <- as.numeric(difftime(Sys.time(), file.mtime(diff_csv), units = "hours"))
  if (age_h < 24) need_diff <- FALSE
}

if (need_diff) {
  cat("[btcMinerCap] fetching difficulty from blockchain.info...\n")
  url  <- "https://api.blockchain.info/charts/difficulty?timespan=all&sampled=false&metadata=false&cors=true&format=json"
  resp <- tryCatch(httr::GET(url, httr::timeout(30)), error = function(e) NULL)
  if (!is.null(resp) && httr::status_code(resp) == 200) {
    raw  <- jsonlite::fromJSON(httr::content(resp, as = "text", encoding = "UTF-8"))
    df_d <- as.data.frame(raw$values) %>%
      rename(ts = x, difficulty = y) %>%
      mutate(date = as.Date(as.POSIXct(ts, origin = "1970-01-01", tz = "UTC"))) %>%
      filter(difficulty > 0) %>%
      select(date, difficulty) %>%
      arrange(date)
    readr::write_csv(df_d, diff_csv)
    cat("[btcMinerCap] difficulty cache written\n")
  } else {
    if (!file.exists(diff_csv)) stop("Difficulty fetch failed and no cache available.")
    cat("[btcMinerCap] difficulty fetch failed, using cache\n")
  }
}

# ── fetch / cache: price + blkcnt ─────────────────────────────────────────────
need_price <- TRUE
if (file.exists(price_csv)) {
  age_h <- as.numeric(difftime(Sys.time(), file.mtime(price_csv), units = "hours"))
  if (age_h < 24) need_price <- FALSE
}

if (need_price) {
  cat("[btcMinerCap] fetching price/blkcnt from CoinMetrics...\n")
  url  <- paste0(
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "?assets=btc&metrics=PriceUSD,BlkCnt",
    "&frequency=1d&start_time=2011-01-01&page_size=10000"
  )
  resp <- tryCatch(httr::GET(url, httr::timeout(30)), error = function(e) NULL)
  if (!is.null(resp) && httr::status_code(resp) == 200) {
    raw  <- jsonlite::fromJSON(
      httr::content(resp, as = "text", encoding = "UTF-8"),
      simplifyDataFrame = TRUE
    )
    df_p <- as.data.frame(raw$data) %>%
      mutate(
        date   = as.Date(substr(time, 1, 10)),
        price  = as.numeric(PriceUSD),
        blkcnt = as.numeric(BlkCnt)
      ) %>%
      filter(!is.na(date), !is.na(price), price > 0, !is.na(blkcnt)) %>%
      select(date, price, blkcnt) %>%
      arrange(date)
    readr::write_csv(df_p, price_csv)
    cat("[btcMinerCap] price cache written\n")
  } else {
    if (!file.exists(price_csv)) stop("Price fetch failed and no cache available.")
    cat("[btcMinerCap] price fetch failed, using cache\n")
  }
}

# ── load ──────────────────────────────────────────────────────────────────────
diff_daily <- readr::read_csv(diff_csv,
  col_types = cols(date = col_date(), difficulty = col_double())) %>%
  arrange(date)

price_daily <- readr::read_csv(price_csv,
  col_types = cols(date = col_date(), price = col_double(), blkcnt = col_double())) %>%
  filter(!is.na(price), price > 0) %>%
  arrange(date)

# ── extract difficulty adjustment epochs ──────────────────────────────────────
# Difficulty only changes every 2016 blocks (~14 days). The blockchain.info
# data is daily so many consecutive rows share the same difficulty value.
# We want one row per epoch: the date the new difficulty took effect + pct change.
#
# Strategy: find rows where difficulty changes, compute % change vs prior epoch.
# Then cluster any changes within 3 days of each other (API noise) keeping the
# largest-magnitude change per cluster.

epochs_raw <- diff_daily %>%
  mutate(prev_diff = lag(difficulty)) %>%
  filter(!is.na(prev_diff), prev_diff > 0, difficulty != prev_diff) %>%
  mutate(pct_change = (difficulty - prev_diff) / prev_diff * 100)

# Cluster micro-transitions: group changes within 3 days, keep max abs change
epochs <- epochs_raw %>%
  arrange(date) %>%
  mutate(
    gap       = as.integer(date - lag(date, default = as.Date("1900-01-01"))),
    new_group = gap > 3 | row_number() == 1,
    group_id  = cumsum(new_group)
  ) %>%
  group_by(group_id) %>%
  slice_max(abs(pct_change), n = 1, with_ties = FALSE) %>%
  ungroup() %>%
  select(date, difficulty, pct_change) %>%
  arrange(date)

cat(sprintf("[btcMinerCap] %d difficulty adjustment epochs\n", nrow(epochs)))
cat(sprintf("  Most negative: %+.1f%% on %s\n",
  min(epochs$pct_change), epochs$date[which.min(epochs$pct_change)]))
cat(sprintf("  Latest epoch:  %+.1f%% on %s\n",
  tail(epochs$pct_change, 1), tail(epochs$date, 1)))

# ── auto-detect halvings from cumulative block count ──────────────────────────
# First halving at block 210,000 on 2012-11-28.
# Self-calibrate GENESIS_BLK so cum_blocks matches block 210,000 on that date.
price_daily <- price_daily %>%
  mutate(cum_blocks = cumsum(blkcnt))

fh_date    <- as.Date("2012-11-28")
fh_idx     <- which.min(abs(price_daily$date - fh_date))
genesis_blk <- 210000L - as.integer(round(price_daily$cum_blocks[fh_idx]))
price_daily$abs_height <- genesis_blk + price_daily$cum_blocks

halving_heights <- seq(210000L, max(price_daily$abs_height), by = 210000L)
halvings <- vapply(halving_heights, function(h) {
  idx <- which(price_daily$abs_height >= h)[1]
  if (is.na(idx)) return(NA_real_)
  as.numeric(price_daily$date[idx])
}, numeric(1))
halvings <- as.Date(halvings[!is.na(halvings)], origin = "1970-01-01")

cat(sprintf("[btcMinerCap] %d halvings detected\n", length(halvings)))

# ── join price onto epochs for tooltip / current-state display ────────────────
epochs <- epochs %>%
  left_join(price_daily %>% select(date, price), by = "date") %>%
  # fill price for epoch dates that may not be in price_daily exactly
  arrange(date) %>%
  tidyr::fill(price, .direction = "downup")

# ── current state summary ─────────────────────────────────────────────────────
last_ep   <- tail(epochs, 1)
last_neg  <- epochs %>% filter(pct_change < 0) %>% tail(1)
sub_txt <- sprintf(
  "Latest adjustment: %+.1f%% on %s  |  Last negative: %+.1f%% on %s",
  last_ep$pct_change, format(last_ep$date, "%b %d %Y"),
  last_neg$pct_change, format(last_neg$date, "%b %d %Y")
)

# ── theme (bot navy) ──────────────────────────────────────────────────────────
BG   <- "#02233F"
GRID <- "#274066"
TXT  <- "white"

theme_btc <- theme_minimal(base_size = 11) +
  theme(
    plot.background   = element_rect(fill = BG, color = NA),
    panel.background  = element_rect(fill = BG, color = NA),
    panel.grid.major  = element_line(color = GRID, linewidth = 0.35),
    panel.grid.minor  = element_line(color = GRID, linewidth = 0.15),
    axis.text         = element_text(color = TXT, size = 9),
    axis.title        = element_text(color = TXT, size = 10),
    plot.title        = element_text(color = TXT, face = "bold", size = 13, hjust = 0),
    plot.subtitle     = element_text(color = "#aaaaaa", size = 9, hjust = 0),
    plot.caption      = element_text(color = "#777777", size = 8, hjust = 1),
    legend.background = element_rect(fill = BG, color = NA),
    legend.key        = element_rect(fill = NA, color = NA),
    legend.text       = element_text(color = TXT, size = 8),
    legend.title      = element_text(color = TXT, size = 9),
    legend.position   = "right",
    plot.margin       = margin(10, 14, 10, 10)
  )

# ── band definitions (bitbo zones) ────────────────────────────────────────────
#  green:  0%  to -5%   minor stress
#  yellow: -5% to -10%  significant stress
#  red:    < -10%        extreme capitulation

# ── plot ──────────────────────────────────────────────────────────────────────
# Primary: BTC price (log scale, right axis) drawn as a faint line
# Secondary: epoch difficulty % change as bars/segments, colored by zone

# Trim price to same date range as epochs
plot_start <- as.Date("2012-01-01")
price_plot <- price_daily %>% filter(date >= plot_start)
epochs_plot <- epochs %>% filter(date >= plot_start)

# Assign zone color to each epoch
epochs_plot <- epochs_plot %>%
  mutate(zone = case_when(
    pct_change <= -10 ~ "Extreme (< -10%)",
    pct_change <= -5  ~ "Significant (-5 to -10%)",
    pct_change <   0  ~ "Minor (0 to -5%)",
    TRUE              ~ "Positive"
  ))

zone_colors <- c(
  "Extreme (< -10%)"       = "#e74c3c",
  "Significant (-5 to -10%)" = "#f1c40f",
  "Minor (0 to -5%)"       = "#27ae60",
  "Positive"               = "#4a90d9"
)

# Dual-axis: price (log, right) + difficulty pct (left)
# We scale the price axis to fit within the plot area as a secondary line.
# Use sec_axis with a transform: price_scaled = log10(price) normalized to [y_lo, y_hi]
y_lo <- min(epochs_plot$pct_change) * 1.15
y_hi <- max(epochs_plot$pct_change, 0) * 1.10 + 5

price_lo <- min(price_plot$price[price_plot$date >= plot_start], na.rm = TRUE)
price_hi <- max(price_plot$price, na.rm = TRUE)

# Map log10(price) into the UPPER portion of the y-axis only (above 0),
# so the price line doesn't overlap the capitulation bars below zero.
# Price occupies [0, y_hi], capitulation bars occupy [y_lo, 0].
log_lo <- log10(price_lo)
log_hi <- log10(price_hi)
px_to_y  <- function(p) 0 + (log10(p) - log_lo) / (log_hi - log_lo) * y_hi
y_to_px  <- function(y) 10^(log_lo + (y - 0)    / (y_hi - 0)        * (log_hi - log_lo))

price_plot <- price_plot %>% mutate(y_scaled = px_to_y(price))

# Price axis breaks - only show values that map well above the 0 line
px_brk_raw <- c(1000, 10000, 100000)
px_brk_raw <- px_brk_raw[px_brk_raw >= price_lo * 0.5 & px_brk_raw <= price_hi * 2]
px_brk_y   <- px_to_y(px_brk_raw)
# Drop any breaks that map too close to the 0 line (less than 15% of y_hi)
keep       <- px_brk_y >= y_hi * 0.15
px_brk_y   <- px_brk_y[keep]
px_brk_raw <- px_brk_raw[keep]
px_brk_lbl <- paste0("$", formatC(px_brk_raw/1000, format="fg"), "k")

p <- ggplot() +

  # -- background band: positive (above 0)
  annotate("rect", xmin = plot_start, xmax = max(epochs_plot$date) + 30,
           ymin = 0, ymax = y_hi, fill = "#1a4a2a", alpha = 0.18) +

  # -- band: 0 to -5% (minor green)
  annotate("rect", xmin = plot_start, xmax = max(epochs_plot$date) + 30,
           ymin = -5, ymax = 0, fill = "#27ae60", alpha = 0.12) +

  # -- band: -5 to -10% (yellow)
  annotate("rect", xmin = plot_start, xmax = max(epochs_plot$date) + 30,
           ymin = -10, ymax = -5, fill = "#f1c40f", alpha = 0.12) +

  # -- band: < -10% (red)
  annotate("rect", xmin = plot_start, xmax = max(epochs_plot$date) + 30,
           ymin = y_lo, ymax = -10, fill = "#e74c3c", alpha = 0.12) +

  # -- BTC price (scaled to left axis, faint line) -- draw FIRST so it's behind bars
  geom_line(data = price_plot,
            aes(x = date, y = y_scaled),
            color = "#f0a500", linewidth = 0.55, alpha = 0.65) +

  # -- halvings
  geom_vline(xintercept = halvings, color = "#8899aa",
             linetype = "dashed", linewidth = 0.45, alpha = 0.8) +

  annotate("label",
    x = halvings, y = rep(y_hi * 0.92, length(halvings)),
    label = rep("Halving", length(halvings)),
    fill = "#0d3358", color = "#aaccee", size = 2.3, fontface = "bold") +

  # -- zero line
  geom_hline(yintercept = 0, color = "#ffffff", linewidth = 0.4, alpha = 0.4) +

  # -- epoch bars (segments from 0 down to pct_change)
  geom_segment(data = epochs_plot,
               aes(x = date, xend = date, y = 0, yend = pct_change, color = zone),
               linewidth = 0.9, alpha = 0.85) +

  # -- epoch dots
  geom_point(data = epochs_plot,
             aes(x = date, y = pct_change, color = zone),
             size = 1.6, alpha = 0.95) +

  # -- threshold lines
  geom_hline(yintercept = -5,  color = "#f1c40f", linewidth = 0.3, linetype = "dotted", alpha = 0.6) +
  geom_hline(yintercept = -10, color = "#e74c3c", linewidth = 0.3, linetype = "dotted", alpha = 0.6) +

  scale_color_manual(
    values = zone_colors,
    name   = "Difficulty drop",
    guide  = guide_legend(override.aes = list(linewidth = 2, size = 3))
  ) +

  scale_y_continuous(
    name   = "Difficulty adjustment (%)",
    labels = function(x) paste0(ifelse(x >= 0, "+", ""), round(x, 1), "%"),
    sec.axis = sec_axis(
      transform = ~ y_to_px(.),
      name   = "BTC Price (USD)",
      breaks = px_brk_y,
      labels = px_brk_lbl
    )
  ) +

  scale_x_date(
    date_breaks = "1 year",
    date_labels = "%Y",
    expand      = c(0.01, 0)
  ) +

  labs(
    title    = "BTC Miner Capitulation - Difficulty Adjustment % per Epoch",
    subtitle = sub_txt,
    x        = NULL,
    caption  = "Source: blockchain.info (difficulty), CoinMetrics (price) | JHCV"
  ) +

  theme_btc +
  theme(
    axis.title.y.right = element_text(color = "#f0a500", size = 9),
    axis.text.y.right  = element_text(color = "#f0a500", size = 8)
  )

# ── save ──────────────────────────────────────────────────────────────────────
ggsave(out_png, p, width = 13, height = 7, dpi = 150, bg = BG)
cat(sprintf("[btcMinerCap] saved -> %s\n", out_png))
