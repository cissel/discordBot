# mlbOBPSharpe.R by JHCV
# Batting-side analogue to mlbERASharpe.R / mlbWHIPSharpe.R.
# James's instinct: AVG is a bad candidate (bounded 0-1, walks ignored, only ~4-5
# PA/game means very few distinct values possible per game - it's "lumpy").
# OBP and OPS are better bases: not walk-blind, and OPS captures power too.
#
# Game-level OBP = (H + BB) / PA          (no HBP column available - close approx)
# Game-level SLG = TB / AB
# Game-level OPS = OBP + SLG
#
# OBS = (pitcher... er, batter's mean rate stat - league avg) / (that stat's game-to-game SD)
#   - unlike ERA/WHIP, HIGHER OBP/OPS is better, so no sign flip and no axis flip needed
#   - rewards batters who are BOTH better than average AND consistent about it
#
# Usage: Rscript mlbOBPSharpe.R <output_path> <mode: obp|ops> <min_games> <fa_filter>
#   fa_filter: pipe-delimited player names to restrict to (e.g. free agents), or "ALL"

suppressPackageStartupMessages({
  library(readr)
  library(dplyr)
  library(ggplot2)
  library(ggrepel)
  library(scales)
  library(stringi)
})

args      <- commandArgs(trailingOnly = TRUE)
out_path  <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/sports/mlb/fantasy/obpSharpe.png"
mode      <- if (length(args) >= 2) tolower(args[2]) else "obp"
min_games <- if (length(args) >= 3) as.integer(args[3]) else 20L
fa_filter <- if (length(args) >= 4) args[4] else "ALL"
out_path  <- path.expand(out_path)

if (!mode %in% c("obp", "ops")) stop(paste("Unknown mode:", mode, "- use 'obp' or 'ops'"))

FANTASY_DIR <- "~/discordBot/outputs/sports/mlb/fantasy/playerData"
logs <- read_csv(file.path(FANTASY_DIR, "batter_game_logs.csv"), show_col_types = FALSE)

# league baseline always computed from the FULL unfiltered league, even in FA scope
full_logs <- logs

# ── FA filter (accent-insensitive, same approach as mlbFantasyRiskPlotte.R) ──
if (fa_filter != "ALL") {
  fa_names <- strsplit(fa_filter, "\\|")[[1]]
  fa_names_clean <- stri_trans_general(fa_names, "Latin-ASCII")
  logs <- logs %>%
    mutate(player_name_clean = stri_trans_general(player_name, "Latin-ASCII")) %>%
    filter(player_name_clean %in% fa_names_clean) %>%
    select(-player_name_clean)
  message("FA filter applied: ", nrow(logs), " rows, ", n_distinct(logs$player_name), " players remaining")
  # streamers/waiver hitters may have few games - relax the games floor but still
  # need >=2 for sd(). Total PA floor (100) applied later, after per-player aggregation.
  min_games <- 2L
  min_pa_fa <- 100L
} else {
  min_pa_fa <- 0L
}

# ── navy theme (James's standard) ───────────────────────────────────────────
myTheme <- theme(
  legend.position   = "right",
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid        = element_line(color = "#274066"),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white"),
  axis.title        = element_text(color = "white"),
  plot.title        = element_text(color = "white", hjust = .5, face = "bold", size = 15),
  plot.subtitle     = element_text(color = "white", hjust = .5, size = 10),
  plot.caption      = element_text(color = "white", size = 8),
  strip.background  = element_rect(fill = "#0a2d50", color = NA),
  strip.text        = element_text(color = "white", face = "bold"),
  legend.background = element_rect(fill = "#02233F", color = NA),
  legend.text       = element_text(color = "white"),
  legend.title      = element_text(color = "white")
)

# ── game-level rate stats (computed for both filtered logs AND full league) ──
# OBP defined only where PA > 0; SLG defined only where AB > 0; OPS needs both.
add_rates <- function(df) {
  df %>%
    mutate(
      obp_game = if_else(PA > 0, (H + BB) / PA, NA_real_),
      slg_game = if_else(AB > 0, TB / AB, NA_real_),
      ops_game = obp_game + slg_game
    )
}
g <- add_rates(logs)
g_full <- add_rates(full_logs)

metric_col <- if (mode == "obp") "obp_game" else "ops_game"
metric_label <- if (mode == "obp") "OBP" else "OPS"
sd_label <- paste0(metric_label, "SD")
sharpe_label <- paste0(metric_label, " Sharpe")

g$metric_val <- g[[metric_col]]
g_valid <- g %>% filter(!is.na(metric_val))

g_full$metric_val <- g_full[[metric_col]]
league_avg <- mean(g_full$metric_val, na.rm = TRUE)   # always the FULL-league baseline

# ── per-batter stats ──────────────────────────────────────────────────────────
stats <- g_valid %>%
  group_by(playerid, player_name, fantasy_position) %>%
  summarize(
    games      = n(),
    total_pa   = sum(PA, na.rm = TRUE),
    mean_metric = mean(metric_val, na.rm = TRUE),
    metric_sd   = sd(metric_val, na.rm = TRUE),
    .groups    = "drop"
  ) %>%
  filter(games >= min_games, total_pa >= min_pa_fa, is.finite(metric_sd), metric_sd > 0) %>%
  mutate(sharpe = (mean_metric - league_avg) / metric_sd)   # higher metric = better, so no sign flip

# show ALL qualifying batters (previously capped at top 45 by PA, which silently
# dropped up to 80% of qualified hitters - fixed per James's report Aug 2026)
cat("Batters plotted:", nrow(stats), "\n")

p <- ggplot(stats, aes(x = metric_sd, y = mean_metric, size = total_pa, color = sharpe)) +
  geom_point(alpha = 0.8) +
  geom_text_repel(aes(label = player_name), color = "white", size = 2.2,
                   max.overlaps = 40, box.padding = 0.3, force = 2,
                   segment.color = "#274066", segment.alpha = 0.5) +
  scale_color_gradient2(low = "red", mid = "white", high = "#2E9E4E",
                         midpoint = 0, name = paste0(sharpe_label, "\n")) +
  scale_size_continuous(name = "Total PA", range = c(1.5, 8)) +
  labs(
    x = paste0(sd_label, "  (std dev of game-level ", metric_label, " - lower = more consistent)"),
    y = paste0("Mean ", metric_label, "  (up = better)"),
    title = paste0(metric_label, " Sharpe - Consistency-Adjusted Hitting Efficiency"),
    subtitle = paste0(sharpe_label, " = (batter mean ", metric_label, " - league avg ", metric_label,
                       ") / ", sd_label, "   |   min games: ", min_games,
                       if (fa_filter != "ALL") paste0("  |  Free Agents Only (min ", min_pa_fa, " PA)") else "",
                       "   |   as of ", as.Date(max(logs$game_date, na.rm = TRUE))),
    caption = "Source: MLB Stats API game logs | JHCV | OBP approximated as (H+BB)/PA - no HBP in data | Ohtani excluded (batting-side game logs unavailable - fetched as SP only)"
  ) +
  myTheme

# smaller canvas is plenty when FA-scoped (pool is naturally small); full league needs the big canvas
plot_w <- if (fa_filter != "ALL") 12 else 18
plot_h <- if (fa_filter != "ALL") 8  else 12
ggsave(out_path, plot = p, width = plot_w, height = plot_h, dpi = 150)
cat("Saved:", out_path, "\n")
