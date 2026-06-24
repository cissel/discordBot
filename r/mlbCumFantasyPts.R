#!/usr/bin/env Rscript
# mlbCumFantasyPts.R
# Faceted cumulative fantasy points chart for dock ellis fan club (World Sillies ESPN)
# Panels: batters first (high->low), then pitchers (high->low)
# Two-way players (Ohtani) appear in BOTH batter and pitcher sections
# Mid-season acquisitions get a vertical red dashed line at their join date
#
# Args:
#   [1] csv_path   (default: ~/discordBot/outputs/sports/mlb/fantasy/cumfp.csv)
#   [2] out_path   (default: ~/discordBot/outputs/sports/mlb/fantasy/cumfp.png)
#   [3] meta_path  (default: ~/discordBot/outputs/sports/mlb/fantasy/cumfp_meta.csv)

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(lubridate)
})

args      <- commandArgs(trailingOnly = TRUE)
csv_path  <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/sports/mlb/fantasy/cumfp_t4.csv"
out_path  <- if (length(args) >= 2) args[2] else "~/discordBot/outputs/sports/mlb/fantasy/cumfp_t4.png"
meta_path <- if (length(args) >= 3) args[3] else "~/discordBot/outputs/sports/mlb/fantasy/cumfp_t4_meta.csv"
team_name <- if (length(args) >= 4) args[4] else "dock ellis fan club"
csv_path  <- path.expand(csv_path)
out_path  <- path.expand(out_path)
meta_path <- path.expand(meta_path)

# ── theme ─────────────────────────────────────────────────────────────────────
col_bg    <- "#02233F"
col_panel <- "#031e37"
col_white <- "#FFFFFF"
col_grid  <- "#1a3a5c"
col_cyan  <- "#00BFFF"
col_gold  <- "#FFD700"
col_vline <- "#FF4444"

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
    plot.title         = element_text(color = col_white, size = 13, face = "bold", hjust = 0.5),
    plot.subtitle      = element_text(color = col_gold,  size = 8.5, hjust = 0.5),
    plot.caption       = element_text(color = col_white, size = 7,   hjust = 1),
    legend.position    = "none",
    plot.margin        = margin(12, 16, 8, 12)
  )
}

# ── load data ─────────────────────────────────────────────────────────────────
if (!file.exists(csv_path)) {
  message("[mlbCumFantasyPts] CSV not found: ", csv_path)
  quit(status = 1)
}

df   <- read_csv(csv_path, show_col_types = FALSE) %>% mutate(date = as.Date(date))
meta <- if (file.exists(meta_path)) read_csv(meta_path, show_col_types = FALSE) else NULL

if (nrow(df) == 0) {
  message("[mlbCumFantasyPts] CSV is empty")
  quit(status = 1)
}

# ── cumulative points per player ───────────────────────────────────────────────
df <- df %>%
  arrange(player_name, date) %>%
  group_by(player_name) %>%
  mutate(cum_pts = cumsum(daily_pts)) %>%
  ungroup()

# ── process meta ──────────────────────────────────────────────────────────────
PITCHER_POS   <- c("SP", "RP")
PITCHER_SLOTS <- c(13L, 14L, 15L)   # ESPN slot IDs: SP=13, RP=14/15

if (!is.null(meta)) {

  # Fix acq_date: blank for draft picks, real date for mid-season adds
  # Use case_when (not ifelse) to preserve Date class
  meta <- meta %>%
    mutate(
      acq_date   = as.Date(case_when(
        is_draft ~ NA_character_,
        is.na(acq_date) | acq_date == "" ~ NA_character_,
        TRUE ~ as.character(acq_date)
      )),
      is_pitcher = position %in% PITCHER_POS,
      pos_group  = if_else(is_pitcher, "Pitcher", "Batter"),
      pos_order  = case_when(
        position == "C"   ~ 1L,
        position == "1B"  ~ 2L,
        position == "2B"  ~ 3L,
        position == "3B"  ~ 4L,
        position == "SS"  ~ 5L,
        position == "LF"  ~ 6L,
        position == "CF"  ~ 7L,
        position == "RF"  ~ 8L,
        position == "DH"  ~ 9L,
        position == "SP"  ~ 10L,
        position == "RP"  ~ 11L,
        TRUE              ~ 12L
      )
    )

  # ── two-way players: their ★ rows already exist in df (written by Python) ──
  # Just add the pitcher-side meta rows so they get correct panel_label / pos_group
  two_way_meta <- meta %>% filter(is_two_way)
  if (nrow(two_way_meta) > 0) {
    pitcher_rows <- two_way_meta %>%
      mutate(
        player_name = paste0(player_name, " \u2605"),
        position    = "SP",
        is_pitcher  = TRUE,
        pos_group   = "Pitcher",
        pos_order   = 10L
      )
    meta <- bind_rows(meta, pitcher_rows)
  }

} else {
  # No meta: treat everyone as a batter, no acq lines
  meta <- df %>%
    distinct(player_name) %>%
    mutate(position = "?", pos_group = "Batter", pos_order = 99L,
           acq_date = as.Date(NA), is_draft = TRUE, is_pitcher = FALSE)
}

# ── season totals (after two-way rows added to df) ───────────────────────────
totals <- df %>%
  group_by(player_name) %>%
  summarise(season_total = max(cum_pts, na.rm = TRUE), .groups = "drop") %>%
  left_join(
    meta %>% select(player_name, position, pos_group, pos_order, acq_date, is_draft),
    by = "player_name"
  )

# ── sort: Batters first (high->low), then Pitchers (high->low) ───────────────
totals <- totals %>%
  arrange(
    desc(pos_group == "Batter"),
    desc(season_total)
  )

# ── short names + panel labels ────────────────────────────────────────────────
shorten_name <- function(nm) {
  # strip ★ suffix before shortening, re-add after
  star  <- grepl("\u2605", nm)
  clean <- sub(" \u2605$", "", nm)
  parts <- strsplit(clean, " ")[[1]]
  short <- if (length(parts) >= 2)
    paste0(substr(parts[1], 1, 1), ". ", paste(parts[-1], collapse = " "))
  else clean
  if (star) paste0(short, " \u2605") else short
}

totals <- totals %>%
  mutate(
    short_name  = sapply(player_name, shorten_name),
    panel_label = paste0(short_name, "  [", replace(position, is.na(position), "?"), "]")
  )

# Factor level = display order
df <- df %>%
  left_join(
    totals %>% select(player_name, panel_label, season_total, pos_group, acq_date, is_draft),
    by = "player_name"
  ) %>%
  mutate(
    panel_label = factor(panel_label, levels = totals$panel_label),
    line_col    = if_else(pos_group == "Batter", col_cyan, "#FF8C00")
  )

totals <- totals %>%
  mutate(panel_label = factor(panel_label, levels = totals$panel_label))

# ── acquisition vlines ────────────────────────────────────────────────────────
acq_lines <- totals %>%
  filter(!is_draft, !is.na(acq_date)) %>%
  select(panel_label, acq_date)

cat(sprintf("[mlbCumFantasyPts] %d panels, %d acquisition lines\n",
            nrow(totals), nrow(acq_lines)))

# ── date range ────────────────────────────────────────────────────────────────
min_date <- min(df$date)
max_date <- max(df$date)
as_of    <- format(max_date, "%b %d, %Y")

# ── season total label data ───────────────────────────────────────────────────
label_df <- totals %>%
  mutate(x_pos = min_date + 3, y_pos = Inf,
         label = paste0(round(season_total, 0), " pts")) %>%
  select(panel_label, x_pos, y_pos, label)

# ── plot ──────────────────────────────────────────────────────────────────────
n_players <- nrow(totals)
ncols     <- min(4L, n_players)

p <- ggplot(df, aes(x = date, y = cum_pts)) +
  geom_area(aes(fill = line_col), alpha = 0.18, show.legend = FALSE) +
  geom_line(aes(color = line_col), linewidth = 0.9, show.legend = FALSE) +
  # acquisition date vline
  { if (nrow(acq_lines) > 0)
      geom_vline(
        data      = acq_lines,
        aes(xintercept = acq_date),
        color     = col_vline,
        linewidth = 0.7,
        linetype  = "dashed"
      )
    else list()
  } +
  # acquisition date label (just above x-axis)
  { if (nrow(acq_lines) > 0)
      geom_text(
        data        = acq_lines,
        aes(x = acq_date + 1, y = -Inf, label = format(acq_date, "%b %d")),
        inherit.aes = FALSE,
        vjust    = -0.4,
        hjust    = 0,
        color    = col_vline,
        size     = 2.0,
        fontface = "italic"
      )
    else list()
  } +
  # season total label (top-left of panel)
  geom_text(
    data        = label_df,
    aes(x = x_pos, y = y_pos, label = label),
    inherit.aes = FALSE,
    vjust = 1.6, hjust = 0,
    color = col_gold, size = 2.4, fontface = "bold"
  ) +
  scale_x_date(
    date_breaks = "1 month",
    date_labels = "%b",
    expand      = expansion(mult = c(0.02, 0.05))
  ) +
  scale_y_continuous(
    labels = comma_format(accuracy = 1),
    expand = expansion(mult = c(0.08, 0.15))
  ) +
  scale_color_identity() +
  scale_fill_identity() +
  facet_wrap(~ panel_label, ncol = ncols, scales = "free_y") +
  labs(
    title    = paste0(team_name, " - cumulative fantasy points"),
    subtitle = paste0(
      "World Sillies ESPN 2026  |  through ", as_of,
      "  |  cyan = batters  orange = pitchers  red line = acquisition date  \u2605 = two-way"
    ),
    x       = NULL,
    y       = "cumulative fantasy points",
    caption = paste0(
      "ESPN H2H points  |  current roster  |  batters (high-low) then pitchers (high-low)  ",
      "|  updated ", format(Sys.Date(), "%Y-%m-%d")
    )
  ) +
  tw()

# ── output ────────────────────────────────────────────────────────────────────
nrows <- ceiling(n_players / ncols)
pw    <- 1400L
ph    <- max(700L, nrows * 210L + 140L)

png(out_path, width = pw, height = ph, res = 130L)
print(p)
invisible(dev.off())

cat(sprintf("[mlbCumFantasyPts] saved %s (%dx%d)\n", out_path, pw, ph))
