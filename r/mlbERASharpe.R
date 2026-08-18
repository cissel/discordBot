# mlbERASharpe.R by JHCV
# James's stat idea: ERSD (std dev of game-level ERA) as a companion to ERA,
# combined into ERS ("ERA Sharpe") - a Sharpe-ratio-style consistency metric.
#
# ERS = (league_avg_ERA_at_position - pitcher_mean_ERA) / pitcher_ERSD
#   - numerator: how far BELOW league average the pitcher's ERA runs (positive = good, since lower ERA is better)
#   - denominator: game-to-game ERA volatility (ERSD) - lower = more consistent
#   - so ERS rewards pitchers who are BOTH better than average AND consistent about it,
#     exactly like a financial Sharpe ratio rewards excess return per unit of volatility.
#
# Usage: Rscript mlbERASharpe.R <output_path> <min_games_sp> <min_games_rp> <fa_filter>
#   fa_filter: pipe-delimited player names to restrict to (e.g. free agents), or "ALL"

suppressPackageStartupMessages({
  library(readr)
  library(dplyr)
  library(ggplot2)
  library(ggrepel)
  library(scales)
  library(stringi)
})

args         <- commandArgs(trailingOnly = TRUE)
out_path     <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/sports/mlb/fantasy/eraSharpe.png"
min_games_sp <- if (length(args) >= 2) as.integer(args[2]) else 8L
min_games_rp <- if (length(args) >= 3) as.integer(args[3]) else 12L
fa_filter    <- if (length(args) >= 4) args[4] else "ALL"
out_path     <- path.expand(out_path)

FANTASY_DIR <- "~/discordBot/outputs/sports/mlb/fantasy/playerData"
logs <- read_csv(file.path(FANTASY_DIR, "pitcher_game_logs.csv"), show_col_types = FALSE)

# ── FA filter (accent-insensitive, same approach as mlbFantasyRiskPlotte.R) ──
if (fa_filter != "ALL") {
  fa_names <- strsplit(fa_filter, "\\|")[[1]]
  fa_names_clean <- stri_trans_general(fa_names, "Latin-ASCII")
  logs <- logs %>%
    mutate(player_name_clean = stri_trans_general(player_name, "Latin-ASCII")) %>%
    filter(player_name_clean %in% fa_names_clean) %>%
    select(-player_name_clean)
  message("FA filter applied: ", nrow(logs), " rows, ", n_distinct(logs$player_name), " players remaining")
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

# ── per-pitcher, per-position stats ──────────────────────────────────────────
stats <- logs %>%
  group_by(playerid, player_name, fantasy_position) %>%
  summarize(
    games     = n(),
    total_ip  = sum(IP, na.rm = TRUE),
    mean_era  = mean(ERA, na.rm = TRUE),
    ersd      = sd(ERA, na.rm = TRUE),
    .groups   = "drop"
  )

# league-average ERA computed PER POSITION (SP and RP have very different IP/game profiles)
# use the FULL unfiltered league for the baseline - even in FA scope, "average"
# should mean the whole league, not just the free agent pool
full_logs <- read_csv(file.path(FANTASY_DIR, "pitcher_game_logs.csv"), show_col_types = FALSE)
league_avg <- full_logs %>%
  group_by(fantasy_position) %>%
  summarize(league_era = mean(ERA, na.rm = TRUE), .groups = "drop")

# when scoped to FAs, drop the min-games floor (streamers may have few appearances)
# and skip the top-N cap, since the FA pool is already small.
# still need >=2 games minimum - sd() of a single observation is NA/undefined.
if (fa_filter != "ALL") {
  min_games_sp <- 2L
  min_games_rp <- 2L
}

stats <- stats %>%
  left_join(league_avg, by = "fantasy_position") %>%
  mutate(
    min_games = if_else(fantasy_position == "SP", min_games_sp, min_games_rp),
    ers       = (league_era - mean_era) / ersd
  ) %>%
  filter(games >= min_games, is.finite(ersd), ersd > 0)

# cap each facet by total innings pitched so labels stay legible (skip when FA-scoped)
# SP cluster is denser (more similar ERSD/ERA), so cap it tighter than RP
if (fa_filter == "ALL") {
  stats <- bind_rows(
    stats %>% filter(fantasy_position == "SP") %>% slice_max(order_by = total_ip, n = 26L, with_ties = FALSE),
    stats %>% filter(fantasy_position == "RP") %>% slice_max(order_by = total_ip, n = 35L, with_ties = FALSE)
  )
}

stats$fantasy_position <- factor(stats$fantasy_position, levels = c("SP", "RP"))

cat("SP pitchers plotted:", sum(stats$fantasy_position == "SP"), "\n")
cat("RP pitchers plotted:", sum(stats$fantasy_position == "RP"), "\n")

p <- ggplot(stats, aes(x = ersd, y = mean_era, size = total_ip, color = ers)) +
  geom_point(alpha = 0.85) +
  geom_text_repel(aes(label = player_name), color = "white", size = 2.6,
                   max.overlaps = 25, box.padding = 0.4, force = 3,
                   segment.color = "#274066") +
  scale_y_reverse() +   # lower ERA is better -> flip so "up" visually means "better"
  scale_color_gradient2(low = "red", mid = "white", high = "#2E9E4E",
                         midpoint = 0, name = "ERS\n(ERA Sharpe)") +
  scale_size_continuous(name = "Total IP", range = c(1.5, 8)) +
  facet_wrap(~ fantasy_position, scales = "free", ncol = 2) +
  labs(
    x = "ERSD  (std dev of game-level ERA - lower = more consistent)",
    y = "Mean ERA  (axis flipped: up = better)",
    title = "ERA Sharpe (ERS) - Consistency-Adjusted Pitching Efficiency",
    subtitle = paste0("ERS = (league avg ERA - pitcher mean ERA) / ERSD   |   min games: SP ",
                       min_games_sp, ", RP ", min_games_rp,
                       if (fa_filter != "ALL") "  |  Free Agents Only" else "",
                       "   |   as of ", as.Date(max(logs$game_date, na.rm = TRUE))),
    caption = "Source: MLB Stats API game logs | JHCV"
  ) +
  myTheme

ggsave(out_path, plot = p, width = 13, height = 7.5, dpi = 150)
cat("Saved:", out_path, "\n")
