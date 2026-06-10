#!/usr/bin/env Rscript
# btcMVRV.R - BTC MVRV Ratio (Market Value to Realized Value) chart
# Data: CoinMetrics community API - CapMVRVCur, PriceUSD
# Usage: Rscript btcMVRV.R [output.png]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(httr)
  library(jsonlite)
})

# -- paths ---------------------------------------------------------------------
args      <- commandArgs(trailingOnly = TRUE)
out_png   <- if (length(args) >= 1) args[1] else
               path.expand("~/discordBot/outputs/markets/btcMVRV.png")
cache_dir <- path.expand("~/discordBot/outputs/markets/cache")
cache_csv <- file.path(cache_dir, "BTC_nupl_daily.csv")
dir.create(cache_dir, showWarnings = FALSE, recursive = TRUE)

# -- fetch / cache logic -------------------------------------------------------
need_fetch <- TRUE
if (file.exists(cache_csv)) {
  age_hours <- as.numeric(difftime(Sys.time(), file.mtime(cache_csv), units = "hours"))
  if (age_hours < 24) {
    need_fetch <- FALSE
    cat(sprintf("[btcMVRV] cache fresh (%.1f h old), skipping fetch\n", age_hours))
  } else {
    cat(sprintf("[btcMVRV] cache stale (%.1f h old), re-fetching\n", age_hours))
  }
}

if (need_fetch) {
  url <- paste0(
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "?assets=btc&metrics=CapMVRVCur,PriceUSD",
    "&frequency=1d&start_time=2010-07-18&page_size=10000"
  )
  cat("[btcMVRV] fetching from CoinMetrics ...\n")
  resp <- tryCatch(httr::GET(url, httr::timeout(30)), error = function(e) NULL)

  ok <- !is.null(resp) && httr::status_code(resp) == 200
  if (ok) {
    raw    <- httr::content(resp, as = "text", encoding = "UTF-8")
    parsed <- jsonlite::fromJSON(raw, simplifyDataFrame = TRUE)
    df_raw <- parsed$data

    df_raw <- df_raw %>%
      rename(date = time) %>%
      mutate(
        date       = as.Date(substr(date, 1, 10)),
        CapMVRVCur = suppressWarnings(as.numeric(CapMVRVCur)),
        PriceUSD   = suppressWarnings(as.numeric(PriceUSD))
      )

    write_csv(df_raw, cache_csv)
    cat(sprintf("[btcMVRV] saved %d rows to cache\n", nrow(df_raw)))
  } else {
    code <- if (!is.null(resp)) httr::status_code(resp) else "no response"
    cat(sprintf("[btcMVRV] WARNING: fetch failed (%s)", code))
    if (file.exists(cache_csv)) {
      cat(" - using stale cache\n")
    } else {
      stop(" - no cache available, aborting")
    }
  }
}

# -- load & filter -------------------------------------------------------------
df <- read_csv(cache_csv, show_col_types = FALSE) %>%
  mutate(
    date       = as.Date(date),
    CapMVRVCur = suppressWarnings(as.numeric(CapMVRVCur)),
    PriceUSD   = suppressWarnings(as.numeric(PriceUSD))
  ) %>%
  filter(!is.na(CapMVRVCur), CapMVRVCur > 0) %>%
  arrange(date)

cat(sprintf("[btcMVRV] %d rows after filtering\n", nrow(df)))

# -- zone classification -------------------------------------------------------
mvrv_lo <- 0
mvrv_hi <- 7

df <- df %>%
  mutate(
    zone = case_when(
      CapMVRVCur < 1.0               ~ "Undervalued",
      CapMVRVCur >= 1.0 & CapMVRVCur < 2.0  ~ "Fair Value",
      CapMVRVCur >= 2.0 & CapMVRVCur < 3.5  ~ "Overvalued",
      CapMVRVCur >= 3.5              ~ "Euphoria",
      TRUE                           ~ NA_character_
    ),
    zone = factor(zone, levels = c("Undervalued", "Fair Value", "Overvalued", "Euphoria"))
  )

zone_colors <- c(
  "Undervalued" = "#e74c3c",
  "Fair Value"  = "#e67e22",
  "Overvalued"  = "#f1c40f",
  "Euphoria"    = "#3498db"
)

# -- price overlay: map log10(price) into MVRV y range ------------------------
log_price <- log10(df$PriceUSD[!is.na(df$PriceUSD)])
lp_min    <- floor(min(log_price, na.rm = TRUE))
lp_max    <- ceiling(max(log_price, na.rm = TRUE))

price_to_mvrv <- function(p) {
  mvrv_lo + (log10(p) - lp_min) / (lp_max - lp_min) * (mvrv_hi - mvrv_lo)
}
mvrv_to_price <- function(m) {
  10 ^ (lp_min + (m - mvrv_lo) / (mvrv_hi - mvrv_lo) * (lp_max - lp_min))
}

df <- df %>%
  mutate(price_scaled = ifelse(!is.na(PriceUSD), price_to_mvrv(PriceUSD), NA_real_))

# -- static background band data -----------------------------------------------
x_min <- min(df$date)
x_max <- max(df$date)

bands <- data.frame(
  zone = factor(
    c("Undervalued", "Fair Value", "Overvalued", "Euphoria"),
    levels = c("Undervalued", "Fair Value", "Overvalued", "Euphoria")
  ),
  ymin = c(mvrv_lo, 1.0, 2.0, 3.5),
  ymax = c(1.0,     2.0, 3.5, mvrv_hi)
)

# -- subtitle info -------------------------------------------------------------
latest_row  <- tail(df, 1)
latest_date <- format(latest_row$date, "%b %d %Y")
latest_mvrv <- round(latest_row$CapMVRVCur, 2)
latest_zone <- as.character(latest_row$zone)

subtitle_text <- sprintf(
  "Latest: %s  MVRV = %.2f  [%s]",
  latest_date, latest_mvrv, latest_zone
)

# -- palette / theme -----------------------------------------------------------
BG    <- "#0d1117"
GRID  <- "#21262d"
WHITE <- "#e6edf3"
GREY  <- "#8b949e"

myTheme <- theme_minimal(base_size = 10) +
  theme(
    plot.background    = element_rect(fill = BG,   color = NA),
    panel.background   = element_rect(fill = BG,   color = NA),
    panel.grid.major   = element_line(color = GRID, linewidth = 0.3),
    panel.grid.minor   = element_blank(),
    axis.ticks         = element_line(color = GRID),
    axis.text          = element_text(color = WHITE, size = 9),
    axis.title         = element_text(color = WHITE, size = 10),
    axis.title.y.right = element_text(color = GREY,  size = 10),
    axis.text.y.right  = element_text(color = GREY,  size = 8),
    plot.title         = element_text(color = WHITE, hjust = 0.5, size = 13, face = "bold"),
    plot.subtitle      = element_text(color = GREY,  hjust = 0.5, size = 9),
    plot.caption       = element_text(color = GREY,  hjust = 1,   size = 7),
    legend.background  = element_rect(fill = BG,   color = NA),
    legend.key         = element_rect(fill = BG,   color = NA),
    legend.text        = element_text(color = WHITE, size = 8),
    legend.title       = element_text(color = WHITE, size = 9),
    legend.position    = "bottom",
    legend.key.width   = unit(1.4, "cm"),
    legend.key.height  = unit(0.35, "cm"),
    strip.background   = element_rect(fill = BG, color = NA),
    strip.text         = element_text(color = WHITE),
    plot.margin        = margin(8, 12, 6, 8)
  )

# -- halving dates -------------------------------------------------------------
halvings <- as.Date(c("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"))

# -- build plot ----------------------------------------------------------------
p <- ggplot() +

  # static background bands (full width)
  geom_rect(
    data = bands,
    aes(xmin = x_min, xmax = x_max, ymin = ymin, ymax = ymax, fill = zone),
    alpha = 0.25,
    inherit.aes = FALSE
  ) +

  # halving vertical lines
  geom_vline(
    xintercept = halvings,
    color = "#ffffff", linewidth = 0.45, linetype = "dotted", alpha = 0.5
  ) +

  # bold horizontal line at MVRV = 1 (realized value baseline)
  geom_hline(
    yintercept = 1,
    linetype = "solid", color = "#e74c3c", linewidth = 0.9, alpha = 0.85
  ) +

  # BTC price overlay (log-scaled into MVRV space)
  geom_line(
    data = df %>% filter(!is.na(price_scaled)),
    aes(x = date, y = price_scaled),
    color = GREY, linewidth = 0.65, alpha = 0.70,
    inherit.aes = FALSE
  ) +

  # MVRV ratio line
  geom_line(
    data = df,
    aes(x = date, y = CapMVRVCur),
    color = "#00bfff", linewidth = 0.9, alpha = 0.95,
    inherit.aes = FALSE
  ) +

  # halving labels near top
  annotate(
    "text",
    x = halvings, y = mvrv_hi - 0.25,
    label = c("H1", "H2", "H3", "H4"),
    color = "#ffffff", size = 2.8, alpha = 0.65, hjust = -0.15
  ) +

  # fills for legend
  scale_fill_manual(
    name   = "MVRV Zone",
    values = zone_colors,
    guide  = guide_legend(nrow = 1, override.aes = list(alpha = 0.55))
  ) +

  scale_x_date(
    date_breaks = "1 year", date_labels = "%Y", expand = c(0.01, 0)
  ) +

  scale_y_continuous(
    name   = "MVRV Ratio",
    limits = c(mvrv_lo, mvrv_hi),
    breaks = seq(0, 7, by = 1),
    labels = number_format(accuracy = 0.1),
    expand = c(0, 0),
    sec.axis = sec_axis(
      transform = ~ mvrv_to_price(.),
      name      = "BTC Price (USD)",
      labels    = label_dollar(scale_cut = cut_short_scale(), accuracy = NULL)
    )
  ) +

  labs(
    title    = "BTC MVRV Ratio - Market Value to Realized Value",
    subtitle = subtitle_text,
    x        = NULL,
    caption  = "Source: CoinMetrics | JHCV"
  ) +

  myTheme

# -- save ----------------------------------------------------------------------
dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)
ggsave(out_png, plot = p, width = 12, height = 6, dpi = 150, bg = BG)
cat(sprintf("[btcMVRV] saved -> %s\n", out_png))
