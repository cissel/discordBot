# mlbOwnershipPlot.R
# Fantasy points vs ownership % scatter for World Sillies league
# Called by: Rscript mlbOwnershipPlot.R <scope> <output_path>
#   scope: all | batters | pitchers

library(ggplot2)
library(ggrepel)
library(dplyr)
library(readr)

args      <- commandArgs(trailingOnly = TRUE)
position  <- if (length(args) >= 1) tolower(args[1]) else "all"
pool      <- if (length(args) >= 2) tolower(args[2]) else "all"
out_path  <- if (length(args) >= 3) args[3] else "~/discordBot/outputs/sports/mlb/fantasy/ownership_plot.png"

CSV_PATH  <- "~/discordBot/outputs/sports/mlb/fantasy/ownership.csv"
df        <- read_csv(CSV_PATH, show_col_types = FALSE)

# ── position grouping ─────────────────────────────────────────────────────────
PITCHER_POS <- c("SP", "RP")
df <- df %>%
  mutate(
    pos_group = case_when(
      position %in% PITCHER_POS ~ position,
      TRUE                       ~ "Batter"
    )
  )

# ── position filter ───────────────────────────────────────────────────────────
if (position == "batters") {
  df <- df %>% filter(pos_group == "Batter")
} else if (position == "pitchers") {
  df <- df %>% filter(pos_group %in% c("SP", "RP"))
} else if (position == "of") {
  df <- df %>% filter(position %in% c("OF", "LF", "CF", "RF"))
} else if (position %in% c("sp", "rp", "c", "1b", "2b", "3b", "ss")) {
  pos_upper <- toupper(position)
  df <- df %>% filter(position == pos_upper)
}
# "all" = no filter

# ── pool filter ───────────────────────────────────────────────────────────────
if (pool == "fa") {
  df <- df %>% filter(type == "FA")
}
# "all" = no filter

# drop players with 0 fantasy pts or 0% ownership (log scale can't handle 0)
df <- df %>% filter(fantasy_pts > 0, pct_owned > 0)

# ── colour palette ────────────────────────────────────────────────────────────
pal <- c(
  "Batter" = "#00bfff",   # cyan
  "SP"     = "#ff8c00",   # orange
  "RP"     = "#c0392b"    # red
)

# label every player with data - names are the plot, no points
df <- df %>%
  mutate(label = player_name)

# ── theme ─────────────────────────────────────────────────────────────────────
bot_theme <- theme(
  plot.background   = element_rect(fill = "#02233F"),
  panel.background  = element_rect(fill = "#02233F"),
  panel.grid        = element_line(color = "#274066"),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white", size = 9),
  axis.title        = element_text(color = "white", size = 11),
  plot.title        = element_text(color = "white", size = 14, hjust = 0.5, face = "bold"),
  plot.subtitle     = element_text(color = "white", size = 10, hjust = 0.5),
  plot.caption      = element_text(color = "white", size = 8),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "white"),
  legend.title      = element_text(color = "white"),
  legend.key        = element_rect(fill = "#02233F")
)

pos_label  <- switch(position,
  "batters"  = "Batters",
  "pitchers" = "Pitchers (SP + RP)",
  "all"      = "All Positions",
  toupper(position)   # specific position like SP, OF etc
)
pool_label <- if (pool == "fa") "Free Agents Only" else "All Players"

# ── plot ──────────────────────────────────────────────────────────────────────
p <- ggplot(df, aes(x = pct_owned, y = fantasy_pts, color = pos_group)) +

  geom_text(
    aes(label = label),
    size    = 2.8,
    color   = "#e6edf3",
    na.rm   = TRUE
  ) +

  scale_color_manual(values = pal, name = "Position") +

  scale_x_log10(
    labels = function(x) paste0(x, "%"),
    breaks = c(1, 2, 5, 10, 25, 50, 75, 100),
    limits = c(0.5, 102)
  ) +

  labs(
    x        = "Ownership %",
    y        = "Fantasy Points (season)",
    title    = "MLB Fantasy - Points vs Ownership",
    subtitle = paste0(pos_label, "  -  ", pool_label),
    caption  = paste0("Source: ESPN Fantasy Baseball API / Statcast | JHCV")
  ) +

  bot_theme

ggsave(out_path, plot = p, width = 11, height = 7, dpi = 150)
cat("Saved:", out_path, "\n")
