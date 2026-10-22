# mayportWind.R by JHCV
# Pulls Mayport wind data directly from NOAA CO-OPS API (no rnoaa dependency
# -- rnoaa was archived off CRAN and this script was broken until this fix
# rewrote it the same way tidePlot.R already moved off rnoaa).

suppressPackageStartupMessages({
  library(tidyverse)
  library(httr)
  library(jsonlite)
})

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
                  legend.title = element_text(color = "white"))

#####

##### NOAA CO-OPS API #####

STATION  <- "8720218"   # Mayport Bar Pilots Dock
BASE_URL <- "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

begin_date <- format(Sys.Date() - 1, "%Y%m%d")
end_date   <- format(Sys.Date() + 1, "%Y%m%d")

fetch_coops <- function(product) {
  resp <- GET(BASE_URL, query = list(
    station    = STATION,
    begin_date = begin_date,
    end_date   = end_date,
    product    = product,
    datum      = "STND",
    time_zone  = "lst_ldt",
    units      = "english",
    format     = "json",
    application = "discordbot"
  ))

  if (status_code(resp) != 200) {
    stop(sprintf("NOAA API error %d for product '%s'", status_code(resp), product))
  }

  body <- content(resp, as = "text", encoding = "UTF-8")
  parsed <- fromJSON(body)

  if (!is.null(parsed$error)) {
    stop(sprintf("NOAA API returned error: %s", parsed$error$message))
  }

  parsed
}

#####

##### Pull Data #####

cat("Fetching wind observations...\n")
mw_raw <- fetch_coops("wind")
mw <- mw_raw$data |>
  transmute(
    t    = as.POSIXct(t, format = "%Y-%m-%d %H:%M", tz = "America/New_York"),
    spd  = as.numeric(s),     # units=english -> mph
    gusts = as.numeric(g),    # units=english -> mph
    d    = as.numeric(d),
    dr   = dr
  ) |>
  filter(!is.na(spd))

mws <- mw |>
  select(t, spd, d, dr) |>
  rename(windSpd = spd)
mws$type <- "Sustained"

mwg <- mw |>
  select(t, gusts, d, dr) |>
  rename(windSpd = gusts)
mwg$type <- "Gusts"

# Combine the two dataframes vertically
mayportWinds <- bind_rows(mws, mwg)

#####

##### Plot #####

mayportWindPlot <- ggplot(mayportWinds,
                          aes(x = t,
                              y = windSpd,
                              color = type)) +

  geom_line() +

  labs(x = "Time",
       y = "Wind Speed (mph)",
       color = "Type",
       caption = "Source: NOAA CO-OPS | Mayport Bar Pilots Dock | JHCV",
       subtitle = paste(tail(mayportWinds$dr, 1),
                        " @ ",
                        round(tail(mwg$windSpd, 1), 2),
                        "mph",
                        sep = ""),
       title = paste("Mayport Winds as of: ",
                     tail(mayportWinds$t, 1),
                     sep = "")) +

  myTheme +
  myLegend

out_path <- path.expand("~/discordBot/outputs/weather/mayportWinds.png")
dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

ggsave(out_path,
       plot = mayportWindPlot,
       width = 10,
       height = 4,
       dpi = 300,
       bg = "transparent")

cat(sprintf("Saved: %s\n", out_path))

#####
