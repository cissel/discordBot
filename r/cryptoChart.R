# cryptoChart.R by JHCV

##### Required Packages #####

library(tidyverse)
library(dplyr)
library(ggplot2)
library(ggthemes)
library(scales)

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
                   intraday = "Today (Intraday)",
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
for (i in 2:nrow(df)) {
  df$pct[i] <- (df$close[i] - df$close[i-1]) / df$close[i-1]
}

p <- ggplot(df,
            aes(x = time,
                y = close)) +
  geom_line(color = "white") +
  labs(x = "Time",
       y = "Price (USD)",
       subtitle = paste0("$", formatC(tail(df$close, 1), format = "f", digits = 2),
                         " (", round(tail(df$pct * 100, 1), 2), "%)"),
       title = paste0(symbol, "/USD - ", tf_label, " as of ", max(df$time)),
       caption = "Source: Alpaca Markets | JHCV") +
  scale_y_continuous(labels = scales::dollar) +
  myTheme

ggsave(path.expand("~/discordBot/outputs/markets/cryptochart.png"),
       p, width = 8, height = 4.5, dpi = 300)

#####