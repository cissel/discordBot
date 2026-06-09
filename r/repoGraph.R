# repoGraph.R by JHCV
# Plots discordBot repo activity over time:
#   Top panel  - cumulative lines of code (filled area)
#   Bottom panel - weekly commit count (bars)

library(ggplot2)
library(dplyr)
library(scales)
library(grid)

##### Theme #####

myTheme <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#274066"),
  panel.grid.minor  = element_blank(),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white", size = 9),
  axis.title        = element_text(color = "white", size = 10),
  plot.title        = element_text(color = "white", hjust = 0.5, size = 14, face = "bold"),
  plot.subtitle     = element_text(color = "#8BAAC8", hjust = 0.5, size = 10),
  plot.caption      = element_text(color = "#8BAAC8", size = 8),
  strip.background  = element_rect(fill = "#02233F", color = NA),
  strip.text        = element_text(color = "white"),
  plot.margin       = margin(12, 16, 8, 16)
)

##### Load Data #####

csv_path <- path.expand("~/discordBot/outputs/server/repo_graph.csv")
if (!file.exists(csv_path)) stop("repo_graph.csv not found - run repoGraph.py first")

df <- read.csv(csv_path, stringsAsFactors = FALSE) %>%
  mutate(week = as.Date(week))

##### Summary stats for subtitle #####

total_commits <- sum(df$commits)
total_loc     <- max(df$cumulative_loc)
total_weeks   <- nrow(df)
date_range    <- paste0(format(min(df$week), "%b %Y"), " - ", format(max(df$week), "%b %Y"))

##### Panel 1 - Cumulative LOC area chart #####

p1 <- ggplot(df, aes(x = week, y = cumulative_loc)) +
  geom_area(fill = "#00BFFF", alpha = 0.25) +
  geom_line(color = "#00BFFF", linewidth = 1.1) +
  scale_x_date(date_breaks = "2 months", date_labels = "%b '%y", expand = c(0.01, 0)) +
  scale_y_continuous(labels = comma_format(), expand = c(0, 0)) +
  labs(
    title    = "discordBot - Repo Growth Over Time",
    subtitle = paste0(date_range, "  |  ", total_commits, " commits  |  ", comma(total_loc), " net lines"),
    x        = NULL,
    y        = "Cumulative Lines of Code",
    caption  = NULL
  ) +
  myTheme +
  theme(
    axis.text.x  = element_blank(),
    axis.ticks.x = element_blank(),
    plot.margin  = margin(12, 16, 2, 16)
  )

##### Panel 2 - Weekly commit bars #####

avg_commits <- mean(df$commits[df$commits > 0])

p2 <- ggplot(df, aes(x = week, y = commits)) +
  geom_col(fill = "#4A90D9", width = 5) +
  geom_hline(yintercept = avg_commits, color = "#FFD700", linetype = "dashed", linewidth = 0.7) +
  annotate("text",
           x     = max(df$week),
           y     = avg_commits + 0.3,
           label = paste0("avg ", round(avg_commits, 1), "/wk"),
           color = "#FFD700",
           size  = 2.8,
           hjust = 1) +
  scale_x_date(date_breaks = "2 months", date_labels = "%b '%y", expand = c(0.01, 0)) +
  scale_y_continuous(breaks = function(x) unique(floor(pretty(x))), expand = c(0, 0.2)) +
  labs(
    x       = NULL,
    y       = "Commits / Week",
    caption = "Source: git log | JHCV"
  ) +
  myTheme +
  theme(plot.margin = margin(2, 16, 10, 16))

##### Combine with grid #####

out_path <- path.expand("~/discordBot/outputs/server/repo_graph.png")

png(out_path, width = 1200, height = 700, res = 120, bg = "#02233F")

grid.newpage()
pushViewport(viewport(layout = grid.layout(2, 1, heights = unit(c(2, 1), "null"))))

print(p1, vp = viewport(layout.pos.row = 1, layout.pos.col = 1))
print(p2, vp = viewport(layout.pos.row = 2, layout.pos.col = 1))

dev.off()

cat("ok:", out_path, "\n")
