# crude.R by JHCV

##### Required Packages #####

library(tidyverse)
library(dplyr)
library(ggplot2)
library(fredr)
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

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))

#####

##### Federal Reserve API Authentication #####

fredr_set_key("d47e2b30bf4826314df23a57408a56a6")

#####

##### Functions #####

getFredData <- function(series = "DCOILWTICO") {
  fredr_series_observations(series) |>
    select(date, value)
}

#####

##### Pull Data #####

df <- getFredData()

#####

##### Create Plot #####

p <- ggplot(df,
            aes(x = date,
                y = value)) +
  
  geom_line(color = "white") +
  
  geom_hline(yintercept = tail(df$value, 1),
             color = "white") +
  
  geom_vline(xintercept = as_date("01-09-11"),
             color = "white",
             alpha = .5) +
  
  scale_y_continuous(labels = scales::dollar) +
  labs(title = paste("Crude Oil - West Texas Intermediate (WTI) - Cushing, Oklahoma as of ",
                     tail(df$date, 1),
                     ": ",
                     sep = ""),
       subtitle = paste("$",
                        tail(df$value, 1),
                        sep = ""),
       x = "Date",
       y = "Price per Barrel (USD)",
       caption = "JHCV") +
  myTheme

#####

##### Save plot #####

ggsave("/Users/jamescissel/discordbot/outputs/markets/crudewti.png",
       p,
       width = 8, 
       height = 4.5, 
       dpi = 300)

#ggplotly(p)

#####