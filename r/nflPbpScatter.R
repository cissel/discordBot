#!/usr/bin/env Rscript
# nflPbpScatter.R
# Play-by-play "game script" scatter: x = time remaining in the game,
# y = point differential (posteam - defteam), color = play_type (run/pass),
# size = yards_gained, faceted by team. Filters mirror JHCV's original logic
# (down < 4, excludes no_play/FG/PAT/kickoff/kneel/spike plays).
# Args: [season]

suppressPackageStartupMessages({
  library(dplyr)
  library(ggplot2)
  library(nflreadr)
})

args   <- commandArgs(trailingOnly = TRUE)
SEASON <- if (length(args) >= 1) as.integer(args[1]) else get_latest_season()

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl")
OUT_PNG <- file.path(OUT_DIR, "nflPbpScatter.png")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

BG   <- "#02233F"
GRID <- "#274066"
TXT  <- "white"

myTheme <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid        = element_line(color = GRID),
  axis.ticks        = element_line(color = GRID),
  axis.text         = element_text(color = TXT),
  axis.title        = element_text(color = TXT, hjust = 0.5),
  plot.title        = element_text(color = TXT, hjust = 0.5, face = "bold"),
  plot.subtitle     = element_text(color = TXT, hjust = 0.5),
  plot.caption      = element_text(color = TXT, size = 6),
  strip.background  = element_rect(fill = BG, color = GRID),
  strip.text.x      = element_text(color = TXT, size = 7, face = "bold"),
  legend.position    = "right",
  legend.background = element_rect(fill = BG, color = NA),
  legend.text        = element_text(color = TXT),
  legend.title       = element_text(color = TXT, hjust = 0.5)
)

message("Loading play-by-play for ", SEASON, "...")
nfl26 <- tryCatch(
  load_pbp(SEASON),
  error = function(e) { message("load_pbp error: ", e$message); NULL }
)
if (is.null(nfl26) || nrow(nfl26) == 0) {
  message("No pbp data available for ", SEASON, ".")
  quit(status = 1)
}

ptdf <- nfl26 |>

  subset(!is.na(posteam) & !is.na(play_type)) |>

  subset(down < 4) |>

  subset(!play_type %in% c("no_play",
                           "field_goal",
                           "extra_point",
                           "kickoff",
                           "qb_kneel",
                           "qb_spike")) |>

  subset(!is.na(game_seconds_remaining)) |>

  mutate("pd" = posteam_score - defteam_score)

if (nrow(ptdf) == 0) {
  message("No plays resolved for ", SEASON, ".")
  quit(status = 1)
}

ptp <- ggplot(ptdf,
              aes(x = game_seconds_remaining,
                  y = pd,
                  color = as.factor(play_type),
                  size = yards_gained)) +

  geom_vline(aes(xintercept = 1800),  # halftime
             color = "white",
             alpha = .25) +

  geom_hline(aes(yintercept = 0),
             alpha = .25,
             color = "white") +

  geom_point(alpha = .25) +

  facet_wrap(.~posteam,
             ncol = 4) +

  labs(x = "Time Remaining in Game (min:sec)",
       y = "Point Differential",
       title = paste0("NFL ", SEASON),
       color = "Play Type",
       size = "Yards Gained") +

  scale_x_reverse(breaks = seq(0, 3600, 900),
                   labels = c("0:00", "15:00", "30:00", "45:00", "60:00")) +

  scale_y_continuous(breaks = seq(-40, 40, 10)) +

  scale_color_manual(values = c(run = "#E0524C", pass = "#2E7BE0")) +

  scale_size_continuous(limits = c(0, 100)) +

  myTheme

ggsave(OUT_PNG, ptp, width = 18, height = 22, dpi = 200, bg = BG)
message("Saved: ", OUT_PNG)
