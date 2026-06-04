# tradeChart.R by JHCV
##### Required Packages #####
library(tidyverse)
library(ggplot2)
library(ggthemes)
library(scales)
#####

##### Plot Appearance Theme #####
myTheme <- theme(
  legend.position = "none",
  plot.background = element_rect(fill = "#02233F"),
  panel.background = element_rect(fill = "#02233F"),
  panel.grid = element_line(color = "#274066"),
  axis.ticks = element_line(color = "#274066"),
  axis.text = element_text(color = "white"),
  axis.title = element_text(color = "white"),
  plot.title = element_text(color = "white", hjust = .5),
  plot.subtitle = element_text(color = "white", hjust = .5),
  plot.caption = element_text(color = "white"),
  strip.background = element_rect(fill = "#02233F"),
  strip.text = element_text(color = "white")
)
#####

##### Args #####
args      <- commandArgs(trailingOnly = TRUE)
ticker    <- args[1]
timeframe <- tolower(args[2])  # recent, close, open, day, full

# Human-readable label for the timeframe
timeframe_label <- switch(timeframe,
                          recent = "Last Hour of Trading",
                          close  = "Market Close (3:00 – 4:00 PM ET)",
                          open   = "Market Open (9:30 – 10:30 AM ET)",
                          day    = "Regular Session (9:30 AM – 4:00 PM ET)",
                          full   = "Full Session (4:00 AM – 8:00 PM ET)",
                          timeframe  # fallback: just show the raw value
)

csv_path <- paste0("~/discordBot/outputs/markets/", ticker, "_trades.csv")

if (!file.exists(csv_path)) {
  stop(paste("CSV not found:", csv_path))
}
#####

##### Read Data #####
df <- read.csv(csv_path, stringsAsFactors = FALSE)

df$time <- as.POSIXct(df$time, format = "%Y-%m-%d %H:%M:%S", tz = "America/New_York")

df <- df[!is.na(df$time) & !is.na(df$price), ]

if (nrow(df) == 0) {
  stop("No valid rows in CSV after cleaning.")
}
#####

##### Derived Values #####
last_price <- tail(df$price, 1)
last_pct   <- tail(df$pct_change, 1)
last_time  <- max(df$time)

pct_color      <- ifelse(last_pct >= 0, "#00C853", "#FF1744")
subtitle_text  <- paste0(
  "$", formatC(last_price, format = "f", digits = 2),
  " (", round(last_pct, 2), "%)"
)
title_text <- paste0(
  "$", ticker, " - ", timeframe_label,
  "\nas of ", format(last_time, "%b %d %Y %I:%M %p %Z")
)
#####

##### Plot #####
p <- ggplot(df,
            aes(x     = time,
                y     = price,
                size  = size,
                color = as.factor(exchange))) +
  
  geom_point(alpha = 0.6) +
  
  labs(
    x        = "Time",
    y        = "Share Price (USD)",
    title    = title_text,
    subtitle = subtitle_text
  ) +
  
  scale_y_continuous(labels = scales::dollar) +
  scale_x_datetime(date_labels = "%I:%M %p", date_breaks = "30 min") +
  
  myTheme +
  theme(axis.text.x = element_text(angle = 30, hjust = 1))

ggsave(
  "~/discordBot/outputs/markets/tradechart.png",
  p,
  width  = 8,
  height = 4.5,
  dpi    = 300
)

cat("Chart saved.\n")
#####