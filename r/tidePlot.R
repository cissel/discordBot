# tidePlot.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(httr)
library(magick)
library(rnoaa)

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

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

##### NOAA API KEY #####

noaaKey <- "GyHGAotydjayGDfwSedFhZhHqcXkLavr"
Sys.setenv("NOAA_KEY"="GyHGAotydjayGDfwSedFhZhHqcXkLavr")

#####

##### NOAA Stations #####

stAugBuoyStation <- "41117"

#####

##### Pull data from Mayport station #####

mayportMeta <- data.frame("location" = c("Bar Pilots Dock"), "id" = c("8720218"))

md <- coops_search(station_name = mayportMeta$id, 
                   begin_date = as.numeric(gsub("-", "", Sys.Date()-1)), 
                   end_date = as.numeric(gsub("-", "", Sys.Date()+1)), 
                   product = "one_minute_water_level", 
                   datum = "stnd",
                   time_zone = "lst")

md$data$t <- as.POSIXct(md$data$t, 
                        tz = "America/New_York")

md <- md$data

names(md) <- c("time", "waterLevel")

md$series <- "observed"

mp <- coops_search(station_name = mayportMeta$id, 
                   begin_date = gsub("-", "", Sys.Date()-1), 
                   end_date = gsub("-", "", Sys.Date()+1), 
                   #end_date = 20230820,
                   product = "predictions", 
                   datum = "stnd",
                   time_zone = "lst")

mp$predictions$t <- as.POSIXct(mp$predictions$t,
                               tz = "America/New_York")

mp <- mp$predictions

names(mp) <- c("time", "waterLevel")

mp$series <- "predicted"

mayport <- rbind(mp, md)

#####

##### Generate plot #####

tidePlot <- ggplot(mayport,
                   aes(x = time,
                       y = waterLevel,
                       color = series,
                       linewidth = series)) +
  
  geom_line(alpha = .75) +
  
  geom_point(data = md,
             aes(x = tail(time, 1),
                 y = tail(waterLevel, 1)),
             color = "white",
             size = 2) +
  
  labs(x = "Time", 
       y = "Water Level", 
       caption = "JHCV",
       subtitle = tail(md$time, 1),
       title = "Mayport Tides") + 
  
  scale_linewidth_manual(values = c("observed" = 1.25,
                                    "predicted" = .5)) +
  
  myTheme

ggsave("/Users/jamescissel/discordBot/outputs/weather/mayportTides.png",
       plot = tidePlot,
       width = 10,
       height = 4,
       dpi = 300,
       bg = "transparent")

#####
