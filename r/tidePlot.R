# tidePlot.R by JHCV
# Pulls Mayport tide data directly from NOAA CO-OPS API (no rnoaa dependency)

suppressPackageStartupMessages({
  library(tidyverse)
  library(httr)
  library(jsonlite)
})

# ── theme ─────────────────────────────────────────────────────────────────────

myTheme <- theme(
  legend.position   = "none",
  plot.background   = element_rect(fill = "#02233F"),
  panel.background  = element_rect(fill = "#02233F"),
  panel.grid        = element_line(color = "#274066"),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white"),
  axis.title        = element_text(color = "white"),
  plot.title        = element_text(color = "white", hjust = .5),
  plot.subtitle     = element_text(color = "white", hjust = .5),
  plot.caption      = element_text(color = "white"),
  strip.background  = element_rect(fill = "#02233F"),
  strip.text        = element_text(color = "white")
)

# ── NOAA CO-OPS API ───────────────────────────────────────────────────────────

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

# observed water level
cat("Fetching observed water level...\n")
obs_raw <- fetch_coops("one_minute_water_level")
obs <- obs_raw$data |>
  transmute(
    time       = as.POSIXct(t, format = "%Y-%m-%d %H:%M", tz = "America/New_York"),
    waterLevel = as.numeric(v),
    series     = "observed"
  ) |>
  filter(!is.na(waterLevel))

# predicted tides
cat("Fetching predicted tides...\n")
pred_raw <- fetch_coops("predictions")
pred <- pred_raw$predictions |>
  transmute(
    time       = as.POSIXct(t, format = "%Y-%m-%d %H:%M", tz = "America/New_York"),
    waterLevel = as.numeric(v),
    series     = "predicted"
  ) |>
  filter(!is.na(waterLevel))

mayport <- rbind(pred, obs)

# ── plot ──────────────────────────────────────────────────────────────────────

last_obs_time  <- tail(obs$time, 1)
last_obs_level <- tail(obs$waterLevel, 1)

tidePlot <- ggplot(mayport,
                   aes(x = time,
                       y = waterLevel,
                       color = series,
                       linewidth = series)) +

  geom_line(alpha = .75) +

  geom_point(data = tail(obs, 1),
             aes(x = time, y = waterLevel),
             color = "white",
             size = 2,
             inherit.aes = FALSE) +

  labs(x        = "Time",
       y        = "Water Level (ft, STND)",
       caption  = "Source: NOAA CO-OPS | Mayport Bar Pilots Dock",
       subtitle = format(last_obs_time, "%Y-%m-%d %H:%M %Z"),
       title    = "Mayport Tides") +

  scale_color_manual(values = c("observed" = "#00FFFF",
                                "predicted" = "white")) +

  scale_linewidth_manual(values = c("observed" = 1.25,
                                    "predicted" = 0.5)) +

  myTheme

out_path <- path.expand("~/discordBot/outputs/weather/mayportTides.png")
dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

ggsave(out_path,
       plot   = tidePlot,
       width  = 10,
       height = 4,
       dpi    = 150,
       bg     = "#02233F")

cat(sprintf("Saved: %s\n", out_path))
