#targetShare.R by JHCV

##### Required packages #####

library(tidyverse)
library(lubridate)
library(plotly)
library(timetk)
library(fflr)
library(ggimage)
library(ggthemes)
library(nflfastR)
library(httr)
library(rvest)
library(wdman)
library(tidytext)

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

# Load play by play for entire NFL szn
nfl24 <- load_pbp(2024)

# Filter for passing plays and plays that have a target (pass attempts)
passing_plays <- nfl24 |>
  filter(play_type == "pass" & !is.na(receiver_player_name))

# Create a new dataframe that summarizes target share for each receiver
target_share_df <- passing_plays |>
  # Group by team and receiver name
  group_by(posteam, receiver_player_name) |>
  # Calculate the number of targets per receiver
  summarise(targets = n()) |>
  ungroup() |>
  # Calculate total passing plays for each team
  left_join(
    passing_plays |>
      group_by(posteam) |>
      summarise(total_team_passes = n()),
    by = "posteam"
  ) |>
  # Calculate target share as a percentage
  mutate(target_share = (targets / total_team_passes)) |>
  # Select relevant columns and rename them
  select(Team = posteam, Player_Name = receiver_player_name, Target_Share = target_share) |>
  arrange(Team, desc(Target_Share))

# Filter to get top 5 receivers per team by target share
top_5_receivers_df <- target_share_df |>
  group_by(Team) |>
  slice_max(order_by = Target_Share, n = 5) |>
  ungroup()

# Create a ranking column for each player (1 = most targeted, 5 = least targeted)
top_5_receivers_df <- top_5_receivers_df |>
  group_by(Team) |>
  arrange(desc(Target_Share)) |>  # Ensure players are sorted by descending Target_Share
  mutate(Rank = row_number()) |>  # Assign ranks (1-5) based on the sorted Target_Share
  ungroup()

# Plot the data using Rank on the x-axis
tgtShrPlot <- ggplot(top_5_receivers_df,
                     aes(x = as.factor(Rank),  # Use Rank as x-axis
                         y = Target_Share, 
                         fill = Target_Share,
                         color = Player_Name)) +
  geom_bar(stat = "identity") +  # Remove outlines and adjust bar width
  geom_text(aes(y = Target_Share+.01,
                label = Player_Name),
            color = "white",
            size = 1.25) +
  facet_wrap(~ Team, scales = "free_y", nrow = 4) +  # Free y-axis scale for different team distributions
  scale_fill_gradient("low" = "red",
                      "high" = "green") +
  scale_y_continuous(labels = scales::percent) +
  myTheme +
  theme(axis.text.x = element_text(angle = 45, hjust = 1),  # Rotate x-axis labels for readability
        panel.grid.major.x = element_blank()) +
  labs(x = "Receiver Rank",  # Label x-axis as Receiver Rank
       y = "Target Share", 
       subtitle = "2024 Season",
       caption = "JHCV",
       title = "NFL WR Target Share by Team") 

# Interactive plot
#ggplotly(tgtShrPlot)

ggsave("/Users/jamescissel/discordBot/outputs/sports/nfl/tgtShr.png",
       plot = tgtShrPlot,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")
