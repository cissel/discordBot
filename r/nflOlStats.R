#!/usr/bin/env Rscript
# nflOlStats.R
# Advanced OL blocking stats - Pressure Rate vs Sack Rate scatter
# Args: [season] [player_name (optional)]
# Sources: nflreadr load_participation (pass plays, was_pressure) + load_players

suppressPackageStartupMessages({
  library(nflreadr)
  library(dplyr)
  library(ggplot2)
  library(tidyr)
})

args    <- commandArgs(trailingOnly = TRUE)
SEASON  <- if (length(args) >= 1) as.integer(args[1]) else get_latest_season()
PLAYER  <- if (length(args) >= 2) toupper(args[2]) else NA_character_

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl")
OUT_PNG <- file.path(OUT_DIR, "nflOlStats.png")
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

OL_POSITIONS <- c("T", "G", "C", "OT", "OG", "OL", "LT", "RT", "LG", "RG")
MIN_PASS_SNAPS <- 150

message("Loading participation data for ", SEASON, "...")
part <- tryCatch(
  load_participation(seasons = SEASON, include_pbp = TRUE) %>%
    filter(pass == 1, !is.na(was_pressure), !is.na(offense_players)),
  error = function(e) { message("Participation error: ", e$message); NULL }
)
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

single_mode <- !is.na(PLAYER)
target_row  <- NULL
if (single_mode) {
  target_row <- df %>%
    filter(toupper(name) == PLAYER | toupper(sub(".* ", "", name)) == PLAYER)
  if (nrow(target_row) == 0) {
    message(paste0("Player '", PLAYER, "' not found in ", SEASON,
                   " data (min ", MIN_PASS_SNAPS, " pass snaps required)."))
    quit(status = 2)
  }
}

# Both axes inverted: lower pressure rate + lower sack rate = top-right = best
p <- ggplot(df, aes(x = pressure_rate, y = sack_rate)) +
  geom_hline(yintercept = mean(df$sack_rate, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_vline(xintercept = mean(df$pressure_rate, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_point(
    aes(size  = pass_snaps,
        color = sacks),
    alpha = 0.80
  ) +
  scale_color_gradientn(
    colours  = c("#26a69a", "#FFFFFF", "#FF8C00", "#FF4444"),
    name     = "Sacks Allowed",
    na.value = "#888888"
  ) +
  scale_size_continuous(range = c(1.5, 6), name = "Pass Snaps") +
  scale_x_reverse() +
  scale_y_reverse() +
  geom_text(
    aes(label = sub("(\\w)\\.\\s+(.*)", "\\2", name)),
    size  = 2.4,
    color = TXT,
    vjust = -0.7,
    na.rm = TRUE
  )

if (single_mode && !is.null(target_row) && nrow(target_row) > 0) {
  p <- p + geom_point(
    data  = target_row,
    aes(x = pressure_rate, y = sack_rate),
    color = "white", size = 5, shape = 1, stroke = 1.5
  )
}

x_lim <- range(df$pressure_rate, na.rm = TRUE)
y_lim <- range(df$sack_rate, na.rm = TRUE)

p <- p +
  labs(
    title    = paste0(SEASON, " NFL Advanced Blocking - OL"),
    subtitle = paste0("Pressure Rate vs Sack Rate | axes inverted (top-right = elite) | ",
                      "bubble = pass snaps | color = sacks | min ", MIN_PASS_SNAPS, " pass snaps"),
    x        = "Pressure Rate (inverted - left = fewer pressures)",
    y        = "Sack Rate (inverted - top = fewer sacks)",
    caption  = "Source: NFL participation data / nflreadr | JHCV"
  ) +
  dark_theme +
  theme(legend.position = "bottom") +
  annotate("text", x = x_lim[2], y = y_lim[1],
           label = "Elite Blocker", color = GRN, size = 2.6, hjust = 1, fontface = "italic") +
  annotate("text", x = x_lim[1], y = y_lim[2],
           label = "Liability", color = RED, size = 2.6, hjust = 0, fontface = "italic")

ggsave(OUT_PNG, p, width = 9, height = 6.5, dpi = 220, bg = BG)
message("Saved: ", OUT_PNG)
