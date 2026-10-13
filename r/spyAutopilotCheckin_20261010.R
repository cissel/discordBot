#!/usr/bin/env Rscript
# SPY autopilot check-in, Oct 10 2026 — equity curve + direction accuracy
# since the Aug 3 restart (post data-freeze bug fixes).

suppressMessages({
  library(dplyr)
  library(ggplot2)
  library(scales)
  library(tidyr)
})

# ---- navy theme ----
bg <- "#02233F"; grid_col <- "#274066"
col_qb <- "#7B4CE0"  # reuse palette: purple=strategy
col_rb <- "#2E9E4E"  # green=B&H
col_wr <- "#2E7BE0"  # blue=accent
col_te <- "#E0A02E"  # gold=markers

tw <- function(base_size = 12) {
  theme_minimal(base_size = base_size) %+replace% theme(
    plot.background  = element_rect(fill = bg, color = NA),
    panel.background = element_rect(fill = bg, color = NA),
    panel.grid.major = element_line(color = grid_col, linewidth = 0.3),
    panel.grid.minor = element_blank(),
    axis.text   = element_text(color = "white"),
    axis.title  = element_text(color = "white"),
    plot.title    = element_text(color = "white", face = "bold", size = base_size + 3),
    plot.subtitle = element_text(color = "white", size = base_size - 1),
    plot.caption  = element_text(color = "white", size = base_size - 3),
    legend.background = element_rect(fill = bg, color = NA),
    legend.text  = element_text(color = "white"),
    legend.title = element_text(color = "white"),
    strip.background = element_rect(fill = "#0a2d4d", color = NA),
    strip.text = element_text(color = "white", face = "bold")
  )
}

df <- read.csv("~/discordBot/outputs/markets/spy_trade_log.csv", stringsAsFactors = FALSE)
df$date <- as.Date(df$date)

restart_date <- as.Date("2026-08-03")
live <- df %>% filter(date >= restart_date) %>% arrange(date)

live <- live %>%
  mutate(
    strat_equity = equity,
    bh_shares    = first(equity) / first(spy_price),
    bh_equity    = bh_shares * spy_price
  )

long <- live %>%
  select(date, strat_equity, bh_equity) %>%
  pivot_longer(c(strat_equity, bh_equity), names_to = "series", values_to = "value") %>%
  mutate(series = recode(series, strat_equity = "SPY Autopilot (live paper)", bh_equity = "SPY Buy & Hold"))

p1 <- ggplot(long, aes(x = date, y = value, color = series)) +
  geom_line(linewidth = 1.1) +
  scale_color_manual(values = c("SPY Autopilot (live paper)" = col_wr, "SPY Buy & Hold" = col_rb)) +
  scale_y_continuous(labels = dollar_format()) +
  labs(
    title = "SPY Autopilot vs Buy & Hold — Equity Curve",
    subtitle = sprintf("Live paper trading since Aug 3, 2026 restart (post data-freeze fixes) | n=%d trading days",
                        nrow(live)),
    x = NULL, y = "Equity ($)", color = NULL,
    caption = "Eval clock restarted Aug 3'26 after cross-asset cache + sector feature freeze bugs were fixed."
  ) +
  tw() + theme(legend.position = "top")

# Rolling direction accuracy (20-day window)
live$realized_up <- c(diff(live$spy_price) > 0, NA)
live$signal_up <- live$signal_direction == "UP"
live$hit <- live$signal_up == live$realized_up

roll_acc <- sapply(seq_len(nrow(live)), function(i) {
  lo <- max(1, i - 19)
  vals <- live$hit[lo:i]
  vals <- vals[!is.na(vals)]
  if (length(vals) < 5) return(NA)
  mean(vals)
})
live$roll_acc20 <- roll_acc

p2 <- ggplot(live, aes(x = date, y = roll_acc20)) +
  geom_hline(yintercept = 0.5, color = "white", linetype = "dashed", linewidth = 0.5) +
  geom_line(color = col_te, linewidth = 1.1, na.rm = TRUE) +
  geom_point(color = col_te, size = 1.8, na.rm = TRUE) +
  scale_y_continuous(labels = percent_format(), limits = c(0, 1)) +
  labs(
    title = "Rolling 20-Day Direction Accuracy",
    subtitle = "Dashed line = coinflip (50%). Min 5 obs required per window.",
    x = NULL, y = "Accuracy"
  ) +
  tw()

out_path <- "~/discordBot/outputs/markets/spy_autopilot_checkin_20261010.png"
png(path.expand(out_path), width = 1400, height = 1400, res = 150, bg = bg)
gridExtra::grid.arrange(p1, p2, ncol = 1, heights = c(1.1, 1))
dev.off()

cat("Saved:", out_path, "\n")
