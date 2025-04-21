# yieldSpreade.R by JHCV

##### Required Packages #####

library(fredr)
library(dplyr)
library(ggplot2)
library(plotly)
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
                                              hjust = .5))

#####

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))

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

# Short-term maturities (months)
stir <- data.frame(
  label = c("1M", "3M", "6M"),
  name = c("DGS1MO", "DGS3MO", "DGS6MO"),
  stringsAsFactors = FALSE
)

# Long-term maturities (years)
ltir <- data.frame(
  label = c("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"),
  name = paste0("DGS", c(1, 2, 3, 5, 7, 10, 20, 30)),
  stringsAsFactors = FALSE
)

all_series <- rbind(stir, ltir)

# Initialize empty dataframe
all_data <- data.frame()

# Fetch data for each series
for (i in 1:nrow(all_series)) {
  data <- getFredData(all_series$name[i]) |>
    mutate(
      value = value / 100,
      label = all_series$label[i]
    )
  all_data <- rbind(all_data, data)
}

##### Plot #####

ysp <- ggplot(all_data, aes(x = date, y = value, color = label)) +
  geom_line() +
  scale_y_continuous(labels = percent_format(accuracy = 1)) +
  labs(
    title = "Historical U.S. Treasury Yields by Maturity",
    x = "Date",
    y = "Yield",
    color = "Maturity"
  ) +
  myTheme

# ---- Save the plot ----
#ggsave("/Users/jamescissel/discordBot/outputs/markets/yield_spread.png", ysp, width = 8, height = 4.5, dpi = 300)
ggplotly(ysp)
