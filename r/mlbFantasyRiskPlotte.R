# fantasyRisk.R by JHCV
# Loads fantasy baseball game log CSVs into dataframes for analysis.
# Called by the Discord bot via /mlb fantasyRisk

##### Required packages #####
library(readr)
library(tidyverse)
library(dplyr)
#####

##### Plot Appearance Theme #####

myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = "#02233F"),
                 panel.background = element_rect(fill = "#02233F"),
                 panel.grid = element_line(color = "#274066"),
                 axis.ticks = element_line(color = "#274066"),
                 axis.text = element_text(color = "white"),
                 axis.title = element_text(color = "white"),
                 plot.title = element_text(color = "white",
                                           hjust = .5),
                 plot.subtitle = element_text(color = "white",
                                              hjust = .5),
                 plot.caption = element_text(color = "white"),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

#####

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

# ── paths ──────────────────────────────────────────────────────────────────────
FANTASY_DIR <- "~/discordBot/outputs/sports/mlb/fantasy/playerData"

batter_logs     <- read_csv(file.path(FANTASY_DIR, "batter_game_logs.csv"),  show_col_types = FALSE)
pitcher_logs    <- read_csv(file.path(FANTASY_DIR, "pitcher_game_logs.csv"), show_col_types = FALSE)
batter_summary  <- read_csv(file.path(FANTASY_DIR, "batter_season_summary.csv"),  show_col_types = FALSE)
pitcher_summary <- read_csv(file.path(FANTASY_DIR, "pitcher_season_summary.csv"), show_col_types = FALSE)

# ── args ──────────────────────────────────────────────────────────────────────
# Usage: Rscript mlbFantasyRiskPlotte.R <position> <output_path> <fa_filter>
#   position:    C | 1B | 2B | 3B | SS | OF | SP | RP | ALL
#   output_path: where to save the plot PNG
#   fa_filter:   pipe-delimited player names to filter to, or "ALL"

args      <- commandArgs(trailingOnly = TRUE)
position  <- if (length(args) >= 1) toupper(args[1]) else "ALL"
out_path  <- if (length(args) >= 2) args[2] else file.path(FANTASY_DIR, "fantasyRisk.png")
fa_filter <- if (length(args) >= 3) args[3] else "ALL"

PITCHER_POSITIONS <- c("SP", "RP")
BATTER_POSITIONS  <- c("C", "1B", "2B", "3B", "SS", "OF")

# ── position filter (creates logs) ────────────────────────────────────────────
if (position == "ALL") {
  logs <- bind_rows(batter_logs, pitcher_logs)
} else if (position == "UTIL") {
  logs <- batter_logs  # all non-pitcher positions
} else if (position %in% PITCHER_POSITIONS) {
  logs <- pitcher_logs %>% filter(fantasy_position == position)
} else if (position %in% BATTER_POSITIONS) {
  logs <- batter_logs %>% filter(fantasy_position == position)
} else {
  stop(paste("Unknown position:", position))
}

# ── FA filter (applied after logs exists) ─────────────────────────────────────
if (fa_filter != "ALL") {
  fa_names <- strsplit(fa_filter, "\\|")[[1]]
  # normalize accents on both sides so e.g. Hernandez == Hernández
  fa_names_clean <- stringi::stri_trans_general(fa_names, "Latin-ASCII")
  logs <- logs %>%
    mutate(player_name_clean = stringi::stri_trans_general(player_name, "Latin-ASCII")) %>%
    filter(player_name_clean %in% fa_names_clean) %>%
    select(-player_name_clean)
  message("FA filter applied: ", nrow(logs), " rows, ", n_distinct(logs$player_name), " players remaining")
}

cat("Position:", position, "\n")
cat("Rows loaded:", nrow(logs), "\n")
cat("Players:", n_distinct(logs$player_name), "\n")
cat("Output path:", out_path, "\n")

# ── your analysis goes here ────────────────────────────────────────────────────

# ── minimum games threshold + top-N cap (only applied when scope is ALL) ───────
# Keeps the "all players" plot readable by filtering out low-sample players
# and capping to the top performers by total fantasy points.
# FA/SP-starter scopes pass a name filter so these filters are skipped.
if (fa_filter == "ALL") {
  min_games <- if (position %in% PITCHER_POSITIONS) 8L else 15L
  top_n_cap <- if (position == "UTIL") 25L else if (position == "OF") 60L else if (position %in% PITCHER_POSITIONS) 60L else 30L
} else {
  min_games <- 1L
  top_n_cap <- Inf
}

df <- logs |>
  
  group_by(playerid,
           player_name,
           team) |>
  
  summarize("n"     = n(),
            "tot"   = sum(fantasy_pts),
            "m"     = mean(fantasy_pts),
            "sd"    = sd(fantasy_pts),
            "sharpe"= mean(fantasy_pts)/sd(fantasy_pts),
            .groups = "drop") |>
  
  filter(n >= min_games) |>
  slice_max(order_by = tot, n = top_n_cap, with_ties = FALSE)

p <- ggplot(df,
            aes(x = sd,
                y = m,
                size = tot,
                color = sharpe)) +
  
  geom_text(aes(label = player_name)) +
  
  scale_color_gradient2(low = "red", 
                        mid = "white", 
                        high = "green", 
                        midpoint = median(df$sharpe,
                                          na.rm = TRUE)) +
  
  labs(x = "Standard Deviation",
       y = "Mean",
       color = "Sharpe",
       size = "Fantasy Points",
       title = "MLB Fantasy Points per Game",
       subtitle = paste0(position, " - as of ", as.Date(max(logs$game_date, na.rm = TRUE)),
                         if (fa_filter == "ALL") paste0("  (min ", min_games, " games)") else ""),
       caption = "Source: ESPN Fantasy Baseball API / Statcast | JHCV") +
  
  myTheme +
  myLegend

# ── save plot to out_path ──────────────────────────────────────────────────────
ggsave(out_path, plot = p, width = 10, height = 7, dpi = 150)