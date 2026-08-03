#!/usr/bin/env Rscript
# nflPhase1Diagnostics.R
# 4-panel navy-theme diagnostic plot for the NFL moneyball Phase 1 feature build:
# scheme/coach signature + snap/target share. Sanity-checks the pipeline visually
# before handing off to Phase 2 modeling.
suppressMessages({
  library(tidyverse)
  library(ggplot2)
  library(patchwork)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
OUT_PATH <- file.path(OUT_DIR, "phase1_diagnostics.png")

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

feat  <- read_csv(file.path(OUT_DIR, "nfl_phase1_features.csv"), show_col_types = FALSE)
coach <- read_csv(file.path(OUT_DIR, "coach_scheme_signature.csv"), show_col_types = FALSE)
scheme <- read_csv(file.path(OUT_DIR, "team_scheme_features.csv"), show_col_types = FALSE)
staff  <- read_csv(file.path(OUT_DIR, "coaching_staff.csv"), show_col_types = FALSE)

# Panel 1: team PROE by season, 2024 sorted, colored by confidence in coach data
p1_data <- coach %>% filter(season == 2024) %>% arrange(proe_neutral)
p1 <- ggplot(p1_data, aes(x = reorder(team, proe_neutral), y = proe_neutral, fill = confidence)) +
  geom_col() +
  geom_hline(yintercept = 0, color = "#4a6a80", linetype = "dashed") +
  coord_flip() +
  scale_fill_manual(values = c(high = "#4fc3f7", medium = "#ffa726", low = "#ef5350")) +
  labs(title = "2024 Team Pass Rate Over Expected (Neutral Script)",
       subtitle = "Positive = pass-heavier than expected | color = coaching-staff data confidence",
       x = NULL, y = "PROE (neutral script)", fill = "Confidence") +
  navy

# Panel 2: coach signature vs actual team PROE that season - shows the model is
# using PRIOR seasons only (should show natural scatter/lag, not identical values)
p2 <- ggplot(coach %>% filter(coach_n_prior_seasons > 0),
             aes(x = coach_proe_signature, y = proe_neutral)) +
  geom_point(aes(color = factor(new_oc)), alpha = 0.6, size = 1.8) +
  geom_abline(slope = 1, intercept = 0, linetype = "dashed", color = "#4a6a80") +
  scale_color_manual(values = c("0" = "#4fc3f7", "1" = "#ef5350"),
                      labels = c("0" = "Continuing OC", "1" = "New OC")) +
  labs(title = "Coach Career Signature vs Actual Team PROE",
       subtitle = "Signature = OC's PRIOR-season history (no leakage) | dashed = perfect prediction",
       x = "Coach career PROE signature (prior seasons)", y = "Actual team PROE this season",
       color = NULL) +
  navy

# Panel 3: confidence distribution of coaching staff data by team (spot-check guide)
p3_data <- staff %>%
  count(team, confidence) %>%
  mutate(confidence = factor(confidence, levels = c("low","medium","high")))
p3 <- ggplot(p3_data, aes(x = team, y = n, fill = confidence)) +
  geom_col() +
  scale_fill_manual(values = c(high = "#4fc3f7", medium = "#ffa726", low = "#ef5350")) +
  labs(title = "Coaching Staff Data Confidence by Team (2018-2025)",
       subtitle = "Red segments = seasons flagged for manual spot-check before trusting coach signature",
       x = NULL, y = "# seasons (of 8)", fill = "Confidence") +
  navy +
  theme(axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 6))

# Panel 4: target share distribution by position, 2024 - sanity check the merge
p4_data <- feat %>% filter(season == 2024, position %in% c("WR","RB","TE"), targets >= 20)
p4 <- ggplot(p4_data, aes(x = target_share, fill = position)) +
  geom_density(alpha = 0.5) +
  scale_fill_manual(values = c(WR = "#69f0ae", RB = "#ef5350", TE = "#ffa726")) +
  labs(title = "2024 Target Share Distribution by Position",
       subtitle = "Min 20 targets | sanity check: WR right-shifted vs RB as expected",
       x = "Season target share", y = "Density", fill = "Position") +
  navy

layout <- (p1 | p3) / p2 / p4
final <- layout +
  plot_annotation(
    caption = sprintf("NFL Moneyball Phase 1 | %d player-seasons, %d team-seasons, coach data confidence: %d high / %d medium / %d low",
                       nrow(feat), nrow(coach), sum(staff$confidence=="high"), sum(staff$confidence=="medium"), sum(staff$confidence=="low"))
  ) &
  theme(plot.background = element_rect(fill = "#02233F", color = NA))

ggsave(OUT_PATH, final, width = 13, height = 16, dpi = 150, bg = "#02233F")
cat("Wrote:", OUT_PATH, "\n")
