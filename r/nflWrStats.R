#!/usr/bin/env Rscript
# nflWrStats.R
# Advanced WR receiving stats leaderboard (WR only)
# Args: [season] [player_name (optional, for single-player mode)]
# Sources: NFL Next Gen Stats + nflreadr player_stats + PFR advstats

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
OUT_PNG <- file.path(OUT_DIR, "nflWrStats.png")
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
  legend.key        = element_rect(fill = BG, color = NA),
  strip.background  = element_rect(fill = BG, color = GRID),
  strip.text        = element_text(color = TXT, size = 8.5, face = "bold")
)

message("Loading NGS receiving data for ", SEASON, "...")
ngs <- tryCatch(
  load_nextgen_stats(seasons = SEASON, stat_type = "receiving"),
  error = function(e) { message("NGS error: ", e$message); NULL }
)
if (is.null(ngs) || nrow(ngs) == 0) {
  message("No NGS receiving data available.")
  quit(status = 1)
}

message("Loading player stats for ", SEASON, "...")
pstats <- tryCatch(
  load_player_stats(seasons = SEASON) %>%
    filter(position == "WR") %>%
    group_by(player_id, player_display_name, position) %>%
    summarise(
      targets       = sum(targets, na.rm = TRUE),
      receptions    = sum(receptions, na.rm = TRUE),
      rec_yards     = sum(receiving_yards, na.rm = TRUE),
      rec_tds       = sum(receiving_tds, na.rm = TRUE),
      rec_first_down = sum(receiving_first_downs, na.rm = TRUE),
      rec_epa       = sum(receiving_epa, na.rm = TRUE),
      target_share  = mean(target_share, na.rm = TRUE),
      air_yards_share = mean(air_yards_share, na.rm = TRUE),
      wopr          = mean(wopr, na.rm = TRUE),
      .groups = "drop"
    ),
  error = function(e) { message("player_stats error: ", e$message); NULL }
)

# Season-aggregate NGS (WR only)
ngs_szn <- ngs %>%
  filter(season_type == "REG", player_position == "WR") %>%
  group_by(player_gsis_id, player_display_name, player_position) %>%
  summarise(
    ngs_tgts        = sum(targets, na.rm = TRUE),
    avg_sep         = mean(avg_separation, na.rm = TRUE),
    avg_cushion     = mean(avg_cushion, na.rm = TRUE),
    avg_yac_oe      = mean(avg_yac_above_expectation, na.rm = TRUE),
    avg_air_yds     = mean(avg_intended_air_yards, na.rm = TRUE),
    air_yds_share   = mean(percent_share_of_intended_air_yards, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(ngs_tgts >= 30)

# Join player_stats
if (!is.null(pstats)) {
  df <- ngs_szn %>%
    left_join(pstats, by = c("player_gsis_id" = "player_id"))
  df <- df %>%
    mutate(name = dplyr::coalesce(player_display_name.x, player_display_name.y))
} else {
  df <- ngs_szn %>% rename(name = player_display_name)
}

df <- df %>%
  filter(!is.na(name), !is.na(avg_sep), !is.na(avg_yac_oe))

# Use raw targets for size (fall back to ngs_tgts if pstats missing)
df <- df %>%
  mutate(sz_targets = dplyr::coalesce(targets, ngs_tgts))

# Single player mode
single_mode <- !is.na(PLAYER)
if (single_mode) {
  target_row <- df %>%
    filter(toupper(name) == PLAYER | toupper(sub(".* ", "", name)) == PLAYER)
  if (nrow(target_row) == 0) {
    message(paste0("Player '", PLAYER, "' not found in ", SEASON, " data (min 30 targets required)."))
    quit(status = 2)
  }
}

# ---- Main scatter: Separation vs YAC Over Expected ----
# Size by targets (raw count), gradient color by rec_epa
p <- ggplot(df, aes(x = avg_sep, y = avg_yac_oe)) +
  geom_hline(yintercept = 0, color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_vline(xintercept = mean(df$avg_sep, na.rm = TRUE),
             color = GRID, linetype = "dashed", linewidth = 0.5) +
  geom_point(
    aes(size = sz_targets,
        color = rec_epa),
    alpha = 0.80
  ) +
  scale_color_gradientn(
    colours  = c("#FF4444", "#FF8C00", "#FFFFFF", "#26a69a"),
    name     = "Rec EPA",
    na.value = "#888888"
  ) +
  scale_size_continuous(range = c(1.5, 6), name = "Targets") +
  geom_text(
    aes(label = sub("(\\w)\\.\\s+(.*)", "\\2", name)),  # abbreviate first name
    size   = 2.4,
    color  = TXT,
    vjust  = -0.7,
    na.rm  = TRUE
  )

# Highlight single player if provided
if (single_mode && nrow(target_row) > 0) {
  p <- p + geom_point(
    data  = target_row,
    aes(x = avg_sep, y = avg_yac_oe),
    color = "white", size = 5, shape = 1, stroke = 1.5
  )
}

p <- p +
  labs(
    title    = paste0(SEASON, " NFL Advanced Receiving - WR"),
    subtitle = paste0("Separation vs YAC Over Expected | bubble size = targets | color = Rec EPA | min 30 targets"),
    x        = "Avg Separation at Catch (yds)",
    y        = "Avg YAC Over Expected (yds)",
    caption  = "Source: NFL Next Gen Stats / nflreadr | JHCV"
  ) +
  dark_theme +
  theme(legend.position = "bottom")

# Quadrant labels
x_mid <- mean(df$avg_sep, na.rm = TRUE)
y_lim <- range(df$avg_yac_oe, na.rm = TRUE)
x_lim <- range(df$avg_sep, na.rm = TRUE)

p <- p +
  annotate("text", x = x_lim[1] + 0.1, y = y_lim[2] - 0.1,
           label = "Open + Creates YAC", color = GRN, size = 2.6, hjust = 0, fontface = "italic") +
  annotate("text", x = x_lim[2] - 0.1, y = y_lim[1] + 0.1,
           label = "Tight + Loses YAC", color = RED, size = 2.6, hjust = 1, fontface = "italic")

ggsave(OUT_PNG, p, width = 9, height = 6.5, dpi = 220, bg = BG)
message("Saved: ", OUT_PNG)
