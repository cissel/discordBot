# buoyWavePlot.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)

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
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

#####

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

##### Pull data #####

df <- read_csv("/Users/jamescissel/discordBot/outputs/weather/buoy41117.csv")
df$WVHT <- df$WVHT*3.28084
df$dt <- as.POSIXct(df$dt, format = "%Y-%m-%d %H:%M", tz = "UTC")

#####

##### Generate plot #####

p <- ggplot(df,
            aes(x = dt,
                y = WVHT)) +
  
  geom_line(color = "cyan") +
  
  geom_hline(yintercept = df$WVHT[which.max(df$dt)],
             color = "cyan") +
  
  labs(x = "Time",
       y = "Wave Height (ft)",
       title = paste("Lastest Observations from NOAA NDBC Buoy #41117 as of ",
                     head(df$dt, 1),
                     ": "
                     head(df$WVHT, 1),
                     sep = "")) +
  
  myTheme

ggsave("/Users/jamescissel/discordBot/outputs/weather/buoyWaves.png",
       plot = p,
       width = 10,
       height = 4,
       dpi = 300,
       bg = "transparent")

#####