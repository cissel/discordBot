#!/usr/bin/env Rscript
# nflAgingCurves.R
# Fantasy football career/aging curves by position - navy theme
# Usage: Rscript nflAgingCurves.R [output_path]
# Default output: outputs/sports/nfl/fantasy/aging_curves.png

suppressMessages({
  library(tidyverse)
  library(ggplot2)
  library(patchwork)
})

args     <- commandArgs(trailingOnly = TRUE)
OUT_PATH <- if (length(args) >= 1) args[1] else
            path.expand("~/discordBot/outputs/sports/nfl/fantasy/aging_curves.png")
FEAT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")

# ── theme (matches modelDiagnostics.R house style) ──────────────────────────
navy <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#1a3a5c", linewidth = 0.4),
  panel.grid.minor  = element_line(color = "#122840", linewidth = 0.2),
  axis.text         = element_text(color = "#a0b8cc", size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = "white",   size = 13, face = "bold", hjust = 0),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 9,  hjust = 0),
  plot.caption      = element_text(color = "#4a6a80", size = 7),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 9, face = "bold"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "#a0b8cc", size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = "#02233F"),
  plot.margin       = margin(10, 14, 10, 12)
)

POS_COLORS <- c(QB = "#4fc3f7", RB = "#ef5350", WR = "#69f0ae", TE = "#ffa726")

curves  <- read_csv(file.path(FEAT_DIR, "aging_curves.csv"), show_col_types = FALSE)
summary <- read_csv(file.path(FEAT_DIR, "aging_curve_summary.csv"), show_col_types = FALSE)

# ── Panel 1: combined season-value index, all positions overlaid ───────────
p1 <- ggplot(curves, aes(x = age, y = combined_index, color = position)) +
  geom_hline(yintercept = 100, linetype = "dashed", color = "#4a6a80", linewidth = 0.4) +
  geom_line(linewidth = 1.1) +
  geom_point(data = summary, aes(x = peak_age, y = 100, color = position),
             inherit.aes = FALSE, size = 2.5, shape = 18) +
  scale_color_manual(values = POS_COLORS) +
  scale_x_continuous(breaks = seq(20, 40, 2)) +
  labs(title = "Fantasy Career Value by Age",
       subtitle = "Room 40 scoring | delta-method aging curve (rate x volume), indexed to peak age = 100",
       x = "Age (as of Sept 1)", y = "Combined Season-Value Index", color = "Position") +
  navy

# ── Panel 2: rate curve only (per-game skill trajectory) ────────────────────
p2 <- ggplot(curves, aes(x = age, y = rate_index, color = position)) +
  geom_hline(yintercept = 100, linetype = "dashed", color = "#4a6a80", linewidth = 0.4) +
  geom_line(linewidth = 1.0) +
  scale_color_manual(values = POS_COLORS) +
  scale_x_continuous(breaks = seq(20, 40, 2)) +
  labs(title = "Per-Game Rate Curve",
       subtitle = "Pts/game skill trajectory (survivorship-bias-corrected)",
       x = "Age", y = "Rate Index (peak = 100)", color = "Position") +
  navy

# ── Panel 3: volume curve only (games-played / availability trajectory) ────
p3 <- ggplot(curves, aes(x = age, y = volume_index, color = position)) +
  geom_hline(yintercept = 100, linetype = "dashed", color = "#4a6a80", linewidth = 0.4) +
  geom_line(linewidth = 1.0) +
  scale_color_manual(values = POS_COLORS) +
  scale_x_continuous(breaks = seq(20, 40, 2)) +
  labs(title = "Availability Curve",
       subtitle = "Games-played trajectory -- where the RB/WR 'cliff' actually shows up",
       x = "Age", y = "Volume Index (peak = 100)", color = "Position") +
  navy

# ── Panel 4: sample support (n observations feeding each age point) ─────────
p4 <- ggplot(curves, aes(x = age, y = n_rate_obs, fill = position)) +
  geom_col(position = "dodge", alpha = 0.85) +
  scale_fill_manual(values = POS_COLORS) +
  scale_x_continuous(breaks = seq(20, 40, 2)) +
  labs(title = "Sample Support",
       subtitle = "Player-to-player deltas feeding each age point -- thin past ~34 for RB/WR",
       x = "Age", y = "# consecutive-season deltas", fill = "Position") +
  navy

layout <- (p1) / (p2 | p3) / p4
final  <- layout +
  plot_annotation(
    caption = sprintf(
      "Panel: %s (1999-2024, %d player-seasons) | Delta method isolates true aging from survivorship bias",
      paste(summary$position, collapse = "/"), sum(summary$n_player_seasons)
    )
  ) &
  theme(plot.background = element_rect(fill = "#02233F", color = NA))

ggsave(OUT_PATH, final, width = 11, height = 15, dpi = 150, bg = "#02233F")
cat("Wrote:", OUT_PATH, "\n")
