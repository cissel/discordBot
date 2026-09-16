#!/usr/bin/env Rscript
# nflRunRadars.R
# NFL "run radar" / sonar charts: polar bar charts showing run-direction tendency
# (angle) and volume (bar length) per team, colored by avg yards gained
# (red = worst, white = avg, green = best) for that direction across the league.
# Ported from foosball/runRadars.Rmd (inspiration: statsbymichaellopez's NFL
# tracking Voronoi/sonar Kaggle notebook) into the bot's navy dark theme.
# Args: [season]

suppressPackageStartupMessages({
  library(dplyr)
  library(ggplot2)
  library(nflreadr)
})

args   <- commandArgs(trailingOnly = TRUE)
SEASON <- if (length(args) >= 1) as.integer(args[1]) else get_latest_season()

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl")
OUT_PNG <- file.path(OUT_DIR, "nflRunRadars.png")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

BG   <- "#02233F"
GRID <- "#274066"
TXT  <- "white"

dark_theme <- theme(
  legend.position   = "none",
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid        = element_line(color = GRID),
  axis.ticks        = element_line(color = GRID),
  axis.text         = element_text(color = TXT),
  axis.title        = element_text(color = TXT, hjust = 0.5),
  plot.title        = element_text(color = TXT, hjust = 0.5, face = "bold"),
  plot.subtitle     = element_text(color = TXT, hjust = 0.5),
  plot.caption      = element_text(color = TXT, size = 5),
  strip.background  = element_rect(fill = BG, color = GRID),
  strip.text.x      = element_text(color = TXT, size = 8.5, face = "bold")
)

message("Loading play-by-play for ", SEASON, "...")
nfl <- tryCatch(
  load_pbp(SEASON) %>% subset(season_type == "REG"),
  error = function(e) { message("load_pbp error: ", e$message); NULL }
)
if (is.null(nfl) || nrow(nfl) == 0) {
  message("No pbp data available for ", SEASON, ".")
  quit(status = 1)
}

runs <- nfl %>%
  subset(play_type == "run") %>%
  mutate(runDir = as.factor(paste(
    ifelse(run_location == "middle", "center", run_location),
    ifelse(run_location != "middle", run_gap, ""),
    sep = " "
  )))

# run-direction -> position number around the circle
rgp <- data.frame(
  runDir = c("left end", "left tackle", "left guard",
             "center",
             "right guard", "right tackle", "right end"),
  rdn = 1:7
)

rdf <- runs %>%
  subset(runDir != "NA NA") %>%
  group_by(runDir, posteam) %>%
  summarize(
    nRuns      = n(),
    mean_yards = mean(yards_gained),
    tot_yards  = sum(yards_gained),
    mean_epa   = mean(epa),
    tuddies    = sum(touchdown),
    .groups = "drop"
  )

if (nrow(rdf) == 0) {
  message("No run-direction data resolved for ", SEASON, ".")
  quit(status = 1)
}

rdf$rdn <- 0
for (i in seq_len(nrow(rdf))) {
  match_j <- which(rgp$runDir == rdf$runDir[i])
  if (length(match_j) > 0) rdf$rdn[i] <- rgp$rdn[match_j[1]]
}
rdf$rdn[rdf$rdn == 0] <- 4  # "middle" fallback, mirrors runRadars.Rmd quirk

nAngles <- 202.5 / (7 + 2)
rdf <- rdf %>% mutate(runAngle = rdn * nAngles)

colfunc <- colorRampPalette(c("#FF4444", "white", "#26a69a")) # red -> white -> green

# rank/fill WITHIN each team so every team's radar has its own worst->best scale.
# yardRank is a 0-1 relative rank per team (0 = team's worst direction,
# 1 = team's best), used to drive both the per-cell color AND a shared legend.
rcdf <- rdf %>%
  group_by(posteam) %>%
  arrange(mean_yards, .by_group = TRUE) %>%
  mutate(
    fill     = colfunc(n()),
    yardRank = (row_number() - 1) / pmax(n() - 1, 1)
  ) %>%
  ungroup()

label <- if (length(args) >= 1) SEASON else paste0(SEASON, " (latest)")

nrp <- ggplot(rcdf, aes(x = runAngle, y = nRuns)) +
  geom_col(aes(fill = yardRank)) +
  labs(
    x        = "Run Direction",
    y        = "Number of Runs",
    subtitle = "Bar length = run volume | color = avg yards gained, ranked within each team",
    title    = paste0(SEASON, " NFL Run Radars"),
    caption  = "Source: nflreadr/nflfastR | JHCV"
  ) +
  scale_color_identity() +
  scale_fill_gradientn(
    colours = c("#FF4444", "white", "#26a69a"),
    limits  = c(0, 1),
    breaks  = c(0, 0.5, 1),
    labels  = c("Worst", "Avg", "Best"),
    name    = "Avg Yards\n(per team)"
  ) +
  dark_theme +
  theme(legend.position = "right") +
  coord_polar(start = (pi / -2), direction = 1) +
  scale_x_continuous(limits = c(0, 360),
                      breaks = rcdf$runAngle,
                      labels = rcdf$runDir) +
  facet_wrap(~posteam, nrow = 4) +
  theme(axis.text.x = element_text(size = 5),
        legend.background = element_rect(fill = BG, color = NA),
        legend.text  = element_text(color = TXT),
        legend.title = element_text(color = TXT, hjust = 0.5))

ggsave(OUT_PNG, nrp, width = 16, height = 10, dpi = 200, bg = BG)
message("Saved: ", OUT_PNG)
