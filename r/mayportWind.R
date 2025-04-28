# windPlot.R by JHCV

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

##### Pull Data #####

mayportMeta <- data.frame("location" = c("Bar Pilots Dock"), "id" = c("8720218"))

mw <- coops_search(station_name = mayportMeta$id, 
                   begin_date = as.numeric(gsub("-", "", Sys.Date()-1)), 
                   end_date = as.numeric(gsub("-", "", Sys.Date()+1)), 
                   product = "wind", 
                   datum = "stnd",
                   time_zone = "lst")$data |>
  
  mutate(spd = s*1.15078,
         gusts = as.numeric(g)*1.15078)

mws <- mw |>
  
  select(t, spd, d, dr)

mws$type <- "sustained"

mwg <- mw |>
  
  select(t, gusts, d, dr)

mwg$type <- "gusts"

# Rename columns to be consistent for both dataframes
mws <- mws %>%
  rename(windSpd = spd)

mwg <- mwg %>%
  rename(windSpd = gusts)

# Combine the two dataframes vertically
mayportWinds <- bind_rows(mws, mwg)

# View the combined dataframe
#head(mayportWinds)

mayportWindPlot <- ggplot(mayportWinds, 
                          aes(x = t,
                              y = windSpd,
                              color = type)) +
  
  geom_line() +
  
  labs(x = "Time",
       y = "Wind Speed (mph)",
       color = "Type",
       title = "Mayport Winds") +
  
  myTheme +
  myLegend

ggsave("/Users/jamescissel/discordBot/outputs/weather/mayportWinds.png",
       plot = mayportWindPlot,
       width = 10,
       height = 4,
       dpi = 300,
       bg = "transparent")

#####