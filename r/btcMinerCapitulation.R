#!/usr/bin/env Rscript
# btcMinerCapitulation.R
# BTC price / price at last difficulty bottom (%)
# colored by cumulative blocks elapsed since that bottom
# Difficulty bottoms = known historical miner capitulation events
# + algorithm detects new ones in recent data (>=10% HR drop, 45-day local min)
# Data: CoinMetrics Community API (PriceUSD, HashRate, BlkCnt)
# Usage: Rscript btcMinerCapitulation.R [output.png]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(httr)
  library(jsonlite)
})

# -- paths ---------------------------------------------------
args      <- commandArgs(trailingOnly = TRUE)
out_png   <- if (length(args) >= 1) args[1] else
               path.expand("~/discordBot/outputs/markets/btcMinerCap.png")
cache_dir <- path.expand("~/discordBot/outputs/markets/cache")
cache_csv <- file.path(cache_dir, "BTC_miner_cap_daily.csv")
dir.create(cache_dir,        showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)

# -- fetch / cache -------------------------------------------
need_fetch <- TRUE
if (file.exists(cache_csv)) {
  age_h <- as.numeric(difftime(Sys.time(), file.mtime(cache_csv), units = "hours"))
  if (age_h < 24) need_fetch <- FALSE
}

if (need_fetch) {
  cat("[btcMinerCap] fetching CoinMetrics...\n")
  url <- paste0(
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "?assets=btc&metrics=PriceUSD,HashRate,BlkCnt",
    "&frequency=1d&start_time=2011-01-01&page_size=10000"
  )
  resp <- tryCatch(httr::GET(url, httr::timeout(30)), error = function(e) NULL)
  if (!is.null(resp) && httr::status_code(resp) == 200) {
    raw  <- jsonlite::fromJSON(
      httr::content(resp, as = "text", encoding = "UTF-8"),
      simplifyDataFrame = TRUE
    )
    df_r <- as.data.frame(raw$data) %>%
      mutate(
        date     = as.Date(substr(time, 1, 10)),
        price    = as.numeric(PriceUSD),
        hashrate = as.numeric(HashRate),
        blkcnt   = as.numeric(BlkCnt)
      ) %>%
      filter(!is.na(date), !is.na(price), price > 0,
             !is.na(hashrate), hashrate > 0, !is.na(blkcnt)) %>%
      select(date, price, hashrate, blkcnt) %>%
      arrange(date)
    readr::write_csv(df_r, cache_csv)
    cat("[btcMinerCap] cache written\n")
  } else {
    if (!file.exists(cache_csv)) stop("Fetch failed and no cache available.")
    cat("[btcMinerCap] fetch failed, using cache\n")
  }
}

# -- load ----------------------------------------------------
df <- readr::read_csv(cache_csv, col_types = cols(
  date     = col_date(),
  price    = col_double(),
  hashrate = col_double(),
  blkcnt   = col_double()
)) %>%
  filter(!is.na(price), price > 0) %>%
  arrange(date) %>%
  mutate(
    idx        = row_number(),
    cum_blocks = cumsum(blkcnt),
    hr_smooth  = zoo::rollmedian(hashrate, k = 29, fill = NA, align = "center")
  )

# -- difficulty bottom identification ------------------------
# Anchor dates: known major miner capitulation bottoms.
# For each anchor we find the actual local HR minimum within +-30 days.
anchor_dates <- as.Date(c(
  "2011-12-15",   # earliest: pre-2012 bear
  "2014-12-17",   # 2014-15 bear bottom
  "2016-08-09",   # post-2016-halving dip
  "2018-12-29",   # 2018 bear bottom
  "2020-03-25",   # COVID crash
  "2021-06-27",   # China mining ban
  "2022-12-24",   # FTX / 2022 bear bottom
  "2024-05-27"    # post-2024-halving dip
))

# For each anchor, find the row index of the local HR minimum within +-30 days
find_local_min_near <- function(df, anchor, window_days = 30L) {
  lo <- anchor - window_days
  hi <- anchor + window_days
  sub <- df %>% filter(date >= lo, date <= hi)
  if (nrow(sub) == 0) return(NA_integer_)
  sub$idx[which.min(sub$hr_smooth)]
}

known_bottom_idxs <- sapply(anchor_dates, find_local_min_near, df = df, window_days = 30L)
known_bottom_idxs <- sort(unique(known_bottom_idxs[!is.na(known_bottom_idxs)]))

# -- detect additional bottoms in recent data (last 2 years) -
# For anything after the last known anchor, use the algorithmic approach:
# local HR min within 45 days AND >=10% below preceding 1-yr peak
last_known_date <- df$date[max(known_bottom_idxs)]
recent_df       <- df %>% filter(date > last_known_date + 60)  # 60-day buffer

hr_arr <- df$hr_smooth
recent_bottoms <- integer(0)

if (nrow(recent_df) > 90) {
  order_days <- 45L
  for (i in seq_len(nrow(recent_df))) {
    gi <- recent_df$idx[i]   # global index in df
    if (is.na(hr_arr[gi]))   next
    lo <- max(1L, gi - order_days); hi <- min(nrow(df), gi + order_days)
    if (hr_arr[gi] != min(hr_arr[lo:hi], na.rm = TRUE)) next
    # Must be a genuine drop: >=10% below 1-yr preceding peak
    peak_lo  <- max(1L, gi - 365L)
    yr_peak  <- max(hr_arr[peak_lo:gi], na.rm = TRUE)
    if (hr_arr[gi] > yr_peak * 0.90) next
    recent_bottoms <- c(recent_bottoms, gi)
  }
  # Dedup within 90-day clusters
  if (length(recent_bottoms) > 0) {
    deduped <- c(recent_bottoms[1])
    for (b in recent_bottoms[-1]) {
      if (b - tail(deduped, 1) < 90) {
        # Replace if lower
        if (hr_arr[b] < hr_arr[tail(deduped, 1)])
          deduped[length(deduped)] <- b
      } else {
        deduped <- c(deduped, b)
      }
    }
    recent_bottoms <- deduped
  }
}

all_bottom_idxs <- sort(unique(c(known_bottom_idxs, recent_bottoms)))
cat(sprintf("[btcMinerCap] %d difficulty bottoms total\n", length(all_bottom_idxs)))
for (bi in all_bottom_idxs) {
  cat(sprintf("  %s  px=$%.0f\n", df$date[bi], df$price[bi]))
}

# -- assign each row to its most recent bottom ---------------
n <- nrow(df)
assigned <- integer(n)
for (i in seq_len(n)) {
  prior <- all_bottom_idxs[all_bottom_idxs <= i]
  assigned[i] <- if (length(prior) > 0) tail(prior, 1L) else all_bottom_idxs[1]
}

df$bottom_idx       <- assigned
df$price_at_bottom  <- df$price[df$bottom_idx]
df$blocks_at_bottom <- df$cum_blocks[df$bottom_idx]
df$pct_from_bottom  <- df$price / df$price_at_bottom * 100
df$blocks_since     <- pmax(0, df$cum_blocks - df$blocks_at_bottom)

# -- trim to plot range --------------------------------------
df_plot <- df %>%
  filter(!is.na(pct_from_bottom), pct_from_bottom > 0,
         !is.na(blocks_since),
         date >= as.Date("2012-01-01"))

# -- theme (bot navy) ----------------------------------------
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
    plot.margin       = margin(10, 10, 10, 10)
  )

# -- color scale: 0 (blue) -> 50k (cyan) -> 100k (green) -> 150k (yellow) -> 200k (red) --
blk_palette <- c("#1a6fc4", "#27ae60", "#f1c40f", "#e67e22", "#e74c3c")

# -- halving dates -------------------------------------------
halvings <- as.Date(c("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-19"))

# -- y-axis --------------------------------------------------
y_brk <- c(50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000)
y_lbl <- c("50%", "100%", "200%", "500%", "1,000%",
           "2,000%", "5,000%", "10,000%", "20,000%", "50,000%")

# -- subtitle ------------------------------------------------
last_row  <- tail(df_plot %>% filter(!is.na(blocks_since)), 1)
pct_delta <- last_row$pct_from_bottom - 100
direction <- if (pct_delta >= 0) "above" else "below"
sub_txt   <- sprintf(
  "Current: %.0f%% %s last diff bottom ($%.0f)  |  Blocks since bottom: %s",
  abs(pct_delta), direction, last_row$price_at_bottom,
  format(round(last_row$blocks_since), big.mark = ",")
)

# -- plot ----------------------------------------------------
p <- ggplot(df_plot, aes(x = date, y = pct_from_bottom, color = blocks_since)) +

  geom_vline(
    xintercept = halvings,
    color      = "#8899aa",
    linetype   = "dashed",
    linewidth  = 0.5,
    alpha      = 0.8
  ) +

  annotate(
    "label",
    x        = halvings,
    y        = rep(52, 4),
    label    = rep("Halving", 4),
    fill     = "#0d3358",
    color    = "#aaccee",
    size     = 2.4,
    fontface = "bold"
  ) +

  geom_point(size = 0.85, alpha = 0.95) +

  scale_color_gradientn(
    colors = blk_palette,
    limits = c(0, 200000),
    oob    = scales::squish,
    breaks = c(0, 50000, 100000, 150000, 200000),
    labels = c("0", "50k", "100k", "150k", "200k+"),
    name   = "Blocks since\ndifficulty bottom",
    guide  = guide_colorbar(
      barwidth       = 0.8,
      barheight      = 12,
      title.position = "right",
      title.hjust    = 0.5,
      title.theme    = element_text(color = TXT, size = 8, angle = 90)
    )
  ) +

  scale_y_log10(
    breaks = y_brk,
    labels = y_lbl,
    limits = c(48, 60000),
    expand = c(0, 0)
  ) +

  scale_x_date(
    date_breaks = "1 year",
    date_labels = "%Y",
    expand      = c(0.01, 0)
  ) +

  labs(
    title    = "Miner Capitulation - BTC Price / Price at Difficulty Bottom",
    subtitle = sub_txt,
    x        = "Day",
    y        = "BTC price / price at diff bottom",
    caption  = "Source: CoinMetrics (HashRate, BlkCnt, PriceUSD) | JHCV"
  ) +
  theme_btc

# -- save ----------------------------------------------------
ggsave(out_png, p, width = 12, height = 7, dpi = 150, bg = BG)
cat(sprintf("[btcMinerCap] saved -> %s\n", out_png))
