# cryptoChart.R by JHCV

##### Required Packages #####

library(tidyverse)
library(dplyr)
library(ggplot2)
library(ggthemes)
library(scales)
library(patchwork)

#####

##### Plot Appearance Theme #####

myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = "#02233F"),
                 panel.background = element_rect(fill = "#02233F"),
                 panel.grid = element_line(color = "#274066"),
                 axis.ticks = element_line(color = "#274066"),
                 axis.text = element_text(color = "white"),
                 axis.title = element_text(color = "white"),
                 plot.title = element_text(color = "white",
                                           hjust = .5),
                 plot.subtitle = element_text(color = "white",
                                              hjust = .5),
                 plot.caption = element_text(color = "white"),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

#####

##### Pull Data #####

args      <- commandArgs(trailingOnly = TRUE)
symbol    <- toupper(args[1])
timeframe <- if (length(args) >= 2) args[2] else "6mo"

tf_label <- switch(timeframe,
                   intraday = "Last 24 Hours (1-min)",
                   `1w`     = "1 Week",
                   `1mo`    = "1 Month",
                   `3mo`    = "3 Months",
                   `6mo`    = "6 Months",
                   `1y`     = "1 Year",
                   `2y`     = "2 Years",
                   `5y`     = "5 Years",
                   `10y`    = "10 Years",
                   max      = "Max",
                   timeframe
)

bars_csv <- path.expand(paste0("~/discordBot/outputs/markets/", symbol, "_", timeframe, "_bars.csv"))
if (!file.exists(bars_csv)) stop("Bars CSV not found: ", bars_csv)

df <- read_csv(bars_csv, show_col_types = FALSE) %>%
  mutate(time = if (timeframe == "intraday") as.POSIXct(date, tz = "America/New_York") else as.Date(date)) %>%
  arrange(time)

df$pct <- 0
if (nrow(df) >= 2) {
  for (i in 2:nrow(df)) {
    df$pct[i] <- (df$close[i] - df$close[i-1]) / df$close[i-1]
  }
}

df <- df %>% mutate(vol_color = if_else(close >= open, "up", "down"))

# ── price plot ────────────────────────────────────────────────────────────────
p_price <- ggplot(df, aes(x = time, y = close)) +
  geom_line(color = "white") +
  labs(x = NULL,
       y = "Price (USD)",
       subtitle = paste0("$", formatC(tail(df$close, 1), format = "f", digits = 2),
                         " (", round(tail(df$pct * 100, 1), 2), "%)"),
       title = paste0(symbol, "/USD - ", tf_label, " as of ", max(df$time)),
       caption = NULL) +
  scale_y_continuous(labels = scales::dollar) +
  myTheme +
  theme(axis.text.x = element_blank(), axis.ticks.x = element_blank())

# ── volume plot (skipped for intraday - no volume data from Alpaca crypto) ───
if (timeframe != "intraday") {
  vol_max <- max(df$volume, na.rm = TRUE)
  if (vol_max >= 1e6) {
    vol_scale <- 1e6; vol_unit <- "M"
  } else if (vol_max >= 1e3) {
    vol_scale <- 1e3; vol_unit <- "K"
  } else {
    vol_scale <- 1;   vol_unit <- ""
  }

  p_vol <- ggplot(df, aes(x = time, y = volume / vol_scale, fill = vol_color)) +
    geom_bar(stat = "identity", width = 0.8) +
    scale_fill_manual(values = c("up" = "#26a69a", "down" = "#ef5350")) +
    scale_y_continuous(labels = function(x) paste0(x, vol_unit)) +
    labs(x = "Time", y = "Volume",
         caption = "Source: Alpaca Markets | JHCV") +
    myTheme +
    theme(plot.title = element_blank(), plot.subtitle = element_blank())

  combined <- p_price / p_vol + plot_layout(heights = c(7, 3)) &
    theme(plot.background = element_rect(fill = "#02233F", color = NA))
} else {
  p_price <- p_price +
    labs(x = "Time", caption = "Source: Alpaca Markets | JHCV") +
    theme(axis.text.x = element_text(color = "white"), axis.ticks.x = element_line(color = "#274066"))
  combined <- p_price &
    theme(plot.background = element_rect(fill = "#02233F", color = NA))
}

ggsave(path.expand("~/discordBot/outputs/markets/cryptochart.png"),
       combined, width = 8, height = if (timeframe == "intraday") 4.5 else 5.5, dpi = 300)

#####
