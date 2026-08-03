# worldSilliesRisk.R
# World Sillies risk/volatility plot: mean daily points vs std dev
# Navy theme matching the rest of the bot

library(tidyverse)
library(ggrepel)

setwd("~/discordBot")

##### Plot Appearance Theme #####

myTheme <- theme(
  legend.position    = "none",
  plot.background    = element_rect(fill = "#02233F", color = NA),
  panel.background   = element_rect(fill = "#02233F", color = NA),
  panel.grid.major   = element_line(color = "#274066"),
  panel.grid.minor   = element_line(color = "#1a3255"),
  axis.ticks         = element_line(color = "#274066"),
  axis.text          = element_text(color = "white", size = 11),
  axis.title         = element_text(color = "white", size = 13),
  plot.title         = element_text(color = "white", hjust = 0.5, size = 16, face = "bold"),
  plot.subtitle      = element_text(color = "#8ab4d4", hjust = 0.5, size = 11),
  plot.caption       = element_text(color = "#8ab4d4", size = 9),
  plot.margin        = margin(16, 16, 12, 16)
)

#####

##### Load data #####

df <- read_csv("outputs/sports/mlb/fantasy/risk.csv",
               show_col_types = FALSE)

#####

##### Derived values #####

mean_daily_pts <- mean(df$mean_daily)
mean_std_pts <- mean(df$std_daily)

#####

##### Plot #####

p <- ggplot(df, aes(x = std_daily, y = mean_daily)) +

  # Reference lines at means
  geom_vline(xintercept = mean_std_pts, color = "white", alpha = 0.35, linewidth = 0.6) +
  geom_hline(yintercept = mean_daily_pts, color = "white", alpha = 0.35, linewidth = 0.6) +

  # Quadrant shading
  # Low vol, high mean = consistent performer
  annotate("rect",
           xmin = -Inf, xmax = mean_std_pts,
           ymin = mean_daily_pts, ymax = Inf,
           fill = "#1a4a2e", alpha = 0.18) +
  # High vol, high mean = high risk, high reward
  annotate("rect",
           xmin = mean_std_pts, xmax = Inf,
           ymin = mean_daily_pts, ymax = Inf,
           fill = "#1a4a2e", alpha = 0.35) +
  # Low vol, low mean = low scoring, stable
  annotate("rect",
           xmin = -Inf, xmax = mean_std_pts,
           ymin = -Inf, ymax = mean_daily_pts,
           fill = "#4a1a1a", alpha = 0.25) +
  # High vol, low mean = volatile underperformer
  annotate("rect",
           xmin = mean_std_pts, xmax = Inf,
           ymin = -Inf, ymax = mean_daily_pts,
           fill = "#4a1a1a", alpha = 0.12) +

  # Points with gradient coloring by mean daily
  geom_point(aes(color = mean_daily), size = 4, alpha = 0.85) +

  # Team name labels with repel
  geom_label_repel(aes(label = team_name, color = mean_daily),
                   fill       = "#02233F",
                   size       = 3.6,
                   fontface   = "bold",
                   label.padding = unit(0.25, "lines"),
                   label.r    = unit(0.15, "lines"),
                   label.size = 0.3,
                   max.overlaps = Inf,
                   box.padding = 0.5,
                   seed       = 42) +

  scale_color_gradient2(low      = "#e05252",
                        mid      = "white",
                        high     = "#52c27a",
                        midpoint = mean_daily_pts,
                        name     = "Mean Daily Pts") +

  labs(
    title    = "World Sillies - Risk Analysis",
    subtitle = "Mean Daily Points vs Volatility (Std Dev)",
    x        = "Volatility (Std Dev of Daily Points)",
    y        = "Mean Daily Points",
    caption  = "Source: ESPN Fantasy API | JHCV"
  ) +

  myTheme

#####

##### Save #####

ggsave("outputs/sports/mlb/fantasy/fantasyRisk.png",
       plot   = p,
       width  = 10,
       height = 9,
       dpi    = 300,
       bg     = "#02233F")

cat("Saved: outputs/sports/mlb/fantasy/fantasyRisk.png\n")

#####
