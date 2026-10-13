# jagsOpponent.R by JHCV
# Finds the opponent from the most recently COMPLETED Jaguars game
# (used by /jags win to say "smoking on [opponent] pack")
#
# NOTE (fixed 2026-10-11): previously used nflreadr::load_schedules() /
# nflfastR::fast_scraper_schedules(), which pull from the nflverse-data
# GitHub release snapshot. That snapshot is only refreshed periodically and
# can lag several hours behind a game going final - e.g. on 2026-10-11 the
# Jags beat the Eagles in London (9:30am ET kickoff, final ~12:30pm ET) but
# /jags win run at 1:15pm ET still saw last week's Bengals game as the most
# recent "completed" one because nflverse-data hadn't synced yet. Switched
# to ESPN's live team-schedule API, which reflects final scores immediately.

##### Required Packages #####

library(tidyverse)
library(httr)
library(jsonlite)

#####

out_path <- "~/discordBot/outputs/sports/nfl/jagsOpponent.csv"

write_empty <- function() {
  write_csv(tibble(opponent_abbr = NA, opponent_nick = NA, gameday = NA), out_path)
}

result <- tryCatch({
  resp <- GET(
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/jax/schedule",
    timeout(10)
  )
  stop_for_status(resp)
  d <- content(resp, as = "text", encoding = "UTF-8") |> fromJSON(flatten = TRUE)
  ev <- d$events

  if (is.null(ev) || nrow(ev) == 0) {
    write_empty()
  } else {
    # `competitions` is a list-column (one single-row data frame per event);
    # status.type.completed lives inside each of those, not at the top level
    is_completed <- vapply(ev$competitions, function(cp) {
      isTRUE(cp$status.type.completed[1])
    }, logical(1))
    completed_idx <- which(is_completed)

    if (length(completed_idx) == 0) {
      write_empty()
    } else {
      # most recent completed game by kickoff date
      ev$gameDate <- as.Date(ev$date)
      last_idx <- completed_idx[which.max(ev$gameDate[completed_idx])]

      competitors <- ev$competitions[[last_idx]]$competitors[[1]]
      opp_row <- competitors[competitors$team.abbreviation != "JAX", ]

      out <- tibble(
        opponent_abbr = opp_row$team.abbreviation[1],
        opponent_nick = opp_row$team.nickname[1],
        gameday = as.character(ev$gameDate[last_idx])
      )
      write_csv(out, out_path)
    }
  }
  TRUE
}, error = function(e) {
  message("jagsOpponent.R failed: ", conditionMessage(e))
  # degrade gracefully rather than crashing the bot command, but only
  # overwrite with an empty marker if there's no existing (possibly still
  # valid) csv to fall back on
  if (!file.exists(path.expand(out_path))) write_empty()
  FALSE
})
