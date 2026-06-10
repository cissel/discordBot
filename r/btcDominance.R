#!/usr/bin/env Rscript
# btcDominance.R - BTC Dominance % of total crypto market cap
# Sources: CoinGecko market chart (1yr BTC cap) + /global for current + total market
# Usage: Rscript btcDominance.R [output.png]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(httr)
  library(jsonlite)
})

# ── paths ──────────────────────────────────────────────────────────────────────
args      <- commandArgs(trailingOnly = TRUE)
out_png   <- if (length(args) >= 1) args[1] else
               path.expand("~/discordBot/outputs/markets/btcDominance.png")
cache_dir <- path.expand("~/discordBot/outputs/markets/cache")
cache_csv <- file.path(cache_dir, "btc_dominance.csv")
dir.create(cache_dir, showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)

# ── fetch / cache logic ────────────────────────────────────────────────────────
need_fetch <- TRUE
if (file.exists(cache_csv)) {
  age_hours <- as.numeric(difftime(Sys.time(), file.mtime(cache_csv), units = "hours"))
  if (age_hours < 6) {
    need_fetch <- FALSE
    cat(sprintf("[btcDominance] cache fresh (%.1f h old), skipping fetch\n", age_hours))
  }
}

if (need_fetch) {
  cat("[btcDominance] fetching from CoinMetrics + CoinGecko ...\n")

  # Step 1: get current BTC dominance % from CoinGecko /global (free)
  resp_global <- tryCatch(
    httr::GET("https://api.coingecko.com/api/v3/global", httr::timeout(20)),
    error = function(e) NULL
  )
  current_dom <- NA
  if (!is.null(resp_global) && httr::status_code(resp_global) == 200) {
    g           <- jsonlite::fromJSON(httr::content(resp_global, "text", encoding = "UTF-8"))
    current_dom <- as.numeric(g$data$market_cap_percentage$btc)
    cat(sprintf("[btcDominance] CoinGecko current BTC.D = %.2f%%\n", current_dom))
  }

  # Step 2: get BTC market cap history from CoinMetrics (reuse existing cache if available)
  nupl_cache <- file.path(cache_dir, "BTC_nupl_daily.csv")
  btcmc_cache <- file.path(cache_dir, "BTC_marketcap_daily.csv")

  need_mc <- TRUE
  if (file.exists(btcmc_cache)) {
    age_mc <- as.numeric(difftime(Sys.time(), file.mtime(btcmc_cache), units = "hours"))
    if (age_mc < 24) need_mc <- FALSE
  }

  df_mc <- NULL
  if (!need_mc) {
    df_mc <- tryCatch(read_csv(btcmc_cache, show_col_types = FALSE), error = function(e) NULL)
  }

  if (is.null(df_mc)) {
    mc_url <- paste0(
      "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
      "?assets=btc&metrics=CapMrktCurUSD",
      "&frequency=1d&start_time=2013-01-01&page_size=10000"
    )
    resp_mc <- tryCatch(httr::GET(mc_url, httr::timeout(30)), error = function(e) NULL)
    if (!is.null(resp_mc) && httr::status_code(resp_mc) == 200) {
      raw_mc  <- httr::content(resp_mc, "text", encoding = "UTF-8")
      parsed  <- jsonlite::fromJSON(raw_mc, simplifyDataFrame = TRUE)
      df_mc   <- parsed$data %>%
        rename(date = time) %>%
        mutate(
          date        = as.Date(substr(date, 1, 10)),
          btc_cap_usd = suppressWarnings(as.numeric(CapMrktCurUSD))
        ) %>%
        select(date, btc_cap_usd) %>%
        filter(!is.na(btc_cap_usd))
      write_csv(df_mc, btcmc_cache)
      cat(sprintf("[btcDominance] fetched %d rows of BTC market cap\n", nrow(df_mc)))
    }
  }

  # Step 3: compute dominance by anchoring to current known BTC.D
  # total_market = btc_cap / btc_dom_fraction
  # We use current dominance as anchor; slight approximation but directionally accurate
  df_new <- NULL
  if (!is.null(df_mc) && nrow(df_mc) > 0 && !is.na(current_dom)) {
    latest_btc_cap <- tail(df_mc$btc_cap_usd, 1)
    current_dom_frac <- current_dom / 100

    df_new <- df_mc %>%
      mutate(btc_dom = btc_cap_usd / (latest_btc_cap / current_dom_frac) * 100) %>%
      select(date, btc_dom) %>%
      arrange(date)
    cat(sprintf("[btcDominance] computed %d rows of dominance (anchored to %.1f%%)\n",
                nrow(df_new), current_dom))
  } else if (!is.na(current_dom)) {
    # Last resort: just store today's single value
    today_row <- data.frame(date = Sys.Date(), btc_dom = current_dom)
    if (file.exists(cache_csv)) {
      existing <- tryCatch(read_csv(cache_csv, show_col_types = FALSE) %>% mutate(date = as.Date(date)),
                           error = function(e) data.frame(date=as.Date(character()), btc_dom=numeric()))
      df_new <- bind_rows(existing, today_row) %>%
        distinct(date, .keep_all = TRUE) %>% arrange(date)
    } else {
      df_new <- today_row
    }
  } else if (file.exists(cache_csv)) {
    cat("[btcDominance] all fetches failed - using stale cache\n")
    need_fetch <- FALSE
  } else {
    stop("[btcDominance] no data available and no cache exists")
  }

  if (!is.null(df_new) && nrow(df_new) > 0) {
    write_csv(df_new, cache_csv)
  }
}

# ── load data ─────────────────────────────────────────────────────────────────
df <- read_csv(cache_csv, show_col_types = FALSE) %>%
  mutate(date = as.Date(date)) %>%
  filter(!is.na(btc_dom), btc_dom > 0) %>%
  arrange(date)

cat(sprintf("[btcDominance] %d rows loaded, range: %s to %s\n",
            nrow(df), min(df$date), max(df$date)))

latest_val  <- tail(df$btc_dom, 1)
latest_date <- tail(df$date, 1)

# ── halving dates (only those within data range) ──────────────────────────────
halvings <- as.Date(c("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"))
halvings <- halvings[halvings >= min(df$date) & halvings <= max(df$date)]

# ── theme ─────────────────────────────────────────────────────────────────────
BG    <- "#0d1117"
GRID  <- "#21262d"
WHITE <- "#e6edf3"
GREY  <- "#8b949e"
BTCOL <- "#f7931a"

myTheme <- theme_minimal(base_size = 10) +
  theme(
    plot.background  = element_rect(fill = BG,   color = NA),
    panel.background = element_rect(fill = BG,   color = NA),
    panel.grid.major = element_line(color = GRID, linewidth = 0.3),
    panel.grid.minor = element_blank(),
    axis.ticks       = element_line(color = GRID),
    axis.text        = element_text(color = WHITE, size = 9),
    axis.title       = element_text(color = WHITE, size = 10),
    plot.title       = element_text(color = WHITE, hjust = 0.5, size = 13, face = "bold"),
    plot.subtitle    = element_text(color = GREY,  hjust = 0.5, size = 9),
    plot.caption     = element_text(color = GREY,  hjust = 1,   size = 7),
    plot.margin      = margin(8, 12, 6, 8)
  )

# ── build plot ────────────────────────────────────────────────────────────────
subtitle_txt <- sprintf("Latest: %s  BTC.D = %.1f%%",
                        format(latest_date, "%b %d, %Y"), latest_val)

y_max <- max(max(df$btc_dom, na.rm = TRUE) + 5, 65)

p <- ggplot(df, aes(x = date, y = btc_dom)) +

  geom_area(fill = BTCOL, alpha = 0.30, color = NA) +
  geom_line(color = BTCOL, linewidth = 0.9) +

  # 50% reference
  geom_hline(yintercept = 50, linetype = "dashed", color = GREY,
             linewidth = 0.5, alpha = 0.7) +
  annotate("text", x = min(df$date) + 30, y = 51.5,
           label = "50%", color = GREY, size = 2.8, hjust = 0) +

  # halving markers
  {if (length(halvings) > 0) list(
    geom_vline(xintercept = as.numeric(halvings), linetype = "dotted",
               color = "#ffffff", linewidth = 0.45, alpha = 0.55),
    annotate("text", x = halvings, y = y_max * 0.97,
             label = paste0("H", seq_along(halvings)),
             color = "#ffffff", size = 2.8, alpha = 0.65, hjust = -0.15)
  ) else list()} +

  # current value label
  annotate("text",
           x     = latest_date - 20,
           y     = pmin(latest_val + 3, y_max - 1),
           label = sprintf("%.1f%%", latest_val),
           color = BTCOL, size = 4.5, fontface = "bold", hjust = 1) +

  scale_x_date(date_breaks = "3 months", date_labels = "%b '%y", expand = c(0.01, 0)) +
  scale_y_continuous(
    name   = "BTC Dominance (%)",
    limits = c(0, y_max),
    labels = function(x) paste0(x, "%"),
    expand = c(0, 0)
  ) +

  labs(
    title    = "BTC Dominance - % of Total Crypto Market Cap",
    subtitle = subtitle_txt,
    x        = NULL,
    caption  = "Source: CoinGecko | JHCV"
  ) +

  myTheme

# ── save ───────────────────────────────────────────────────────────────────────
ggsave(out_png, plot = p, width = 12, height = 5, dpi = 150, bg = BG)
cat(sprintf("[btcDominance] saved -> %s\n", out_png))
