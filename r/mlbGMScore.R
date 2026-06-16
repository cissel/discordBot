#!/usr/bin/env Rscript
# mlbGMScore.R — World Sillies MLB Fantasy GM Score plot
# Same style as fantasyWrapped.Rmd GM Score section:
#   stacked z-score bars (4 components) + cyan composite score line
#
# Args: [csv_path] [out_path]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(readr)
})

args     <- commandArgs(trailingOnly = TRUE)
csv_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/sports/mlb/fantasy/gm_scores.csv"
out_path <- if (length(args) >= 2) args[2] else "~/discordBot/outputs/sports/mlb/fantasy/gm_score_plot.png"
csv_path <- path.expand(csv_path)
out_path <- path.expand(out_path)

# ── theme (dark blue, same as other fantasy charts) ───────────────────────────
col_bg    <- "#02233F"
col_white <- "#FFFFFF"
col_grid  <- "#1a3a5c"
col_gold  <- "#FFD700"
col_cyan  <- "#00FFFF"

col_draft  <- "#4CAF50"   # green
col_eff    <- "#2196F3"   # blue
col_txn    <- "#FF9800"   # orange
col_record <- "#E040FB"   # purple

tw <- function() {
  theme(
    plot.background    = element_rect(fill = col_bg,    color = NA),
    panel.background   = element_rect(fill = col_bg,    color = NA),
    panel.grid.major.x = element_line(color = col_grid, linewidth = 0.3),
    panel.grid.major.y = element_blank(),
    panel.grid.minor   = element_blank(),
    axis.text          = element_text(color = col_white, size = 9),
    axis.title         = element_text(color = col_white, size = 10),
    plot.title         = element_text(color = col_white, size = 13, face = "bold", hjust = 0.5),
    plot.subtitle      = element_text(color = col_gold,  size = 9,  hjust = 0.5),
    plot.caption       = element_text(color = col_white, size = 7,  hjust = 1),
    legend.background  = element_rect(fill = col_bg, color = NA),
    legend.text        = element_text(color = col_white, size = 8),
    legend.title       = element_text(color = col_white, size = 9),
    legend.key         = element_rect(fill = col_bg, color = NA),
    plot.margin        = margin(12, 16, 8, 12)
  )
}

# ── load data ──────────────────────────────────────────────────────────────────
if (!file.exists(csv_path)) {
  message("gm_scores.csv not found: ", csv_path)
  quit(status = 1)
}

df <- read_csv(csv_path, show_col_types = FALSE)

# ── pivot to long for stacked bar ─────────────────────────────────────────────
gm_long <- df %>%
  select(team_name, z_draft, z_eff, z_txn, z_record, gm_score) %>%
  pivot_longer(cols = c(z_draft, z_eff, z_txn, z_record),
               names_to = "component", values_to = "z")

# Order teams ascending by gm_score so highest is at top with coord_flip
gm_order <- df %>% arrange(gm_score) %>% pull(team_name)

component_labels <- c(
  z_draft  = "Draft VOE",
  z_eff    = "Lineup Eff",
  z_txn    = "Txn Net",
  z_record = "Record"
)

component_colors <- c(
  z_draft  = col_draft,
  z_eff    = col_eff,
  z_txn    = col_txn,
  z_record = col_record
)

gm_long <- gm_long %>%
  mutate(
    team_name  = factor(team_name, levels = gm_order),
    component  = factor(component, levels = c("z_draft", "z_eff", "z_txn", "z_record"))
  )

gm_score_ref <- df %>%
  select(team_name, gm_score, wins, losses, grade) %>%
  mutate(team_name = factor(team_name, levels = gm_order))

# Format W-L for axis labels
wl_labels <- df %>%
  arrange(match(team_name, gm_order)) %>%
  mutate(axis_label = paste0(team_name, "  (", wins, "-", losses, ")")) %>%
  pull(axis_label)

# ── build plot ─────────────────────────────────────────────────────────────────
p <- ggplot(gm_long, aes(x = factor(team_name, levels = gm_order), y = z, fill = component)) +
  geom_col(position = "stack", alpha = 0.85, width = 0.7) +
  geom_hline(yintercept = 0, color = col_white, linewidth = 0.5, alpha = 0.7) +

  # Cyan composite score line (±0.42 bar-width segment trick)
  geom_segment(
    data = gm_score_ref,
    aes(
      x    = as.numeric(factor(team_name, levels = gm_order)) - 0.42,
      xend = as.numeric(factor(team_name, levels = gm_order)) + 0.42,
      y    = gm_score,
      yend = gm_score
    ),
    inherit.aes = FALSE,
    color = col_cyan, linewidth = 1.4
  ) +

  # Composite score label
  geom_text(
    data = gm_score_ref,
    aes(
      x     = factor(team_name, levels = gm_order),
      y     = gm_score + ifelse(gm_score >= 0, 0.08, -0.08),
      label = paste0(grade, "  ", ifelse(gm_score >= 0, "+", ""), round(gm_score, 2))
    ),
    inherit.aes = FALSE,
    color = col_cyan, size = 3.0, fontface = "bold",
    hjust = ifelse(gm_score_ref$gm_score >= 0, -0.1, 1.1)
  ) +

  scale_fill_manual(
    values = component_colors,
    labels = component_labels,
    name   = "Component"
  ) +
  scale_x_discrete(labels = wl_labels) +
  scale_y_continuous(
    expand = expansion(mult = c(0.05, 0.15)),
    breaks = scales::pretty_breaks(n = 6)
  ) +
  coord_flip() +
  labs(
    title    = paste0("World Sillies GM Score \u2014 ", format(Sys.Date(), "%Y")),
    subtitle = "Stacked z-scores: Draft VOE + Lineup Efficiency + Transaction Net + Record  \u2022  Cyan = composite GM Score",
    x        = NULL,
    y        = "Z-Score",
    caption  = paste0("ESPN World Sillies | ", format(Sys.Date(), "%b %d, %Y"), " | JHCV")
  ) +
  tw() +
  theme(
    legend.position = "bottom",
    legend.direction = "horizontal"
  )

# ── save ───────────────────────────────────────────────────────────────────────
ggsave(out_path, plot = p, width = 10, height = 6, dpi = 150, bg = col_bg)
message("saved: ", out_path)
