#!/usr/bin/env Rscript
# nflOlStats.R
# Advanced OL blocking stats scatter
#   scope=player (default): Pressure Rate vs Sack Rate per OL player
#     Source: nflreadr load_participation (was_pressure) + load_players
#     Participation data is only finalized after a season ends, so with no
#     explicit season this falls back to the prior completed season.
#   scope=team: Sack Rate vs QB Hit Rate per team, live in-season
#     Source: nflreadr load_pbp (sack, qb_hit, qb_dropback) -- updates weekly,
#     so this one *does* default to the current season. qb_hit rate is used
#     as the pressure proxy since official pressure data isn't live.
# Args: [season] [scope: player|team] [filter: player_name|team_abbr (optional)]

suppressPackageStartupMessages({
  library(nflreadr)
  library(dplyr)
  library(ggplot2)
  library(tidyr)
})

args            <- commandArgs(trailingOnly = TRUE)
SEASON_EXPLICIT <- length(args) >= 1 && nzchar(args[1])
SEASON  <- if (SEASON_EXPLICIT) as.integer(args[1]) else get_latest_season()
SCOPE   <- if (length(args) >= 2 && nzchar(args[2])) tolower(args[2]) else "player"
FILTER  <- if (length(args) >= 3 && nzchar(args[3])) toupper(args[3]) else NA_character_
TEAM_MODE <- SCOPE == "team"

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl")
OUT_PNG <- file.path(OUT_DIR, if (TEAM_MODE) "nflOlStatsTeam.png" else "nflOlStats.png")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

BG    <- "#02233F"
GRID  <- "#274066"
TXT   <- "white"
CYAN  <- "#00bfff"
ORG   <- "#FF8C00"
RED   <- "#FF4444"
GRN   <- "#26a69a"

dark_theme <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.3),
  panel.grid.minor  = element_blank(),
  axis.ticks        = element_line(color = GRID),
  axis.text         = element_text(color = TXT, size = 7.5),
  axis.title        = element_text(color = TXT, size = 9),
  plot.title        = element_text(color = TXT, hjust = 0.5, face = "bold", size = 13),
  plot.subtitle     = element_text(color = TXT, hjust = 0.5, size = 9),
  plot.caption      = element_text(color = GRID, size = 7, hjust = 1),
  legend.background = element_rect(fill = BG, color = NA),
  legend.text       = element_text(color = TXT, size = 7.5),
  legend.title      = element_text(color = TXT, size = 8),
  legend.key        = element_rect(fill = BG, color = NA)
)

OL_POSITIONS        <- c("T", "G", "C", "OT", "OG", "OL", "LT", "RT", "LG", "RG")
MIN_PASS_SNAPS       <- 150
MIN_TEAM_DROPBACKS   <- 50

single_mode <- FALSE
target_row  <- NULL

if (TEAM_MODE) {

  # Live play-by-play (sack / qb_hit / qb_dropback) updates weekly in-season,
  # unlike participation data -- so this is safe to default to current season.
  message("Loading play-by-play data for ", SEASON, "...")
  pbp <- tryCatch(
    load_pbp(seasons = SEASON) %>%
      filter(qb_dropback == 1, !is.na(posteam)),
    error = function(e) { message("PBP error: ", e$message); NULL }
  )
  if ((is.null(pbp) || nrow(pbp) == 0) && !SEASON_EXPLICIT) {
    message("No pbp data for ", SEASON, " yet. Falling back to ", SEASON - 1, "...")
    SEASON <- SEASON - 1
    pbp <- tryCatch(
      load_pbp(seasons = SEASON) %>%
        filter(qb_dropback == 1, !is.na(posteam)),
      error = function(e) { message("PBP error: ", e$message); NULL }
    )
  }
  if (is.null(pbp) || nrow(pbp) == 0) {
    message("No play-by-play data available for ", SEASON, ".")
    quit(status = 1)
  }

  message("Aggregating per team...")
  df <- pbp %>%
    group_by(posteam) %>%
    summarise(
      dropbacks = n(),
      sacks     = sum(sack, na.rm = TRUE),
      qb_hits   = sum(qb_hit, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    filter(dropbacks >= MIN_TEAM_DROPBACKS) %>%
    mutate(
      sack_rate = sacks / dropbacks,
      hit_rate  = qb_hits / dropbacks,
      name      = posteam
    )

  if (nrow(df) == 0) {
    message("No teams with >= ", MIN_TEAM_DROPBACKS, " dropbacks found.")
    quit(status = 1)
  }

  message("Teams in chart: ", nrow(df))

  single_mode <- !is.na(FILTER)
  if (single_mode) {
    target_row <- df %>% filter(toupper(name) == FILTER)
    if (nrow(target_row) == 0) {
      message(paste0("Team '", FILTER, "' not found in ", SEASON,
                     " data (min ", MIN_TEAM_DROPBACKS, " dropbacks required)."))
      quit(status = 2)
    }
  }

  df$x_val <- df$hit_rate
  df$y_val <- df$sack_rate
  df$bubble_val <- df$dropbacks
  df$color_val  <- df$sacks

} else {

  message("Loading participation data for ", SEASON, "...")
  part <- tryCatch(
    load_participation(seasons = SEASON, include_pbp = TRUE) %>%
      filter(pass == 1, !is.na(was_pressure), !is.na(offense_players)),
    error = function(e) { message("Participation error: ", e$message); NULL }
  )
  if ((is.null(part) || nrow(part) == 0) && !SEASON_EXPLICIT) {
    message("No participation data for ", SEASON, " yet (data lags live season). Falling back to ", SEASON - 1, "...")
    SEASON <- SEASON - 1
    part <- tryCatch(
      load_participation(seasons = SEASON, include_pbp = TRUE) %>%
        filter(pass == 1, !is.na(was_pressure), !is.na(offense_players)),
      error = function(e) { message("Participation error: ", e$message); NULL }
    )
  }
  if (is.null(part) || nrow(part) == 0) {
    message("No participation data available for ", SEASON, ".")
    quit(status = 1)
  }

  message("Expanding to player-play level...")
  player_plays <- part %>%
    select(nflverse_game_id, play_id, was_pressure, sack, offense_players) %>%
    mutate(player_list = strsplit(offense_players, ";")) %>%
    unnest(player_list) %>%
    rename(player_gsis = player_list)

  message("Loading OL player roster...")
  players <- tryCatch(
    load_players() %>%
      filter(position %in% OL_POSITIONS) %>%
      select(gsis_id, display_name, position),
    error = function(e) { message("Players load error: ", e$message); NULL }
  )
  if (is.null(players) || nrow(players) == 0) {
    message("Could not load player data.")
    quit(status = 1)
  }

  ol_plays <- player_plays %>%
    inner_join(players, by = c("player_gsis" = "gsis_id"))

  message("Aggregating per player...")
  df <- ol_plays %>%
    group_by(player_gsis, display_name, position) %>%
    summarise(
      pass_snaps    = n(),
      pressures     = sum(was_pressure, na.rm = TRUE),
      sacks         = sum(sack, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    filter(pass_snaps >= MIN_PASS_SNAPS) %>%
    mutate(
      pressure_rate = pressures / pass_snaps,
      sack_rate     = sacks / pass_snaps,
      name          = display_name
    )

  if (nrow(df) == 0) {
    message("No OL players with >= ", MIN_PASS_SNAPS, " pass snaps found.")
    quit(status = 1)
  }

  message("OL players in chart: ", nrow(df))

  single_mode <- !is.na(FILTER)
  if (single_mode) {
    target_row <- df %>%
      filter(toupper(name) == FILTER | toupper(sub(".* ", "", name)) == FILTER)
    if (nrow(target_row) == 0) {
      message(paste0("Player '", FILTER, "' not found in ", SEASON,
                     " data (min ", MIN_PASS_SNAPS, " pass snaps required)."))
      quit(status = 2)
    }
  }

  df$x_val <- df$pressure_rate
  df$y_val <- df$sack_rate
  df$bubble_val <- df$pass_snaps
  df$color_val  <- df$sacks
}

if (single_mode && !is.null(target_row)) {
  target_row$x_val <- if (TEAM_MODE) target_row$hit_rate else target_row$pressure_rate
  target_row$y_val <- target_row$sack_rate
}

label_fn <- if (TEAM_MODE) {
  function(n) n
} else {
  function(n) sub("(\\w)\\.\\s+(.*)", "\\2", n)
}

# Both axes inverted: lower x + lower y = top-right = best
p <- ggplot(df, aes(x = x_val, y = y_val)) +
  geom_hline(yintercept = mean(df$y_val, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_vline(xintercept = mean(df$x_val, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_point(
    aes(size  = bubble_val,
        color = color_val),
    alpha = 0.80
  ) +
  scale_color_gradientn(
    colours  = c("#26a69a", "#FFFFFF", "#FF8C00", "#FF4444"),
    name     = "Sacks Allowed",
    na.value = "#888888"
  ) +
  scale_size_continuous(range = if (TEAM_MODE) c(3, 9) else c(1.5, 6),
                         name = if (TEAM_MODE) "Dropbacks" else "Pass Snaps") +
  scale_x_reverse() +
  scale_y_reverse() +
  geom_text(
    aes(label = sapply(name, label_fn)),
    size  = if (TEAM_MODE) 3.4 else 2.4,
    color = TXT,
    vjust = -0.9,
    fontface = if (TEAM_MODE) "bold" else "plain",
    na.rm = TRUE
  )

if (single_mode && !is.null(target_row) && nrow(target_row) > 0) {
  p <- p + geom_point(
    data  = target_row,
    aes(x = x_val, y = y_val),
    color = "white", size = if (TEAM_MODE) 7 else 5, shape = 1, stroke = 1.5
  )
}

x_lim <- range(df$x_val, na.rm = TRUE)
y_lim <- range(df$y_val, na.rm = TRUE)

title_entity <- if (TEAM_MODE) "Team" else "OL"

if (TEAM_MODE) {
  x_axis_label <- "QB Hit Rate (inverted - left = fewer hits allowed)"
  y_axis_label <- "Sack Rate (inverted - top = fewer sacks allowed)"
  chart_subtitle <- paste0("Sack Rate vs QB Hit Rate (top-right = elite) | ",
                           "bubble = dropbacks | color = sacks | min ", MIN_TEAM_DROPBACKS, " dropbacks | live proxy")
  caption_txt <- "Source: NFL play-by-play / nflreadr (sack+hit proxy; official pressure data lags season) | JHCV"
} else {
  x_axis_label <- "Pressure Rate (inverted - left = fewer pressures)"
  y_axis_label <- "Sack Rate (inverted - top = fewer sacks)"
  chart_subtitle <- paste0("Pressure Rate vs Sack Rate | axes inverted (top-right = elite) | ",
                           "bubble = pass snaps | color = sacks | min ", MIN_PASS_SNAPS, " pass snaps")
  caption_txt <- "Source: NFL participation data / nflreadr | JHCV"
}

p <- p +
  labs(
    title    = paste0(SEASON, " NFL Advanced Blocking - ", title_entity),
    subtitle = chart_subtitle,
    x        = x_axis_label,
    y        = y_axis_label,
    caption  = caption_txt
  ) +
  dark_theme +
  theme(legend.position = "bottom") +
  annotate("text", x = x_lim[1], y = y_lim[1],
           label = "Elite Blocker", color = GRN, size = 2.6, hjust = 1, fontface = "italic") +
  annotate("text", x = x_lim[2], y = y_lim[2],
           label = "Liability", color = RED, size = 2.6, hjust = 0, fontface = "italic")

ggsave(OUT_PNG, p, width = 9, height = 6.5, dpi = 220, bg = BG)
message("Saved: ", OUT_PNG)
