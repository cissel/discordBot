# ============================================================
#  BTC Rainbow Chart - Power Law / Halving Cycle
#  Data: CoinMetrics Community API (no key)
# ============================================================

library(ggplot2)
library(dplyr)
library(readr)
library(scales)
library(httr)
library(jsonlite)

# -- output path ---------------------------------------------
args <- commandArgs(trailingOnly = TRUE)
out_path <- if (length(args) >= 1) args[1] else
  path.expand("~/discordBot/outputs/markets/btcRainbow.png")

dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

# -- cache logic ---------------------------------------------
cache_path <- path.expand(
  "~/discordBot/outputs/markets/cache/BTC_price_daily.csv"
)
dir.create(dirname(cache_path), recursive = TRUE, showWarnings = FALSE)

api_url <- paste0(
  "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
  "?assets=btc&metrics=PriceUSD&frequency=1d",
  "&start_time=2010-07-18&page_size=10000"
)

needs_fetch <- TRUE
if (file.exists(cache_path)) {
  age_hours <- as.numeric(
    difftime(Sys.time(), file.mtime(cache_path), units = "hours")
  )
  if (age_hours < 24) needs_fetch <- FALSE
}

if (needs_fetch) {
  message("Fetching BTC price data from CoinMetrics...")
  resp <- tryCatch(
    httr::GET(api_url, httr::timeout(30)),
    error = function(e) {
      message("HTTP request failed: ", conditionMessage(e))
      NULL
    }
  )

  ok <- !is.null(resp) && httr::status_code(resp) == 200

  if (ok) {
    parsed  <- jsonlite::fromJSON(httr::content(resp, as = "text",
                                                encoding = "UTF-8"),
                                  simplifyDataFrame = TRUE)
    df_raw  <- as.data.frame(parsed$data)
    df_save <- df_raw %>%
      select(time, PriceUSD) %>%
      rename(date = time, price_usd = PriceUSD)
    readr::write_csv(df_save, cache_path)
    message("Cache written: ", cache_path)
  } else {
    if (file.exists(cache_path)) {
      message("Fetch failed - using existing cache.")
    } else {
      stop("Fetch failed and no cache available. Cannot continue.")
    }
  }
}

# -- load cache ----------------------------------------------
df <- readr::read_csv(cache_path, col_types = cols(
  date      = col_character(),
  price_usd = col_double()
)) %>%
  mutate(date = as.Date(substr(date, 1, 10))) %>%
  filter(!is.na(date), !is.na(price_usd), price_usd > 0) %>%
  arrange(date)

# -- power-law regression ------------------------------------
genesis <- as.Date("2009-01-03")
df <- df %>%
  mutate(
    days      = as.numeric(date - genesis),
    log_days  = log10(days),
    log_price = log10(price_usd)
  ) %>%
  filter(days > 0)

fit             <- lm(log_price ~ log_days, data = df)
df$fitted_log   <- predict(fit, newdata = df)
df$fitted_price <- 10^df$fitted_log

# -- band definitions ----------------------------------------
# Offsets are in log10 units from the regression line.
# On a log scale these appear as equal-height stripes, but the
# WHOLE rainbow compresses visually as price rises because each
# log10 decade occupies a fixed pixel height - the bands don't
# expand in relative terms as BTC matures.
bands <- list(
  list(lo = 1.5,  hi = 2.0,  color = "#c0392b", label = "Maximum Bubble Territory"),
  list(lo = 1.2,  hi = 1.5,  color = "#e74c3c", label = "Sell. Seriously, SELL!"),
  list(lo = 0.9,  hi = 1.2,  color = "#e67e22", label = "FOMO Intensifies"),
  list(lo = 0.6,  hi = 0.9,  color = "#f1c40f", label = "Is this a bubble?"),
  list(lo = 0.3,  hi = 0.6,  color = "#2ecc71", label = "HODL!"),
  list(lo = 0.0,  hi = 0.3,  color = "#1abc9c", label = "Still Cheap"),
  list(lo = -0.3, hi = 0.0,  color = "#3498db", label = "Accumulate"),
  list(lo = -0.6, hi = -0.3, color = "#2980b9", label = "BUY!"),
  list(lo = -0.9, hi = -0.6, color = "#8e44ad", label = "Fire Sale")
)

# -- build ribbon data ---------------------------------------
# The key to visual compression: extend x-axis well into the future
# so the log-scale curvature is visible. On a log y-axis the bands
# occupy a fixed fraction of a decade at all times, so as the
# regression line flattens and price approaches $1M the 9 bands
# visually crowd into a narrower absolute pixel range.
date_range  <- range(df$date)
future_date <- date_range[2] + 365
all_dates   <- seq(date_range[1], future_date, by = "day")

pred_df <- data.frame(date = all_dates) %>%
  mutate(
    days       = as.numeric(date - genesis),
    log_days   = log10(pmax(days, 1)),
    fitted_log = predict(fit, newdata = data.frame(
      log_days = log10(pmax(days, 1))
    ))
  )

ribbon_list <- lapply(seq_along(bands), function(i) {
  b <- bands[[i]]
  pred_df %>%
    transmute(
      date    = date,
      ymin    = 10^(fitted_log + b$lo),
      ymax    = 10^(fitted_log + b$hi),
      color   = b$color,
      label   = b$label,
      band_id = i
    )
})
ribbon_df <- bind_rows(ribbon_list)

# -- labels: evenly-spaced legend in right margin ------------
y_hi        <- max(ribbon_df$ymax, na.rm = TRUE) * 1.5
y_positions <- 10^seq(log10(y_hi) * 0.97,
                      log10(y_hi) * 0.55,
                      length.out = length(bands))

label_df <- data.frame(
  label_x = date_range[2] + 1,
  label_y = y_positions,
  label   = sapply(bands, `[[`, "label"),
  color   = sapply(bands, `[[`, "color"),
  stringsAsFactors = FALSE
)

# -- halving dates -------------------------------------------
halvings <- as.Date(c("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-19"))

# -- theme ---------------------------------------------------
bg_col   <- "#02233F"
grid_col <- "#274066"
txt_col  <- "white"

theme_btc <- theme_minimal(base_size = 11) +
  theme(
    plot.background   = element_rect(fill = bg_col, color = NA),
    panel.background  = element_rect(fill = bg_col, color = NA),
    panel.grid.major  = element_line(color = grid_col, linewidth = 0.3),
    panel.grid.minor  = element_line(color = grid_col, linewidth = 0.15),
    axis.text         = element_text(color = txt_col),
    axis.title        = element_text(color = txt_col),
    plot.title        = element_text(color = txt_col, face = "bold", size = 14),
    plot.subtitle     = element_text(color = txt_col, size = 10),
    plot.caption      = element_text(color = "#aaaaaa", size = 8, hjust = 1),
    legend.position   = "none",
    plot.margin       = margin(10, 140, 10, 10)
  )

# -- y-axis breaks & labels ----------------------------------
y_breaks <- c(1, 10, 100, 1e3, 1e4, 1e5, 1e6)
y_labels <- c("$1", "$10", "$100", "$1K", "$10K", "$100K", "$1M")

# -- plot ----------------------------------------------------
p <- ggplot()

for (i in seq_along(bands)) {
  rd <- ribbon_df %>% filter(band_id == i)
  p  <- p + geom_ribbon(
    data    = rd,
    mapping = aes(x = date, ymin = ymin, ymax = ymax),
    fill    = bands[[i]]$color,
    alpha   = 0.85
  )
}

halving_df <- data.frame(xintercept = halvings)
p <- p +
  geom_vline(
    data      = halving_df,
    mapping   = aes(xintercept = xintercept),
    color     = "white",
    linetype  = "dashed",
    linewidth = 0.5,
    alpha     = 0.7
  )

p <- p +
  geom_line(
    data    = df,
    mapping = aes(x = date, y = price_usd),
    color   = "white",
    linewidth = 0.7
  )

p <- p +
  geom_text(
    data    = label_df,
    mapping = aes(x = label_x, y = label_y, label = label, color = color),
    hjust   = -0.05,
    size    = 2.6,
    fontface = "bold"
  ) +
  scale_color_identity()

p <- p +
  scale_y_log10(
    breaks = y_breaks,
    labels = y_labels,
    limits = c(0.05, y_hi),
    expand = c(0, 0)
  ) +
  scale_x_date(
    date_breaks = "2 years",
    date_labels = "%Y",
    expand      = c(0, 0)
  ) +
  coord_cartesian(clip = "off") +
  labs(
    title    = "BTC Rainbow Chart",
    subtitle = "Power Law Regression - Halving Cycles",
    x        = NULL,
    y        = "Price (USD)",
    caption  = "Source: CoinMetrics | JHCV"
  ) +
  theme_btc

# -- save ----------------------------------------------------
ggsave(
  filename = out_path,
  plot     = p,
  width    = 11,
  height   = 6.5,
  dpi      = 150,
  bg       = bg_col
)

message("Saved -> ", out_path)
