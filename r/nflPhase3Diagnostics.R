#!/usr/bin/env Rscript
# nflPhase3Diagnostics.R
# Navy 4-panel diagnostic plot for the Phase 3 VORP/ADP inefficiency layer.
suppressMessages({
  library(tidyverse)
  library(ggplot2)
  library(patchwork)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
OUT_PATH <- file.path(OUT_DIR, "phase3_vorp_diagnostics.png")

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

vorp <- read_csv(file.path(OUT_DIR, "vorp_2026.csv"), show_col_types = FALSE)
draftable <- read_csv(file.path(OUT_DIR, "vorp_2026_draftable.csv"), show_col_types = FALSE)

# Panel 1: VORP vs ADP scatter, draftable range only - the core moneyball chart
p1 <- ggplot(draftable, aes(x = adp_overall, y = vorp, color = position)) +
  geom_point(aes(size = injury_shortened_2025, shape = player_type), alpha = 0.75) +
  geom_smooth(method = "loess", se = FALSE, color = "white", linewidth = 0.6) +
  scale_color_manual(values = POS_COLORS) +
  scale_size_manual(values = c(`FALSE` = 1.6, `TRUE` = 3.2),
                     labels = c(`FALSE` = "Full 2025 season", `TRUE` = "2025 injury-shortened (<12 gm)")) +
  scale_shape_manual(values = c(veteran = 16, rookie = 17)) +
  labs(title = "VORP vs ADP (Draft-Relevant Range, ADP \u2264 200)",
       subtitle = "Above white trend line = market undervalues | triangles = rookies (separate, lower-confidence model)",
       x = "Average Draft Position (overall)", y = "VORP (vs Room 40 replacement level)",
       color = "Position", size = NULL, shape = "Player type") +
  navy

# Panel 2: top targets and fades bar chart
top_n <- 10
targets <- draftable %>% slice_max(value_gap, n = top_n) %>% mutate(type = "Draft Target")
fades   <- draftable %>% slice_min(value_gap, n = top_n) %>% mutate(type = "Fade Candidate")
combined <- bind_rows(targets, fades)
p2 <- ggplot(combined, aes(x = reorder(full_name, value_gap), y = value_gap, fill = type)) +
  geom_col() +
  coord_flip() +
  scale_fill_manual(values = c("Draft Target" = "#69f0ae", "Fade Candidate" = "#ef5350")) +
  labs(title = "Top Draft Targets & Fade Candidates (ADP \u2264 200)",
       subtitle = "Value gap = ADP rank minus VORP rank | positive = ADP undervalues, negative = ADP overvalues",
       x = NULL, y = "Value gap (ADP rank - VORP rank)", fill = NULL) +
  navy

# Panel 3: replacement level by position (context for VORP calc)
repl <- vorp %>% distinct(position, replacement_value) %>% arrange(desc(replacement_value))
p3 <- ggplot(repl, aes(x = reorder(position, replacement_value), y = replacement_value, fill = position)) +
  geom_col() +
  scale_fill_manual(values = POS_COLORS) +
  coord_flip() +
  labs(title = "Replacement Level by Position",
       subtitle = "Room 40 roster math: 12 teams x 1QB/2RB/2WR/1TE + shared FLEX (55% RB / 40% WR / 5% TE alloc)",
       x = NULL, y = "Replacement-level projected points", fill = "Position") +
  navy

# Panel 4: raw vs pace-adjusted points for injury-shortened players - shows the
# known limitation transparently rather than hiding it
inj <- draftable %>% filter(injury_shortened_2025, player_type == "veteran") %>%
  select(full_name, position, lag1_room40_pts, lag1_pace_adj_pts) %>%
  pivot_longer(cols = c(lag1_room40_pts, lag1_pace_adj_pts), names_to = "metric", values_to = "pts") %>%
  mutate(metric = recode(metric, lag1_room40_pts = "2025 actual (injury-shortened)",
                          lag1_pace_adj_pts = "2025 healthy 17-game pace"))
p4 <- ggplot(inj, aes(x = reorder(full_name, pts), y = pts, fill = metric)) +
  geom_col(position = "dodge") +
  coord_flip() +
  scale_fill_manual(values = c("2025 actual (injury-shortened)" = "#ef5350",
                                "2025 healthy 17-game pace" = "#4fc3f7")) +
  labs(title = "Injury-Shortened 2025: Actual vs Pace-Adjusted",
       subtitle = "Can't fully separate injury from decline",
       x = NULL, y = "Room 40 points", fill = NULL) +
  navy +
  theme(axis.text.y = element_text(size = 7))

layout <- p1 / p2 / (p3 | p4)
final <- layout +
  plot_annotation(
    caption = sprintf("NFL Moneyball Phase 3 | %d draftable players (ADP<=200) | replacement level from Room 40's actual roster math",
                       nrow(draftable))
  ) &
  theme(plot.background = element_rect(fill = "#02233F", color = NA))

ggsave(OUT_PATH, final, width = 14, height = 20, dpi = 150, bg = "#02233F")
cat("Wrote:", OUT_PATH, "\n")
