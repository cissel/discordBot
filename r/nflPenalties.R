#!/usr/bin/env Rscript
# nflPenalties.R
# Penalty scatter: x = number of penalties, y = yards lost to penalties,
# color = cumulative EPA cost TO the penalized team/player (green = least
# costly, white = neutral, red = most costly).
#   scope=team (default): one dot per team, all 32 shown
#   scope=player: one dot per player (penalty_player_id, disambiguated by
#     team since common surnames collide), min 5 penalties to appear
#
# EPA sign note: nflfastR's `epa` is posteam-centric (positive = good for the
# team with the ball). To get "cost to the penalized side" we flip sign when
# the penalized team is NOT the posteam (i.e. a defensive penalty, where a
# positive posteam epa is bad for the penalized defense).
#
# Data limitation: nflfastR's structured penalty_* columns record only the
# first/primary penalty on a play. Plays with a second enforced penalty
# (offsetting fouls, two fouls on the same play) are ~0.4% of all penalty
# plays in 2025 and are not separately captured -- noted in the caption.
#
# Args: [season] [scope: team|player]

suppressPackageStartupMessages({
  library(nflreadr)
  library(dplyr)
  library(ggplot2)
  library(ggrepel)
})

options(timeout = 300)

args    <- commandArgs(trailingOnly = TRUE)
SEASON  <- if (length(args) >= 1 && nzchar(args[1])) as.integer(args[1]) else get_latest_season()
SCOPE   <- if (length(args) >= 2 && nzchar(args[2])) tolower(args[2]) else "team"
TEAM_MODE <- SCOPE == "team"

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl")
OUT_PNG <- file.path(OUT_DIR, if (TEAM_MODE) "nflPenaltiesTeam.png" else "nflPenaltiesPlayer.png")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

BG   <- "#02233F"
GRID <- "#274066"
TXT  <- "white"

MIN_PLAYER_PENALTIES <- 3

dark_theme <- theme(
  plot.background    = element_rect(fill = BG, color = NA),
  panel.background   = element_rect(fill = BG, color = NA),
  panel.grid.major   = element_line(color = GRID, linewidth = 0.3),
  panel.grid.minor   = element_blank(),
  axis.ticks         = element_line(color = GRID),
  axis.text          = element_text(color = TXT, size = 7.5),
  axis.title         = element_text(color = TXT, size = 9),
  plot.title         = element_text(color = TXT, hjust = 0.5, face = "bold", size = 13),
  plot.subtitle      = element_text(color = TXT, hjust = 0.5, size = 9),
  plot.caption       = element_text(color = GRID, size = 6.5, hjust = 1),
  legend.background  = element_rect(fill = BG, color = NA),
  legend.text        = element_text(color = TXT, size = 7.5),
  legend.title       = element_text(color = TXT, size = 8),
  legend.key         = element_rect(fill = BG, color = NA)
)

message("Loading play-by-play data for ", SEASON, "...")
pbp <- tryCatch(
  load_pbp(seasons = SEASON),
  error = function(e) { message("load_pbp error: ", e$message); NULL }
)
if (is.null(pbp) || nrow(pbp) == 0) {
  message("No pbp data available for ", SEASON, ".")
  quit(status = 1)
}

pen <- pbp %>%
  filter(penalty == 1, !is.na(posteam), !is.na(penalty_team)) %>%
  mutate(
    # EPA cost to the penalized side: flip sign when the penalized team
    # is on defense (not the posteam) so positive = cost/harm either way.
    epa_cost = ifelse(penalty_team == posteam, -epa, epa)
  )

if (nrow(pen) == 0) {
  message("No penalty plays found for ", SEASON, ".")
  quit(status = 1)
}

if (TEAM_MODE) {

  df <- pen %>%
    group_by(penalty_team) %>%
    summarise(
      n_penalties = n(),
      yards_lost  = sum(penalty_yards, na.rm = TRUE),
      epa_cost    = sum(epa_cost, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    mutate(name = penalty_team)

  if (nrow(df) == 0) {
    message("No teams with penalty data for ", SEASON, ".")
    quit(status = 1)
  }

  message("Teams in chart: ", nrow(df))
  label_size <- 3.2
  point_range <- c(3, 3)

} else {

  pen_p <- pen %>% filter(!is.na(penalty_player_id))

  if (nrow(pen_p) == 0) {
    message("No player-attributed penalties found for ", SEASON, ".")
    quit(status = 1)
  }

  df <- pen_p %>%
    group_by(penalty_player_id, penalty_player_name, penalty_team) %>%
    summarise(
      n_penalties = n(),
      yards_lost  = sum(penalty_yards, na.rm = TRUE),
      epa_cost    = sum(epa_cost, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    filter(n_penalties >= MIN_PLAYER_PENALTIES) %>%
    mutate(name = paste0(penalty_player_name, " (", penalty_team, ")"))

  if (nrow(df) == 0) {
    message("No players with >= ", MIN_PLAYER_PENALTIES, " penalties found for ", SEASON, ".")
    quit(status = 1)
  }

  message("Players in chart: ", nrow(df))
  label_size <- 2.4
  point_range <- c(2, 6)
}

color_lim <- max(abs(df$epa_cost), na.rm = TRUE)

# For player scope, n_penalties is a small integer (5-12) so many players
# share the same x value, stacking into unreadable columns. Jitter x
# slightly (deterministic seed) purely for visual separation; axis still
# reflects true integer penalty counts via breaks.
set.seed(42)
df$x_plot <- if (TEAM_MODE) df$n_penalties else df$n_penalties + runif(nrow(df), -0.22, 0.22)

p <- ggplot(df, aes(x = x_plot, y = yards_lost)) +
  geom_point(aes(size = if (TEAM_MODE) NULL else n_penalties, color = epa_cost),
             alpha = 0.9) +
  scale_color_gradient2(
    low      = "#26a69a",
    mid      = "#FFFFFF",
    high     = "#FF4444",
    midpoint = 0,
    limits   = c(-color_lim, color_lim),
    name     = "Cumulative EPA\ncost of penalties"
  ) +
  geom_text_repel(
    aes(label = name),
    size              = label_size,
    color             = TXT,
    fontface          = if (TEAM_MODE) "bold" else "plain",
    na.rm             = TRUE,
    max.overlaps      = if (TEAM_MODE) 20 else 60,
    segment.color     = GRID,
    segment.size      = 0.25,
    box.padding       = 0.3,
    min.segment.length = 0.1,
    seed              = 42
  ) +
  labs(
    title    = paste0(SEASON, " NFL Penalties - ", if (TEAM_MODE) "Team" else "Player"),
    subtitle = paste0("Number of Penalties vs Yards Lost to Penalties | color = cumulative EPA cost ",
                       "(green = least costly, red = most costly)",
                       if (TEAM_MODE) "" else paste0(" | min ", MIN_PLAYER_PENALTIES, " penalties | x jittered for readability")),
    x        = "Number of Penalties",
    y        = "Yards Lost to Penalties",
    caption  = paste0("Source: NFL play-by-play / nflreadr | EPA cost framed from the penalized side's ",
                       "perspective | structured penalty record only -- plays with a 2nd enforced penalty ",
                       "(~0.4% of penalty plays) reflect the primary penalty only | JHCV")
  ) +
  dark_theme +
  theme(legend.position = "right")

if (TEAM_MODE) {
  p <- p + scale_size_identity()
} else {
  p <- p + scale_size_continuous(range = point_range, guide = "none") +
    scale_x_continuous(breaks = sort(unique(df$n_penalties)))
}

chart_w <- if (TEAM_MODE) 9 else 14
chart_h <- if (TEAM_MODE) 7 else 11

ggsave(OUT_PNG, p, width = chart_w, height = chart_h, dpi = 220, bg = BG)
message("Saved: ", OUT_PNG)
