# voiceHours.R by JHCV
# Pairs raw join/leave events (python/main.py on_voice_state_update ->
# outputs/metrics/voice_events.csv) into sessions and plots total voice
# channel hours per user.

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
# Stack-based per user: a channel switch logs leave(old)+join(new) at the
# same instant, which closes and immediately reopens a session - correct,
# since we're measuring total time connected to ANY voice channel, not
# per-channel time. Bot restarts emit a synthetic leave (on close()) and a
# synthetic join (on_ready) for everyone already connected, so sessions
# spanning a restart still pair correctly instead of leaking an unmatched
# join. An unmatched trailing join (bot crashed without a clean close()) is
# closed off "now" so that time still counts.

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
    # unmatched leave (e.g. bot was offline for the join) - nothing to pair, skip
  }
}

# close off anyone still "open" (bot currently running, session in progress)
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
  filter(hours > 0, hours < 24)  # drop negative/garbage and >24h outlier sessions

#####

##### Total hours per user #####

totals <- sess_df |>
  group_by(user_name) |>
  summarize(total_hours = sum(hours), sessions = n(), .groups = "drop") |>
  arrange(desc(total_hours)) |>
  mutate(user_name = factor(user_name, levels = rev(user_name)))

vp <- ggplot(totals, aes(x = user_name, y = total_hours)) +

  geom_col(fill = "#2E7BE0") +

  geom_text(aes(label = sprintf("%.1fh", total_hours)),
            hjust = -0.1, color = "white", size = 3.5) +

  coord_flip(clip = "off") +

  labs(x = NULL,
       y = "Total Voice Hours",
       title = "Room 40 Voice Channel Hours",
       subtitle = paste(sum(totals$sessions), "sessions logged since tracking began"),
       caption = "Source: Discord voice event log | JHCV") +

  expand_limits(y = max(totals$total_hours) * 1.15) +

  myTheme

ggsave("~/discordBot/outputs/metrics/voiceHours.png",
       plot = vp,
       width = 10,
       height = max(4, 0.6 * nrow(totals) + 2),
       dpi = 300,
       bg = "transparent")

#####
