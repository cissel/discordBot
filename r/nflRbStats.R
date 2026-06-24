#!/usr/bin/env Rscript
# nflRbStats.R
# Advanced RB rushing stats - Rush Yards Over Expected scatter
# Args: [season] [player_name (optional)]
# Sources: NFL Next Gen Stats (rushing) + nflreadr player_stats

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
OUT_PNG <- file.path(OUT_DIR, "nflRbStats.png")
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

message("Loading NGS rushing data for ", SEASON, "...")
ngs <- tryCatch(
  load_nextgen_stats(seasons = SEASON, stat_type = "rushing"),
  error = function(e) { message("NGS error: ", e$message); NULL }
)
if (is.null(ngs) || nrow(ngs) == 0) {
  message("No NGS rushing data available.")
  quit(status = 1)
}

message("Loading player stats for ", SEASON, "...")
pstats <- tryCatch(
  load_player_stats(seasons = SEASON) %>%
    filter(position == "RB") %>%
    group_by(player_id, player_display_name) %>%
    summarise(
      carries       = sum(carries, na.rm = TRUE),
      rush_yards    = sum(rushing_yards, na.rm = TRUE),
      rush_tds      = sum(rushing_tds, na.rm = TRUE),
      rush_first_dn = sum(rushing_first_downs, na.rm = TRUE),
      rush_epa      = sum(rushing_epa, na.rm = TRUE),
      .groups = "drop"
    ),
  error = function(e) { message("player_stats error: ", e$message); NULL }
)

# Season-aggregate NGS rushing
ngs_szn <- ngs %>%
  filter(season_type == "REG", player_position == "RB") %>%
  group_by(player_gsis_id, player_display_name, player_position) %>%
  summarise(
    att             = sum(rush_attempts, na.rm = TRUE),
    rush_yds_oe     = sum(rush_yards_over_expected, na.rm = TRUE),
    ryoe_per_att    = mean(rush_yards_over_expected_per_att, na.rm = TRUE),
    rush_pct_oe     = mean(rush_pct_over_expected, na.rm = TRUE),
    efficiency      = mean(efficiency, na.rm = TRUE),
    avg_tlos        = mean(avg_time_to_los, na.rm = TRUE),
    pct_8box        = mean(percent_attempts_gte_eight_defenders, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(att >= 50)

# Join player stats
if (!is.null(pstats)) {
  df <- ngs_szn %>%
    left_join(pstats, by = c("player_gsis_id" = "player_id"))
  df <- df %>%
    mutate(name = dplyr::coalesce(player_display_name.x, player_display_name.y))
} else {
  df <- ngs_szn %>% rename(name = player_display_name)
}

df <- df %>%
  filter(!is.na(name), !is.na(ryoe_per_att), !is.na(efficiency))

single_mode <- !is.na(PLAYER)
if (single_mode) {
  target_row <- df %>%
    filter(toupper(name) == PLAYER | toupper(sub(".* ", "", name)) == PLAYER)
  if (nrow(target_row) == 0) {
    message(paste0("Player '", PLAYER, "' not found in ", SEASON, " data (min 50 carries required)."))
    quit(status = 2)
  }
}

p <- ggplot(df, aes(x = efficiency, y = ryoe_per_att)) +
  geom_hline(yintercept = 0, color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_vline(xintercept = mean(df$efficiency, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_point(
    aes(size  = att,
        color = ryoe_per_att),
    alpha = 0.75
  ) +
  scale_color_gradientn(
    colours  = c("#FF4444", "#FF8C00", "#FFFFFF", "#26a69a"),
    name     = "RYOE/att",
    na.value = "#888888"
  ) +
  scale_size_continuous(range = c(1.5, 5.5), name = "Carries") +
  geom_text(
    aes(label = sub("(\\w)\\.\\s+(.*)", "\\2", name)),
    size  = 2.4,
    color = TXT,
    vjust = -0.7,
    na.rm = TRUE
  )

if (single_mode && nrow(target_row) > 0) {
  p <- p + geom_point(
    data  = target_row,
    aes(x = efficiency, y = ryoe_per_att),
    color = "white", size = 5, shape = 1, stroke = 1.5
  )
}

p <- p +
  labs(
    title    = paste0(SEASON, " NFL Advanced Rushing - RB"),
    subtitle = "NGS Efficiency vs Rush Yards Over Expected per Carry | bubble size = carries | min 50 att",
    x        = "NGS Efficiency Score",
    y        = "Rush Yards Over Expected per Carry",
    caption  = "Source: NFL Next Gen Stats / nflreadr | JHCV"
  ) +
  dark_theme +
  theme(legend.position = "bottom")

x_lim <- range(df$efficiency, na.rm = TRUE)
y_lim <- range(df$ryoe_per_att, na.rm = TRUE)

p <- p +
  annotate("text", x = x_lim[1] + 0.02, y = y_lim[2] - 0.05,
           label = "Efficient + Creates Yards", color = GRN, size = 2.6, hjust = 0, fontface = "italic") +
  annotate("text", x = x_lim[2] - 0.02, y = y_lim[1] + 0.05,
           label = "Grinding but loses YOE", color = RED, size = 2.6, hjust = 1, fontface = "italic")

ggsave(OUT_PNG, p, width = 9, height = 6.5, dpi = 220, bg = BG)
message("Saved: ", OUT_PNG)
