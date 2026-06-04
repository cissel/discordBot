# yieldSpread.R by JHCV
# Usage: Rscript yieldSpread.R [days]
#   days = number of days to look back (default: all history)
#   e.g. Rscript yieldSpread.R 60   -> last 60 days
#        Rscript yieldSpread.R       -> full history

##### Required Packages #####

library(fredr)
library(dplyr)
library(ggplot2)
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
                 plot.title = element_text(color = "white", hjust = .5),
                 plot.subtitle = element_text(color = "white", hjust = .5),
                 plot.caption = element_text(color = "white"),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))

#####

##### CLI argument - days to look back #####

args <- commandArgs(trailingOnly = TRUE)
days <- if (length(args) > 0 && !is.na(suppressWarnings(as.integer(args[1])))) {
  as.integer(args[1])
} else {
  NULL  # NULL = full history
}

#####

##### Federal Reserve API Authentication #####

fredr_set_key("d47e2b30bf4826314df23a57408a56a6")

#####

##### Functions #####

getFredData <- function(series = "UNRATE") {
  fredr_series_observations(series) |>
    select(date, value)
}

##### Fetch Treasury Data #####

stir <- data.frame(
  label = c("1M", "3M", "6M"),
  name  = c("DGS1MO", "DGS3MO", "DGS6MO"),
  stringsAsFactors = FALSE
)

ltir <- data.frame(
  label = c("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"),
  name  = paste0("DGS", c(1, 2, 3, 5, 7, 10, 20, 30)),
  stringsAsFactors = FALSE
)

all_series <- rbind(stir, ltir)
all_data   <- data.frame()

for (i in 1:nrow(all_series)) {
  data <- getFredData(all_series$name[i]) |>
    mutate(value = value / 100, label = all_series$label[i])
  all_data <- rbind(all_data, data)
}

##### Apply timeframe filter #####

plot_data <- if (!is.null(days)) {
  all_data |> subset(date >= Sys.Date() - days)
} else {
  all_data
}

maturity_order    <- c("1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y")
plot_data$label   <- factor(plot_data$label, levels = maturity_order)

##### Subtitle & output path #####

subtitle_date <- max(plot_data$date)

if (!is.null(days)) {
  timeframe_label <- paste0("Last ", days, " Days")
  out_path        <- "~/discordBot/outputs/markets/yield_spread.png"
} else {
  timeframe_label <- "Full History"
  out_path        <- "~/discordBot/outputs/markets/yield_spread.png"
}

##### Plot #####

ysp <- ggplot(plot_data, aes(x = date, y = value, color = label)) +
  geom_line() +
  scale_y_continuous(labels = scales::percent_format(accuracy = 1)) +
  labs(
    title    = paste0("U.S. Treasury Yields by Maturity - ", timeframe_label),
    subtitle = subtitle_date,
    x        = "Date",
    y        = "Yield",
    color    = "Maturity",
    caption  = "JHCV"
  ) +
  myTheme + myLegend

ggsave(out_path, ysp, width = 8, height = 4.5, dpi = 300)
