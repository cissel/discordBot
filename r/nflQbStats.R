#!/usr/bin/env Rscript
# nflQbStats.R
# Advanced QB passing stats - CPOE vs Aggressiveness scatter
# Args: [season] [player_name (optional)]
# Sources: NFL Next Gen Stats (passing) + nflreadr player_stats + PFR advstats

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
OUT_PNG <- file.path(OUT_DIR, "nflQbStats.png")
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

message("Loading NGS passing data for ", SEASON, "...")
ngs <- tryCatch(
  load_nextgen_stats(seasons = SEASON, stat_type = "passing"),
  error = function(e) { message("NGS error: ", e$message); NULL }
)
if (is.null(ngs) || nrow(ngs) == 0) {
  message("No NGS passing data available.")
  quit(status = 1)
}

message("Loading player stats for ", SEASON, "...")
pstats <- tryCatch(
  load_player_stats(seasons = SEASON) %>%
    filter(position == "QB") %>%
    group_by(player_id, player_display_name) %>%
    summarise(
      attempts      = sum(attempts, na.rm = TRUE),
      completions   = sum(completions, na.rm = TRUE),
      pass_yards    = sum(passing_yards, na.rm = TRUE),
      pass_tds      = sum(passing_tds, na.rm = TRUE),
      interceptions = sum(passing_interceptions, na.rm = TRUE),
      pass_epa      = sum(passing_epa, na.rm = TRUE),
      cpoe          = mean(passing_cpoe, na.rm = TRUE),
      .groups = "drop"
    ),
  error = function(e) { message("player_stats error: ", e$message); NULL }
)

message("Loading PFR advstats (pass) for ", SEASON, "...")
pfr <- tryCatch(
  load_pfr_advstats(seasons = SEASON, stat_type = "pass") %>%
    group_by(pfr_player_name) %>%
    summarise(
      bad_throw_pct   = mean(passing_bad_throw_pct, na.rm = TRUE),
      drop_pct        = mean(passing_drop_pct, na.rm = TRUE),
      times_pressured = sum(times_pressured, na.rm = TRUE),
      times_blitzed   = sum(times_blitzed, na.rm = TRUE),
      times_hurried   = sum(times_hurried, na.rm = TRUE),
      times_hit       = sum(times_hit, na.rm = TRUE),
      .groups = "drop"
    ),
  error = function(e) { message("PFR error: ", e$message); NULL }
)

# Season-aggregate NGS passing
ngs_szn <- ngs %>%
  filter(season_type == "REG", player_position == "QB") %>%
  group_by(player_gsis_id) %>%
  summarise(
    player_display_name = dplyr::first(player_display_name),
    player_position     = dplyr::first(player_position),
    att             = sum(attempts, na.rm = TRUE),
    cpoe_ngs        = mean(completion_percentage_above_expectation, na.rm = TRUE),
    aggressiveness  = mean(aggressiveness, na.rm = TRUE),
    avg_ttt         = mean(avg_time_to_throw, na.rm = TRUE),
    avg_air_sticks  = mean(avg_air_yards_to_sticks, na.rm = TRUE),
    avg_cay         = mean(avg_completed_air_yards, na.rm = TRUE),
    avg_iay         = mean(avg_intended_air_yards, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(att >= 100)

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
  filter(!is.na(name), !is.na(cpoe_ngs), !is.na(aggressiveness))

single_mode <- !is.na(PLAYER)
if (single_mode) {
  target_row <- df %>%
    filter(toupper(name) == PLAYER | toupper(sub(".* ", "", name)) == PLAYER)
  if (nrow(target_row) == 0) {
    message(paste0("Player '", PLAYER, "' not found in ", SEASON, " data (min 100 attempts required)."))
    quit(status = 2)
  }
}

# Use cpoe_ngs as primary y axis, aggressiveness as x
p <- ggplot(df, aes(x = aggressiveness, y = cpoe_ngs)) +
  geom_hline(yintercept = 0, color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_vline(xintercept = mean(df$aggressiveness, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_point(
    aes(size  = att,
        color = pass_epa),
    alpha = 0.8
  ) +
  scale_color_gradientn(
    colours = c("#FF4444", "#FF8C00", "#FFFFFF", "#26a69a"),
    name    = "Pass EPA",
    na.value = "#888888"
  ) +
  scale_size_continuous(range = c(2, 6), name = "Attempts") +
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
    aes(x = aggressiveness, y = cpoe_ngs),
    color = "white", size = 5, shape = 1, stroke = 1.5
  )
}

p <- p +
  labs(
    title    = paste0(SEASON, " NFL Advanced Passing - QB"),
    subtitle = "Aggressiveness % vs Completion % Above Expected | bubble size = attempts | min 100 att",
    x        = "Aggressiveness % (throws into tight windows)",
    y        = "CPOE - Completion % Above Expected",
    caption  = "Source: NFL Next Gen Stats / nflreadr | JHCV"
  ) +
  dark_theme +
  theme(legend.position = "bottom")

x_lim <- range(df$aggressiveness, na.rm = TRUE)
y_lim <- range(df$cpoe_ngs, na.rm = TRUE)

p <- p +
  annotate("text", x = x_lim[1] + 0.3, y = y_lim[2] - 0.3,
           label = "Careful but accurate", color = CYAN, size = 2.6, hjust = 0, fontface = "italic") +
  annotate("text", x = x_lim[2] - 0.3, y = y_lim[2] - 0.3,
           label = "Aggressive + accurate", color = GRN, size = 2.6, hjust = 1, fontface = "italic") +
  annotate("text", x = x_lim[2] - 0.3, y = y_lim[1] + 0.3,
           label = "Aggressive + inaccurate", color = RED, size = 2.6, hjust = 1, fontface = "italic")

ggsave(OUT_PNG, p, width = 9, height = 6.5, dpi = 220, bg = BG)
message("Saved: ", OUT_PNG)
