# fedTarget.R by JHCV

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

#####

##### Pull Data #####

df <- getFredData("DFEDTARU")

#####

##### Create Plot #####

p <- ggplot(df,
            aes(x = date,
                y = value/100)) +
  
  geom_line(color = "white") +
  
  geom_hline(yintercept = tail(df$value/100, 1),
             color = "white") +
  
  scale_y_continuous(labels = scales::percent_format(accuracy = 1)) +
  labs(title = paste("Federal Funds Target Range as of ",
                     tail(df$date, 1),
                     ": ",
                     tail(df$value, 1),
                     "%",
                     sep = ""),
       x = "Date",
       y = "Yield") +
  myTheme

#####

##### Save plot #####

ggsave("/Users/jamescissel/discordbot/outputs/markets/dfedtaru.png",
       p,
       width = 8, 
       height = 4.5, 
       dpi = 300)

#ggplotly(p)

#####