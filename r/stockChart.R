# stockChart.R by JHCV

##### Required Packages #####

library(tidyverse)
library(dplyr)
library(ggplot2)
library(AlpacaforR)
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

##### Alpaca Markets API Authentication #####

Sys.setenv('APCA-PAPER-KEY' = 'PKB647009CNEAJ8M6X8K')
Sys.setenv('APCA-PAPER-SECRET' = 'XqrG4mAGBneVRGYPVkYxnbr4LdBYRHXGDPXK8Utq')
Sys.setenv('APCA-LIVE-KEY' = 'AKGNBG6FMQEWRBELM45U')
Sys.setenv('APCA-LIVE-SECRET' = '86eND4Pe8NJp4wNoBzkFGrS2PAvHo3UhOy4xAIlL')
#Sys.setenv(POLYGON-KEY = 'POLYGON-KEY')
Sys.setenv('APCA-LIVE' = 'TRUE')
Sys.setenv('APCA-PRO' = 'TRUE')
myAcct <- account()

#####

##### Pull Data #####

args <- commandArgs(trailingOnly = TRUE)
ticker <- args[1]

df <- market_data(ticker, 
                  timeframe = "day", 
                  from = "2016-01-01", 
                  to = today()+1)

df$pct <- 0

for (i in 2:nrow(df)) {
  
  df$pct[i] <- (df$close[i]-df$close[i-1])/df$close[i-1]
  
}

lr <- lm(df$close ~ df$time)

p <- ggplot(df,
            aes(x = time,
                y = close)) +
  
  geom_abline(aes(slope = lr$coefficients[2],
                  intercept = lr$coefficients[1]),
              color = "white") +
  
  geom_abline(aes(slope = lr$coefficients[2],
                  intercept = lr$coefficients[1]+(sd(lr$residuals)*2)),
              color = "red") +
  
  geom_abline(aes(slope = lr$coefficients[2],
                  intercept = lr$coefficients[1]-(sd(lr$residuals)*2)),
              color = "green") +
  
  geom_line(color = "white") +
  
  labs(x = "Time",
       y = "Share Price (USD)",
       subtitle = paste("$",
                        tail(df$close, 1),
                        " (",
                        round(tail(df$pct*100, 1), 2),
                        "%)",
                        sep = ""),
       title = paste("$",
                     ticker,
                     " as of ",
                     max(df$time),
                     sep = ""),
       caption = "JHCV") +
  
  scale_y_continuous(labels = scales::dollar) +
  
  myTheme

ggsave("/Users/jamescissel/discordBot/outputs/markets/stockchart.png",
       p, width = 8, height = 4.5, dpi = 300)

#####