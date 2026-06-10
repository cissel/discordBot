#!/usr/bin/env Rscript
# btcRealizedPrice.R
# Risk Indicator: BTC Price / Realized Price / True Market Mean /
#                 Active Investor Mean / STH-Realized Price
# Data: CoinMetrics Community API (free tier)
# All on-chain series derived from: PriceUSD, CapMrktCurUSD, CapMVRVCur, SplyCur
# Usage: Rscript btcRealizedPrice.R [output.png]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(httr)
  library(jsonlite)
  library(tidyr)
})

# -- paths ---------------------------------------------------
args      <- commandArgs(trailingOnly = TRUE)
out_png   <- if (length(args) >= 1) args[1] else
               path.expand("~/discordBot/outputs/markets/btcRealizedPrice.png")
cache_dir <- path.expand("~/discordBot/outputs/markets/cache")
cache_csv <- file.path(cache_dir, "BTC_realized_daily.csv")
dir.create(cache_dir, showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)

# -- fetch / cache -------------------------------------------
need_fetch <- TRUE
if (file.exists(cache_csv)) {
  age_h <- as.numeric(difftime(Sys.time(), file.mtime(cache_csv), units = "hours"))
  if (age_h < 24) need_fetch <- FALSE
}

if (need_fetch) {
  cat("[btcRealizedPrice] fetching CoinMetrics...\n")
  url <- paste0(
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "?assets=btc&metrics=PriceUSD,CapMrktCurUSD,CapMVRVCur,SplyCur",
    "&frequency=1d&start_time=2011-01-01&page_size=10000"
  )
  resp <- tryCatch(httr::GET(url, httr::timeout(30)), error = function(e) NULL)
  if (!is.null(resp) && httr::status_code(resp) == 200) {
    raw  <- jsonlite::fromJSON(httr::content(resp, as = "text", encoding = "UTF-8"),
                               simplifyDataFrame = TRUE)
    df_r <- as.data.frame(raw$data) %>%
      mutate(
        date          = as.Date(substr(time, 1, 10)),
        price         = as.numeric(PriceUSD),
        mktcap        = as.numeric(CapMrktCurUSD),
        mvrv          = as.numeric(CapMVRVCur),
        supply        = as.numeric(SplyCur)
      ) %>%
      filter(!is.na(date), !is.na(price), price > 0) %>%
      select(date, price, mktcap, mvrv, supply) %>%
      arrange(date)
    readr::write_csv(df_r, cache_csv)
    cat("[btcRealizedPrice] cache written\n")
  } else {
    if (!file.exists(cache_csv)) stop("Fetch failed and no cache available.")
    cat("[btcRealizedPrice] fetch failed, using existing cache\n")
  }
}

# -- load ----------------------------------------------------
df <- readr::read_csv(cache_csv, col_types = cols(
  date   = col_date(),
  price  = col_double(),
  mktcap = col_double(),
  mvrv   = col_double(),
  supply = col_double()
)) %>%
  filter(!is.na(price), price > 0, !is.na(mvrv), mvrv > 0, !is.na(supply), supply > 0) %>%
  arrange(date)

# -- derive on-chain series ----------------------------------
# Realized Price = Realized Market Cap / Circulating Supply
#   where Realized Cap = Market Cap / MVRV
df <- df %>%
  mutate(
    realized_cap   = mktcap / mvrv,
    realized_price = realized_cap / supply,

    # True Market Mean: 365-day rolling average of realized price
    # (approximates thermocap-adjusted mean holder cost)
    true_mkt_mean  = zoo::rollmean(realized_price, k = 365, fill = NA, align = "right"),

    # Active Realized Price: 90-day rolling average of realized price
    # (shorter window = recently active / "hot" supply cost basis)
    active_realized = zoo::rollmean(realized_price, k = 90, fill = NA, align = "right"),

    # STH Cost Basis: 155-day rolling average of BTC spot price
    # STH = coins moved in last ~155 days; their avg acquisition = medium-term price MA
    sth_cost_basis = zoo::rollmean(price, k = 155, fill = NA, align = "right")
  )

# -- Cycle ATH horizontal lines ------------------------------
# Historical cycle peaks (all-time highs per halving cycle)
cycle_aths <- data.frame(
  cycle = c("Cycle 1 ATH", "Cycle 2 ATH", "Cycle 3 ATH", "Cycle 4 ATH"),
  price = c(1242, 19783, 68991, 108786),
  color = c("#f39c12", "#e74c3c", "#3498db", "#2ecc71"),
  stringsAsFactors = FALSE
)

# -- date range: last 5 years --------------------------------
# Glassnode chart shows roughly Oct 2021 to present; use 2020-01-01 for more context
plot_start <- as.Date("2020-10-01")
df_plot    <- df %>% filter(date >= plot_start)

# Halving that's visible in this window (May 2020, April 2024)
halvings_vis <- as.Date(c("2020-05-11", "2024-04-19"))

# -- last values for subtitle --------------------------------
last       <- tail(df_plot %>% filter(!is.na(realized_price)), 1)
last_price <- last$price
last_rp    <- last$realized_price
last_sth   <- last$sth_cost_basis
last_tmm   <- last$true_mkt_mean
last_arm   <- last$active_realized

# -- theme (bot navy) ----------------------------------------
BG   <- "#02233F"
GRID <- "#274066"
TXT  <- "white"

theme_btc <- theme_minimal(base_size = 11) +
  theme(
    plot.background  = element_rect(fill = BG, color = NA),
    panel.background = element_rect(fill = BG, color = NA),
    panel.grid.major = element_line(color = GRID, linewidth = 0.3),
    panel.grid.minor = element_blank(),
    axis.text        = element_text(color = TXT, size = 9),
    axis.title       = element_text(color = TXT, size = 10),
    plot.title       = element_text(color = TXT, face = "bold", size = 13, hjust = 0),
    plot.subtitle    = element_text(color = "#aaaaaa", size = 9, hjust = 0),
    plot.caption     = element_text(color = "#777777", size = 8, hjust = 1),
    legend.background = element_rect(fill = BG, color = NA),
    legend.key        = element_rect(fill = BG, color = NA),
    legend.text       = element_text(color = TXT, size = 8),
    legend.title      = element_blank(),
    legend.position   = "top",
    legend.spacing.x  = unit(0.3, "cm"),
    plot.margin       = margin(10, 15, 10, 10)
  )

# -- y-axis: log10 scale with K labels -----------------------
y_lo  <- 5000
y_hi  <- 150000
y_brk <- c(10000, 20000, 30000, 40000, 50000,
           60000, 70000, 80000, 90000, 100000, 120000, 140000)
y_lbl <- paste0(y_brk / 1000, "K")

# -- build plot ----------------------------------------------
p <- ggplot(df_plot, aes(x = date))

# Cycle ATH horizontal lines
for (i in seq_len(nrow(cycle_aths))) {
  ath <- cycle_aths[i, ]
  if (ath$price >= y_lo && ath$price <= y_hi * 1.1) {
    p <- p +
      geom_hline(yintercept = ath$price,
                 color      = ath$color,
                 linetype   = "solid",
                 linewidth  = 0.8,
                 alpha      = 0.85)
  }
}

# Halving vertical lines
p <- p +
  geom_vline(xintercept = halvings_vis,
             color      = "#ffffff",
             linetype   = "dotted",
             linewidth  = 0.5,
             alpha      = 0.5)

# On-chain series (under BTC price)
p <- p +
  geom_line(aes(y = realized_price,  color = "Realized Price"),        linewidth = 0.9, na.rm = TRUE) +
  geom_line(aes(y = active_realized, color = "Active Realized Price"),  linewidth = 0.9, na.rm = TRUE) +
  geom_line(aes(y = true_mkt_mean,   color = "True Market Mean"),       linewidth = 0.9, na.rm = TRUE) +
  geom_line(aes(y = sth_cost_basis,  color = "STH Cost Basis"),         linewidth = 0.9, na.rm = TRUE)

# BTC spot price on top
p <- p +
  geom_line(aes(y = price, color = "BTC Price"), linewidth = 1.0)

# Color scale matching Glassnode style
p <- p +
  scale_color_manual(
    values = c(
      "BTC Price"             = "#ffffff",
      "Realized Price"        = "#3498db",
      "Active Realized Price" = "#f39c12",
      "True Market Mean"      = "#a8d08d",
      "STH Cost Basis"        = "#e74c3c"
    ),
    breaks = c("BTC Price", "Realized Price", "Active Realized Price",
               "True Market Mean", "STH Cost Basis")
  )

# Scales
p <- p +
  scale_y_continuous(
    limits = c(y_lo, y_hi),
    breaks = y_brk,
    labels = y_lbl,
    expand = c(0, 0)
  ) +
  scale_x_date(
    date_breaks = "3 months",
    date_labels = "%b '%y",
    expand      = c(0.01, 0)
  )

# Cycle ATH labels on right margin
for (i in seq_len(nrow(cycle_aths))) {
  ath <- cycle_aths[i, ]
  if (ath$price >= y_lo && ath$price <= y_hi * 1.1) {
    p <- p +
      annotate("text",
               x     = max(df_plot$date) + 1,
               y     = ath$price,
               label = ath$cycle,
               color = ath$color,
               hjust = -0.05,
               size  = 2.8,
               fontface = "bold")
  }
}

p <- p +
  coord_cartesian(clip = "off") +
  labs(
    title    = "Risk Indicator: Realized Price / True Market Mean / Active Investor Mean / STH Cost Basis",
    subtitle = sprintf(
      "BTC: $%s  |  Realized: $%s  |  STH CB: $%s  |  Active RP: $%s  |  True Mkt Mean: $%s",
      format(round(last_price),  big.mark = ","),
      format(round(last_rp),     big.mark = ","),
      format(round(last_sth),    big.mark = ","),
      format(round(last_arm),    big.mark = ","),
      format(round(last_tmm),    big.mark = ",")
    ),
    x       = NULL,
    y       = "Price (USD)",
    caption = "Source: CoinMetrics (free tier) | JHCV"
  ) +
  theme_btc +
  theme(
    plot.margin = margin(10, 120, 10, 10)   # right margin for ATH labels
  )

# -- save ----------------------------------------------------
ggsave(out_png, p, width = 12, height = 6.5, dpi = 150, bg = BG)
cat(sprintf("[btcRealizedPrice] saved -> %s\n", out_png))
