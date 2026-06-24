#!/usr/bin/env Rscript
# mlbFantasySzn.R
# Panelized fantasy points for every World Sillies team over the season.
# Panels sorted by current standings (1st place top-left, last place bottom-right).
#
# Modes:
#   cumulative - line+area of running total PF
#   daily      - bar chart of daily pts + mean hline + 10-day rolling avg line
#
# Args:
#   [1] csv_path  (default: ~/discordBot/outputs/sports/mlb/fantasy/szn_daily.csv)
#   [2] out_path  (default: ~/discordBot/outputs/sports/mlb/fantasy/szn_plot_cumulative.png)
#   [3] mode      "cumulative" (default) or "daily"

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(zoo)       # rollmean
})

args     <- commandArgs(trailingOnly = TRUE)
csv_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/sports/mlb/fantasy/szn_daily.csv"
out_path <- if (length(args) >= 2) args[2] else "~/discordBot/outputs/sports/mlb/fantasy/szn_plot_cumulative.png"
mode     <- if (length(args) >= 3) args[3] else "cumulative"
csv_path <- path.expand(csv_path)
out_path <- path.expand(out_path)

# ── theme ─────────────────────────────────────────────────────────────────────
col_bg     <- "#02233F"
col_panel  <- "#031e37"
col_white  <- "#FFFFFF"
col_grid   <- "#1a3a5c"
col_gold   <- "#FFD700"
col_silver <- "#C0C0C0"
col_bronze <- "#CD7F32"
col_mean   <- "#FFFFFF"     # mean line - white dashed
col_r10    <- "#FFFFFF"     # 10-game rolling avg - white

tw <- function() {
  theme(
    plot.background    = element_rect(fill = col_bg,    color = NA),
    panel.background   = element_rect(fill = col_panel, color = NA),
    panel.grid.major   = element_line(color = col_grid,  linewidth = 0.3),
    panel.grid.minor   = element_blank(),
    strip.background   = element_rect(fill = "#0a2d50",  color = NA),
    strip.text         = element_text(color = col_white, size = 7.5, face = "bold"),
    axis.text          = element_text(color = col_white, size = 6.5),
    axis.text.x        = element_text(angle = 30, hjust = 1),
    axis.title         = element_text(color = col_white, size = 9),
    plot.title         = element_text(color = col_white, size = 14, face = "bold", hjust = 0.5),
    plot.subtitle      = element_text(color = col_gold,  size = 8.5, hjust = 0.5),
    plot.caption       = element_text(color = col_white, size = 7,   hjust = 1),
    legend.position    = "bottom",
    legend.background  = element_rect(fill = col_bg, color = NA),
    legend.text        = element_text(color = col_white, size = 7.5),
    legend.title       = element_blank(),
    legend.key         = element_rect(fill = col_bg, color = NA),
    plot.margin        = margin(12, 16, 8, 12)
  )
}

# ── load ──────────────────────────────────────────────────────────────────────
if (!file.exists(csv_path)) {
  message("[mlbFantasySzn] CSV not found: ", csv_path)
  quit(status = 1)
}

df <- read_csv(csv_path, show_col_types = FALSE) %>%
  mutate(date = as.Date(date))

if (nrow(df) == 0) {
  message("[mlbFantasySzn] CSV is empty")
  quit(status = 1)
}

# ── cumulative or daily ────────────────────────────────────────────────────────
if (mode == "cumulative") {
  df <- df %>%
    arrange(team_name, date) %>%
    group_by(team_name) %>%
    mutate(plot_pts = cumsum(daily_pts)) %>%
    ungroup()
  y_label    <- "cumulative fantasy points"
  mode_title <- "cumulative fantasy points"
} else {
  df <- df %>%
    arrange(team_name, date) %>%
    mutate(plot_pts = daily_pts)
  y_label    <- "daily fantasy points"
  mode_title <- "daily fantasy points"
}

# ── standings info per team ────────────────────────────────────────────────────
standings <- df %>%
  group_by(team_name) %>%
  summarise(
    playoff_seed = first(playoff_seed),
    wins         = first(wins),
    losses       = first(losses),
    pf           = first(pf),
    season_total = if (mode == "cumulative") max(plot_pts, na.rm = TRUE)
                   else sum(daily_pts, na.rm = TRUE),
    .groups      = "drop"
  ) %>%
  arrange(playoff_seed)

# ── shorten team names ────────────────────────────────────────────────────────
shorten <- function(nm) {
  nm <- trimws(nm)
  if (nchar(nm) <= 22) return(nm)
  words <- strsplit(nm, " ")[[1]]
  out <- ""
  for (w in words) {
    candidate <- if (nchar(out) == 0) w else paste(out, w)
    if (nchar(candidate) <= 22) out <- candidate else break
  }
  paste0(out, "...")
}

# ── colour: gold=1st, silver=2nd, bronze=3rd, cyan=4, steel=5-8 ──────────────
place_colour <- function(seed) {
  case_when(
    seed == 1 ~ col_gold,
    seed == 2 ~ col_silver,
    seed == 3 ~ col_bronze,
    seed == 4 ~ "#00BFFF",
    TRUE      ~ "#6699CC"
  )
}

standings <- standings %>%
  mutate(
    short_name  = sapply(team_name, shorten),
    line_col    = sapply(playoff_seed, place_colour),
    panel_label = paste0(
      playoff_seed, ". ", short_name,
      "  (", wins, "-", losses, ")"
    )
  )

df <- df %>%
  left_join(standings %>% select(team_name, panel_label, line_col, playoff_seed),
            by = "team_name") %>%
  mutate(panel_label = factor(panel_label, levels = standings$panel_label))

standings <- standings %>%
  mutate(panel_label = factor(panel_label, levels = standings$panel_label))

# ── date range ────────────────────────────────────────────────────────────────
min_date <- min(df$date)
max_date <- max(df$date)
as_of    <- format(max_date, "%b %d, %Y")

# ── season total label ────────────────────────────────────────────────────────
label_df <- standings %>%
  mutate(
    x_pos = min_date + 3,
    y_pos = Inf,
    label = paste0(round(season_total, 0), " pts")
  ) %>%
  select(panel_label, x_pos, y_pos, label)

# ── daily-only: per-team mean + rolling averages ──────────────────────────────
if (mode == "daily") {
  # per-team mean hline data
  mean_df <- df %>%
    group_by(team_name, panel_label) %>%
    summarise(mean_pts = mean(daily_pts, na.rm = TRUE), .groups = "drop")

  # rolling averages - compute per team, keep NA where window not full
  roll_df <- df %>%
    arrange(team_name, date) %>%
    group_by(team_name) %>%
    mutate(
      roll10 = rollmean(daily_pts, k = 10, fill = NA, align = "right")
    ) %>%
    ungroup()
}

# ── subtitle changes per mode ─────────────────────────────────────────────────
colour_legend <- "gold = 1st  silver = 2nd  bronze = 3rd  cyan = 4th  blue = 5-8"

if (mode == "daily") {
  sub_extra <- "  |  white dashed = season mean  |  white line = 10-day rolling avg"
} else {
  sub_extra <- ""
}

# ── plot ──────────────────────────────────────────────────────────────────────
ncols <- 2

if (mode == "cumulative") {

  p <- ggplot(df, aes(x = date, y = plot_pts)) +
    geom_area(aes(fill = line_col), alpha = 0.18, show.legend = FALSE) +
    geom_line(aes(color = line_col), linewidth = 1.0, show.legend = FALSE) +
    geom_text(
      data = label_df, aes(x = x_pos, y = y_pos, label = label),
      inherit.aes = FALSE, vjust = 1.6, hjust = 0,
      color = col_gold, size = 2.6, fontface = "bold"
    ) +
    scale_x_date(date_breaks = "1 month", date_labels = "%b",
                 expand = expansion(mult = c(0.02, 0.05))) +
    scale_y_continuous(labels = comma_format(accuracy = 1),
                       expand = expansion(mult = c(0.05, 0.15))) +
    scale_color_identity() +
    scale_fill_identity() +
    facet_wrap(~ panel_label, ncol = ncols, scales = "fixed") +
    labs(
      title    = paste0("World Sillies 2026 - ", mode_title),
      subtitle = paste0("sorted by standings  |  through ", as_of, "  |  ", colour_legend),
      x = NULL, y = y_label,
      caption  = paste0("ESPN H2H points  |  standings order  |  updated ",
                        format(Sys.Date(), "%Y-%m-%d"))
    ) +
    tw()

} else {

  p <- ggplot(roll_df, aes(x = date)) +
    # bars
    geom_col(aes(y = daily_pts, fill = line_col),
             width = 0.8, alpha = 0.7, show.legend = FALSE) +
    # season mean hline (per panel)
    geom_hline(
      data      = mean_df,
      aes(yintercept = mean_pts),
      color     = col_mean, linewidth = 0.55, linetype = "dashed"
    ) +
    # 10-day rolling average
    geom_line(aes(y = roll10), color = col_r10, linewidth = 1.0, na.rm = TRUE) +
    # season total label
    geom_text(
      data = label_df, aes(x = x_pos, y = y_pos, label = label),
      inherit.aes = FALSE, vjust = 1.6, hjust = 0,
      color = col_gold, size = 2.6, fontface = "bold"
    ) +
    scale_x_date(date_breaks = "1 month", date_labels = "%b",
                 expand = expansion(mult = c(0.02, 0.05))) +
    scale_y_continuous(labels = comma_format(accuracy = 1),
                       expand = expansion(mult = c(0.08, 0.18))) +
    scale_fill_identity() +

    facet_wrap(~ panel_label, ncol = ncols, scales = "fixed") +
    labs(
      title    = paste0("World Sillies 2026 - ", mode_title),
      subtitle = paste0("sorted by standings  |  through ", as_of, "  |  ",
                        colour_legend, sub_extra),
      x = NULL, y = y_label,
      caption  = paste0("ESPN H2H points  |  standings order  |  updated ",
                        format(Sys.Date(), "%Y-%m-%d"))
    ) +
    tw()

}

# ── output ────────────────────────────────────────────────────────────────────
nrows <- ceiling(8L / ncols)
pw    <- 1100L
ph    <- if (mode == "daily") nrows * 265L + 180L else nrows * 240L + 140L

png(out_path, width = pw, height = ph, res = 130L)
print(p)
invisible(dev.off())

cat(sprintf("[mlbFantasySzn] saved %s (%dx%d)\n", out_path, pw, ph))
