# adsbPlot.R by JHCV

##### Required Packages #####

library(tidyverse)
library(leaflet)

#####

# Example data
df <- read_csv("/Users/jamescissel/discordBot/outputs/aerospace/adsb250nm.csv")

# Use icons() instead of makeIcon()
plane_icon <- makeIcon(
  iconUrl = "/Users/jamescissel/discordBot/outputs/aerospace/plane.png",
  iconWidth = 32, iconHeight = 32,
  iconAnchorX = 16, iconAnchorY = 16  # center the icon
)

# Plot
leaflet(df) |>
  addProviderTiles(providers$CartoDB.Positron) %>%
  #addTiles() |>
  addMarkers(~lon, ~lat, 
             icon = plane_icon,
             label = ~flight)

