#  voiceHoursOverTime.R by JHCV
#  Pairs raw join/leave events (python/main.py on_voice_state_update ->
#  outputs/metrics/voice_events.csv) into sessions and plots cumulative
#  voice channel hours per user over time. Styled to match the other
#  /history over time plots (userHistory.R, channelHistory.R) - plain
#  lines, no legend box, end-of-line labels instead.

##### Required Packages #####

library(tidyverse)
library(lubridate)

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

##### Import Data #####

setwd("~/discordBot")

df <- read_csv("outputs/metrics/voice_events.csv", show_col_types = FALSE) |>
  mutate(ts = ymd_hms(datetime, tz = "UTC")) |>
  arrange(user_name, ts)

if (nrow(df) == 0) {
  stop("no voice events logged yet")
}

#####

##### Pair join/leave events into sessions #####
# Same stack-based pairing logic as voiceHours.R - see that file for the
# full rationale on channel switches / bot restarts / unmatched events.

sessions <- list()
open_join <- list()

for (i in seq_len(nrow(df))) {
  u <- df$user_name[i]
  e <- df$event[i]
  t <- df$ts[i]

  if (e == "join") {
    open_join[[u]] <- t
  } else if (e == "leave") {
    if (!is.null(open_join[[u]])) {
      sessions[[length(sessions) + 1]] <- data.frame(
        user_name = u,
        start = open_join[[u]],
        end = t
      )
      open_join[[u]] <- NULL
    }
  }
}

now_utc <- with_tz(Sys.time(), tzone = "UTC")
for (u in names(open_join)) {
  sessions[[length(sessions) + 1]] <- data.frame(
    user_name = u,
    start = open_join[[u]],
    end = now_utc
  )
}

sess_df <- bind_rows(sessions) |>
  mutate(hours = as.numeric(difftime(end, start, units = "hours"))) |>
  filter(hours > 0, hours < 24)

#####

##### Cumulative hours per user #####
# A session is bucketed to the calendar day it started on. Sessions that
# cross midnight are rare (filtered to <24h above) and not split across
# days - close enough for a trend view, not meant to be to-the-minute.
# Running total per user so the line only ever goes up.

vh <- sess_df |>
  mutate(date = as_date(start)) |>
  group_by(user_name, date) |>
  summarize(n = sum(hours), .groups = "drop_last") |>  # keep grouping by user
  arrange(user_name, date) |>
  mutate(tot = cumsum(n)) |>
  ungroup()

# Create label dataframe: last point per user
label_df <- vh |>
  group_by(user_name) |>
  filter(date == max(date)) |>
  ungroup()

vhp <- ggplot(vh,
              aes(x = date,
                  y = tot,
                  color = user_name)) +

  geom_line() +

  geom_text(data = label_df,
            mapping = aes(x = date,
                          y = tot,
                          label = paste(user_name,
                                        ": ",
                                        sprintf("%.1fh", tot),
                                        sep = ""),
                          color = user_name),
            hjust = 0.75,
            size = 3) +

  labs(x = "Date",
       y = "Cumulative Voice Hours",
       title = "Room 40 Voice Channel Hours Over Time",
       subtitle = paste(nrow(sess_df),
                        "sessions as of",
                        max(vh$date),
                        sep = " "),
       caption = "Source: Discord voice event log | JHCV") +

  myTheme

ggsave("~/discordBot/outputs/metrics/voiceHoursOverTime.png",
       plot = vhp,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")

#####
