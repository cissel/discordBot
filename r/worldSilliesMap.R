# worldSilliesMap.R
# World Sillies fantasy baseball PF vs PA scatter map
# Modeled on room40map.R by JHCV

library(tidyverse)
library(lubridate)
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

myLegend <- theme(
  legend.position    = "right",
  legend.background  = element_rect(fill = "#02233F"),
  legend.text        = element_text(color = "white"),
  legend.title       = element_text(color = "white")
)

#####

##### Load data #####

df <- read_csv("outputs/sports/mlb/fantasy/map.csv",
               show_col_types = FALSE)

#####

##### Derived values #####

mean_pf <- mean(df$pf)
mean_pa <- mean(df$pa)
mean_wins <- mean(df$wins)

# Quadrant labels
q_labels <- tibble(
  x    = c(mean_pa - (mean_pa - min(df$pa)) * 0.45,
           mean_pa + (max(df$pa) - mean_pa) * 0.45,
           mean_pa - (mean_pa - min(df$pa)) * 0.45,
           mean_pa + (max(df$pa) - mean_pa) * 0.45),
  y    = c(mean_pf + (max(df$pf) - mean_pf) * 0.42,
           mean_pf + (max(df$pf) - mean_pf) * 0.42,
           mean_pf - (mean_pf - min(df$pf)) * 0.42,
           mean_pf - (mean_pf - min(df$pf)) * 0.42),
  label = c("Good but unlucky", "Dominant", "Bad and unlucky", "Lucky")
)

#####

##### Plot #####

p <- ggplot(df, aes(x = pa, y = pf)) +

  # Quadrant reference lines
  geom_vline(xintercept = mean_pa, color = "white", alpha = 0.35, linewidth = 0.6) +
  geom_hline(yintercept = mean_pf, color = "white", alpha = 0.35, linewidth = 0.6) +

  # Diagonal equality line
  geom_abline(slope = 1, intercept = 0, color = "white", alpha = 0.2,
              linewidth = 0.5, linetype = "dashed") +

  # Quadrant shading
  annotate("rect",
           xmin = -Inf, xmax = mean_pa,
           ymin = mean_pf, ymax = Inf,
           fill = "#1a4a2e", alpha = 0.18) +
  annotate("rect",
           xmin = mean_pa, xmax = Inf,
           ymin = mean_pf, ymax = Inf,
           fill = "#1a4a2e", alpha = 0.35) +
  annotate("rect",
           xmin = -Inf, xmax = mean_pa,
           ymin = -Inf, ymax = mean_pf,
           fill = "#4a1a1a", alpha = 0.25) +
  annotate("rect",
           xmin = mean_pa, xmax = Inf,
           ymin = -Inf, ymax = mean_pf,
           fill = "#4a1a1a", alpha = 0.12) +

  # Quadrant labels
  geom_text(data = q_labels,
            aes(x = x, y = y, label = label),
            color = "white", alpha = 0.22, size = 3.8,
            fontface = "italic", inherit.aes = FALSE) +

  # Points colored by wins
  geom_point(aes(color = wins), size = 4, alpha = 0.85) +

  # Team name labels with repel to avoid overlap
  geom_label_repel(aes(label = team_name, color = wins),
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
                        midpoint = mean_wins,
                        name     = "Wins") +

  labs(
    title    = "World Sillies - Fantasy Map",
    subtitle = paste0("Points For vs Points Against  (incl. live Week ", df$current_week[1], ")"),
    x        = "Points Against",
    y        = "Points For",
    caption  = "Source: ESPN Fantasy API | JHCV"
  ) +

  myTheme +
  myLegend

#####

##### Save #####

ggsave("outputs/sports/mlb/fantasy/fantasyMap.png",
       plot   = p,
       width  = 10,
       height = 9,
       dpi    = 300,
       bg     = "#02233F")

cat("Saved: outputs/sports/mlb/fantasy/fantasyMap.png\n")

#####
