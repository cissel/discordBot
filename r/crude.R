# crude.R by JHCV
# WTI Crude Oil futures (CL=F, NYMEX/CME Globex) via Yahoo Finance chart endpoint
# Usage: Rscript crude.R [output_path] [timeframe]
# Timeframes: intraday, 1w, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max (default: 6mo)

suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(patchwork)
  library(lubridate)
})

##### Config #####
BG        <- "#02233F"
GRID      <- "#274066"
SYMBOL    <- "CL=F"
CACHE_DIR <- path.expand("~/discordBot/outputs/markets/cache")

args      <- commandArgs(trailingOnly = TRUE)
OUTPUT    <- if (length(args) >= 1) args[1] else
             path.expand("~/discordBot/outputs/markets/crudewti.png")
TIMEFRAME <- if (length(args) >= 2) args[2] else "6mo"

dir.create(CACHE_DIR,       showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(OUTPUT), showWarnings = FALSE, recursive = TRUE)

##### Timeframe -> Yahoo interval/range + cache TTL + display label #####
TF_MAP <- list(
  intraday = list(interval = "5m",  range = "1d",   ttl = 300,  label = "Intraday (5min bars)"),
  `1w`     = list(interval = "15m", range = "5d",   ttl = 900,  label = "1 Week (15min bars)"),
  `1mo`    = list(interval = "1h",  range = "1mo",  ttl = 1800, label = "1 Month (1hr bars)"),
  `3mo`    = list(interval = "1d",  range = "3mo",  ttl = 3600, label = "3 Months"),
  `6mo`    = list(interval = "1d",  range = "6mo",  ttl = 3600, label = "6 Months"),
  `1y`     = list(interval = "1d",  range = "1y",   ttl = 3600, label = "1 Year"),
  `2y`     = list(interval = "1d",  range = "2y",   ttl = 3600, label = "2 Years"),
  `5y`     = list(interval = "1wk", range = "5y",   ttl = 21600,label = "5 Years"),
  `10y`    = list(interval = "1wk", range = "10y",  ttl = 21600,label = "10 Years"),
  max      = list(interval = "1mo", range = "max",  ttl = 21600,label = "Max")
)

if (!TIMEFRAME %in% names(TF_MAP)) {
  stop(paste0("Unknown timeframe '", TIMEFRAME, "' - use one of: ",
              paste(names(TF_MAP), collapse = ", ")))
}

tf_cfg      <- TF_MAP[[TIMEFRAME]]
IS_INTRADAY <- grepl("^[0-9]+[mh]$", tf_cfg$interval)  # sub-daily bar granularity (e.g. 5m, 15m, 1h)

##### Theme #####
myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = BG),
                 panel.background = element_rect(fill = BG),
                 panel.grid = element_line(color = GRID),
                 axis.ticks = element_line(color = GRID),
                 axis.text = element_text(color = "white"),
                 axis.title = element_text(color = "white"),
                 plot.title = element_text(color = "white", hjust = .5),
                 plot.subtitle = element_text(color = "white", hjust = .5),
                 plot.caption = element_text(color = "white"),
                 strip.background = element_rect(fill = BG),
                 strip.text = element_text(color = "white"))

##### Fetch + Cache (Yahoo Finance chart endpoint, no API key required) #####
fetch_crude <- function() {
  cache_file <- file.path(CACHE_DIR, paste0("crudewti_", TIMEFRAME, ".csv"))

  if (file.exists(cache_file)) {
    age <- as.numeric(difftime(Sys.time(), file.info(cache_file)$mtime, units = "secs"))
    if (age < tf_cfg$ttl) {
      cat("Cache hit: crude oil", TIMEFRAME, "(age:", round(age), "s)\n")
      cached <- read_csv(cache_file, col_types = cols(), show_col_types = FALSE)
      cached$time <- with_tz(cached$time, "America/New_York")
      return(cached)
    }
  }

  cat("Fetching CL=F from Yahoo Finance (", tf_cfg$interval, "/", tf_cfg$range, ")...\n")
  url <- paste0(
    "https://query1.finance.yahoo.com/v8/finance/chart/",
    URLencode(SYMBOL, reserved = TRUE),
    "?interval=", tf_cfg$interval, "&range=", tf_cfg$range
  )

  resp <- GET(url, add_headers("User-Agent" = "Mozilla/5.0"))
  if (status_code(resp) != 200) {
    stop("Yahoo Finance request failed with HTTP ", status_code(resp))
  }

  dat    <- fromJSON(content(resp, "text", encoding = "UTF-8"), simplifyVector = FALSE)
  result <- dat$chart$result[[1]]
  if (is.null(result)) stop("Yahoo Finance returned no result for ", SYMBOL)

  ts     <- unlist(result$timestamp)
  quote  <- result$indicators$quote[[1]]
  opens  <- unlist(quote$open)
  closes <- unlist(quote$close)
  vols   <- unlist(quote$volume)
  if (is.null(ts) || length(ts) == 0) stop("Yahoo Finance returned no timestamps for ", SYMBOL)

  n <- min(length(ts), length(closes))
  df <- tibble(
    time   = as.POSIXct(ts[seq_len(n)], origin = "1970-01-01", tz = "America/New_York"),
    open   = as.numeric(opens[seq_len(n)]),
    close  = as.numeric(closes[seq_len(n)]),
    volume = if (length(vols) >= n) as.numeric(vols[seq_len(n)]) else NA_real_
  ) %>%
    filter(!is.na(close)) %>%
    arrange(time)

  write_csv(df, cache_file)
  df
}

df <- fetch_crude()

if (nrow(df) < 2) {
  stop("Not enough crude oil data points to plot.")
}

df$time_axis <- if (IS_INTRADAY) df$time else as.Date(df$time)

##### Stats #####
latest       <- tail(df$close, 1)
latest_time  <- tail(df$time, 1)
window_open  <- df$open[1]
window_pct   <- (latest - window_open) / window_open * 100
window_label <- sprintf("%+.2f%%", window_pct)

##### Price plot #####
p_price <- ggplot(df, aes(x = time_axis, y = close)) +
  geom_line(color = "white", linewidth = 0.9) +
  geom_hline(yintercept = latest, color = "white", alpha = 0.4, linetype = "dashed") +
  scale_y_continuous(labels = scales::dollar) +
  labs(title = paste0("Crude Oil - WTI Futures (CL=F) - ", tf_cfg$label,
                      " as of ", format(latest_time, "%b %d %H:%M %Z")),
       subtitle = paste0("$", sprintf("%.2f", latest), "  (", window_label, ")"),
       x = NULL,
       y = "Price per Barrel (USD)") +
  myTheme +
  theme(axis.text.x = if (!is.null(df$volume) && any(!is.na(df$volume))) element_blank() else element_text(color = "white"),
        axis.ticks.x = if (!is.null(df$volume) && any(!is.na(df$volume))) element_blank() else element_line(color = GRID))

HAS_VOLUME <- any(!is.na(df$volume)) && sum(df$volume, na.rm = TRUE) > 0

if (HAS_VOLUME) {
  vol_scale <- if (max(df$volume, na.rm = TRUE) >= 1e6) 1e6 else 1e3
  vol_unit  <- if (vol_scale == 1e6) "M" else "K"

  p_vol <- ggplot(df, aes(x = time_axis, y = volume / vol_scale)) +
    geom_col(fill = "#00bfff", na.rm = TRUE) +
    scale_y_continuous(labels = function(x) paste0(x, vol_unit)) +
    labs(x = NULL, y = "Volume",
         caption = "Source: Yahoo Finance | CME Globex WTI futures (CL=F), ~15min delayed | JHCV") +
    myTheme +
    theme(plot.title = element_blank(), plot.subtitle = element_blank())

  p <- p_price / p_vol + plot_layout(heights = c(7, 3)) &
    theme(plot.background = element_rect(fill = BG, color = NA))
} else {
  p <- p_price +
    labs(caption = "Source: Yahoo Finance | CME Globex WTI futures (CL=F), ~15min delayed | JHCV")
}

##### Save plot #####
ggsave(OUTPUT, plot = p, width = 8, height = 5.5, dpi = 300, bg = BG)
cat("Saved:", OUTPUT, "\n")
