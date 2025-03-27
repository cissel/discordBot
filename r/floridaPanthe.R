# floridaPanth.R by JHCV

##### Required Packages #####

library(tidyverse)
library(hockeyR)
library(sportyR)
library(ggimage)
library(magick)

#####

# Load pbp data
nhl24 <- hockeyR::load_pbp(2024)

# get single game
cup <- nhl24 %>%
  filter(game_date == "2024-06-24" & home_abbr == "FLA")

# grab team logos & colors
team_logos <- hockeyR::team_logos_colors %>%
  filter(team_abbr == unique(cup$home_abbr) | team_abbr == unique(cup$away_abbr)) %>%
  # add in dummy variables to put logos on the ice
  mutate(x = ifelse(full_team_name == unique(cup$home_name), 50, -50),
         y = 0)

# add transparency to logo
transparent <- function(img) {
  magick::image_fx(img, expression = "0.3*a", channel = "alpha")
}

# get only shot events
fenwick_events <- c("MISSED_SHOT","SHOT","GOAL")

shots <- cup %>% filter(event_type %in% fenwick_events) %>%
  # adding team colors
  left_join(team_logos, by = c("event_team_abbr" = "team_abbr"))

# create shot plot
catsCup <- geom_hockey("nhl") +
  ggimage::geom_image(
    data = team_logos,
    aes(x = x, y = y, image = team_logo_espn),
    image_fun = transparent, size = 0.22, asp = 2.35
  ) +
  geom_point(
    data = shots,
    aes(x_fixed, y_fixed),
    size = 6,
    color = shots$team_color1,
    shape = ifelse(shots$event_type == "GOAL", 19, 1)
  ) +
  labs(
    title = glue::glue("{unique(cup$away_name)} @ {unique(cup$home_name)}"),
    subtitle = glue::glue(
      "{unique(cup$game_date)}\n
    {unique(shots$away_abbr)} {unique(shots$away_final)} - {unique(shots$home_final)} {unique(shots$home_abbr)}"
    ),
    caption = "data from hockeyR | plot made with sportyR | JHCV"
  ) +
  theme(
    plot.title = element_text(hjust = 0.5),
    plot.subtitle = element_text(hjust = 0.5),
    plot.caption = element_text(hjust = .9)
  )

ggsave("C:/Users/james/projects/discordBot/outputs/sports/nhl/catsWin.png", plot = catsCup, width = 10, height = 6, dpi = 300, bg = "white")

