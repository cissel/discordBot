# jagsOpponent.R by JHCV
# Finds the opponent from the most recently COMPLETED Jaguars game
# (used by /jags win to say "smoking on [opponent] pack")

##### Required Packages #####

library(tidyverse)
library(nflfastR)
library(nflreadr)

#####

# pull schedule
sched <- fast_scraper_schedules(2026)

# filter for jags
jax <- sched |> subset(home_team == "JAX" | away_team == "JAX")

# filter for games that have already been played (scores populated)
jaxPlayed <- jax |> subset(!is.na(home_score) & !is.na(away_score))

if (nrow(jaxPlayed) == 0) {
  # no completed games yet this season - write an empty marker so the
  # bot side can degrade gracefully instead of crashing
  write_csv(tibble(opponent_abbr = NA, opponent_nick = NA, gameday = NA),
            "~/discordBot/outputs/sports/nfl/jagsOpponent.csv")
} else {
  # most recent completed game (handles bye weeks automatically since
  # those never appear in the schedule as a JAX row)
  lastGame <- jaxPlayed |> subset(as.Date(gameday) == max(as.Date(gameday))) |> head(1)

  opponentAbbr <- ifelse(lastGame$home_team == "JAX", lastGame$away_team, lastGame$home_team)

  teams <- load_teams()
  opponentNick <- teams$team_nick[teams$team_abbr == opponentAbbr]

  out <- tibble(opponent_abbr = opponentAbbr, opponent_nick = opponentNick, gameday = lastGame$gameday)
  write_csv(out, "~/discordBot/outputs/sports/nfl/jagsOpponent.csv")
}
