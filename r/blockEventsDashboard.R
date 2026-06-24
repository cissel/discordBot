# blockEventsDashboard.R
# SPY block order event analysis - navy theme, 4 panels
# Usage: Rscript r/blockEventsDashboard.R [output_path]

for (pkg in c("ggplot2","patchwork","dplyr","tidyr","readr","scales","lubridate","ggrepel")) {
  if (!requireNamespace(pkg, quietly = TRUE))
    install.packages(pkg, repos = "https://cran.rstudio.com/", quiet = TRUE)
}

suppressPackageStartupMessages({
  library(ggplot2)
  library(patchwork)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(scales)
  library(lubridate)
  library(ggrepel)
})

args     <- commandArgs(trailingOnly = TRUE)
OUT_PATH <- if (length(args) >= 1) args[1] else
              path.expand("~/discordBot/outputs/markets/block_events_dashboard.png")
dir.create(dirname(OUT_PATH), recursive = TRUE, showWarnings = FALSE)

EVENTS_CSV   <- path.expand("~/discordBot/outputs/research/block_events.csv")
OUTCOMES_CSV <- path.expand("~/discordBot/outputs/research/block_outcomes.csv")

# ── colours ───────────────────────────────────────────────────────────────────
BG     <- "#02233F"
GRID   <- "#274066"
TXT    <- "white"
ACCENT <- "#4fc3f7"
GREEN  <- "#69f0ae"
RED    <- "#ef5350"
ORANGE <- "#ffa726"
YELLOW <- "#fff176"
PURPLE <- "#ce93d8"
TEAL   <- "#80cbc4"

# ── theme ─────────────────────────────────────────────────────────────────────
navy <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.4),
  panel.grid.minor  = element_line(color = "#1a3a5c", linewidth = 0.2),
  axis.text         = element_text(color = "#a0b8cc", size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = TXT,      size = 11, face = "bold", hjust = 0.5),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 8,  hjust = 0.5),
  plot.caption      = element_text(color = "#4a6a80", size = 7,  hjust = 0.5),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 8, face = "bold"),
  legend.background = element_rect(fill = BG),
  legend.text       = element_text(color = "#a0b8cc", size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = BG),
  plot.margin       = margin(8, 10, 8, 10)
)

# ── load data ─────────────────────────────────────────────────────────────────
ev <- read_csv(EVENTS_CSV,   show_col_types = FALSE) %>%
  mutate(
    trade_date    = as.Date(trade_date),
    dollar_bn     = dollar_value / 1e9,
    deviation_pct = deviation * 100,
    exchange_lbl  = case_when(
      exchange == "D" ~ "Dark Pool",
      exchange == "P" ~ "ADF / OTC",
      exchange == "M" ~ "FINRA",
      TRUE            ~ exchange
    ),
    week = floor_date(trade_date, "week")
  )

oc <- read_csv(OUTCOMES_CSV, show_col_types = FALSE) %>%
  mutate(trade_date = as.Date(trade_date))

# join
df <- ev %>% left_join(
  oc %>% select(trade_date, dollar_value, direction,
                reached_1d, reached_3d, reached_1w, reached_2w, reached_1mo),
  by = c("trade_date", "dollar_value"),
  relationship = "many-to-many"
) %>% distinct()

# ── Panel 1: event timeline - dot size = dollar value, colour = deviation ─────
# weekly event count as bar background, dots overlaid
weekly_counts <- df %>%
  count(week) %>%
  filter(!is.na(week))

# sample large events for labels
big_events <- df %>%
  filter(dollar_bn >= quantile(dollar_bn, 0.95, na.rm = TRUE)) %>%
  slice_max(dollar_bn, n = 8)

p1 <- ggplot() +
  geom_col(data = weekly_counts,
           aes(x = week, y = n),
           fill = "#1a3a5c", width = 5, alpha = 0.7) +
  geom_point(data = df,
             aes(x = trade_date, y = dollar_bn * 6,
                 color = deviation_pct, size = dollar_bn),
             alpha = 0.75) +
  geom_text_repel(data = big_events,
                  aes(x = trade_date, y = dollar_bn * 6,
                      label = paste0("$", round(dollar_bn, 1), "B")),
                  color = YELLOW, size = 2.5, max.overlaps = 6,
                  segment.color = "#4a6a80", box.padding = 0.3) +
  scale_color_gradient2(
    low = TEAL, mid = ORANGE, high = RED,
    midpoint = 1.2,
    name = "Deviation %",
    labels = function(x) paste0(round(x, 1), "%")
  ) +
  scale_size_continuous(range = c(1.5, 7), name = "Size ($B)",
                        labels = function(x) paste0("$", round(x, 1), "B")) +
  scale_y_continuous(
    name = "Events / week",
    sec.axis = sec_axis(~ . / 6, name = "Block size ($B)",
                        labels = function(x) paste0("$", round(x, 1)))
  ) +
  scale_x_date(date_labels = "%b %y", date_breaks = "2 months") +
  labs(title = "SPY Block Order Timeline",
       subtitle = paste0(nrow(df), " qualifying events | $300M+ threshold | >= 0.5% deviation"),
       x = NULL) +
  navy +
  theme(legend.position = "right",
        axis.text.x = element_text(angle = 30, hjust = 1))

# ── Panel 2: reach rate by forward horizon, split by direction ─────────────────
reach_long <- df %>%
  filter(!is.na(direction)) %>%
  select(direction, reached_1d, reached_3d, reached_1w, reached_2w, reached_1mo) %>%
  pivot_longer(-direction, names_to = "horizon", values_to = "reached") %>%
  group_by(direction, horizon) %>%
  summarise(
    rate  = mean(reached, na.rm = TRUE),
    n     = n(),
    se    = sqrt(rate * (1 - rate) / n),
    .groups = "drop"
  ) %>%
  mutate(
    horizon = factor(horizon,
                     levels = c("reached_1d","reached_3d","reached_1w","reached_2w","reached_1mo"),
                     labels = c("1D","3D","1W","2W","1M")),
    dir_lbl = ifelse(direction == "above_market", "Block Above Market", "Block Below Market"),
    clr     = ifelse(direction == "above_market", RED, GREEN)
  )

p2 <- ggplot(reach_long, aes(x = horizon, y = rate, color = dir_lbl, group = dir_lbl)) +
  geom_hline(yintercept = 0.5, color = "#4a6a80", linetype = "dashed", linewidth = 0.5) +
  geom_ribbon(aes(ymin = rate - 1.96 * se, ymax = rate + 1.96 * se, fill = dir_lbl),
              alpha = 0.15, color = NA) +
  geom_line(linewidth = 1.2) +
  geom_point(size = 3) +
  geom_text(aes(label = paste0(round(rate * 100), "%")),
            vjust = -1.1, size = 2.8, fontface = "bold") +
  scale_color_manual(values = c("Block Above Market" = RED, "Block Below Market" = GREEN),
                     name = NULL) +
  scale_fill_manual(values  = c("Block Above Market" = RED, "Block Below Market" = GREEN),
                    name = NULL) +
  scale_y_continuous(labels = percent_format(accuracy = 1), limits = c(0.3, 1.05)) +
  labs(title = "Price Reach Rate by Horizon",
       subtitle = "% of events where price returned to block print level",
       x = "Forward Horizon", y = "Reach Rate") +
  navy +
  theme(legend.position = "bottom")

# ── Panel 3: deviation bucket vs reach rate (1W) ──────────────────────────────
dev_buckets <- df %>%
  filter(!is.na(reached_1w)) %>%
  mutate(dev_bucket = cut(deviation_pct,
                          breaks = c(0.5, 0.75, 1.0, 1.25, 1.5, 2.0, Inf),
                          labels = c("0.5-0.75%","0.75-1.0%","1.0-1.25%","1.25-1.5%","1.5-2.0%","2.0%+"))) %>%
  group_by(dev_bucket) %>%
  summarise(
    reach_1w  = mean(reached_1w,  na.rm = TRUE),
    reach_1mo = mean(reached_1mo, na.rm = TRUE),
    n         = n(),
    avg_size  = mean(dollar_bn, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(!is.na(dev_bucket))

dev_long <- dev_buckets %>%
  select(dev_bucket, n, reach_1w, reach_1mo) %>%
  pivot_longer(c(reach_1w, reach_1mo), names_to = "horizon", values_to = "rate") %>%
  mutate(horizon = ifelse(horizon == "reach_1w", "1 Week", "1 Month"))

p3 <- ggplot(dev_long, aes(x = dev_bucket, y = rate, fill = horizon)) +
  geom_col(position = position_dodge(0.65), width = 0.6) +
  geom_hline(yintercept = 0.5, color = "#4a6a80", linetype = "dashed", linewidth = 0.5) +
  geom_text(
    data = dev_long %>% filter(horizon == "1 Week"),
    aes(y = 0.05, label = n),
    color = "#a0b8cc", size = 2.5,
    position = position_dodge(0.65)
  ) +
  scale_fill_manual(values = c("1 Week" = ACCENT, "1 Month" = ORANGE), name = "Horizon") +
  scale_y_continuous(labels = percent_format(accuracy = 1), limits = c(0, 1.05)) +
  labs(title = "Reach Rate by Deviation Bucket",
       subtitle = "n = events per bucket (labeled inside bars)",
       x = "Deviation from Market at Print", y = "Reach Rate") +
  navy +
  theme(legend.position = "bottom",
        axis.text.x = element_text(angle = 20, hjust = 1))

# ── Panel 4: exchange breakdown - stacked bars of reach rates ─────────────────
exch_summary <- df %>%
  filter(!is.na(exchange_lbl)) %>%
  select(exchange_lbl, reached_1d, reached_3d, reached_1w, reached_2w, reached_1mo, dollar_bn) %>%
  group_by(exchange_lbl) %>%
  summarise(
    across(starts_with("reached"), \(x) mean(x, na.rm = TRUE)),
    n        = n(),
    avg_size = mean(dollar_bn, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  pivot_longer(starts_with("reached"), names_to = "horizon", values_to = "rate") %>%
  mutate(
    horizon = factor(horizon,
                     levels = c("reached_1d","reached_3d","reached_1w","reached_2w","reached_1mo"),
                     labels = c("1D","3D","1W","2W","1M")),
    exch_label = paste0(exchange_lbl, " (n=", n, ", avg $", round(avg_size, 2), "B)")
  )

p4 <- ggplot(exch_summary, aes(x = horizon, y = rate, color = exchange_lbl, group = exchange_lbl)) +
  geom_hline(yintercept = 0.5, color = "#4a6a80", linetype = "dashed", linewidth = 0.5) +
  geom_line(linewidth = 1.1) +
  geom_point(size = 3) +
  geom_text(aes(label = paste0(round(rate * 100), "%")),
            vjust = -1.1, size = 2.6) +
  scale_color_manual(
    values = c("Dark Pool" = PURPLE, "ADF / OTC" = ACCENT, "FINRA" = ORANGE),
    name   = "Venue",
    labels = exch_summary %>%
      distinct(exchange_lbl, n, avg_size) %>%
      mutate(lbl = paste0(exchange_lbl, " (n=", n, ", avg $", round(avg_size, 2), "B)")) %>%
      { setNames(.$lbl, .$exchange_lbl) }
  ) +
  scale_y_continuous(labels = percent_format(accuracy = 1), limits = c(0.3, 1.05)) +
  labs(title = "Reach Rate by Exchange Venue",
       subtitle = "Dark Pool vs ADF/OTC vs FINRA",
       x = "Forward Horizon", y = "Reach Rate") +
  navy +
  theme(legend.position = "bottom")

# ── assemble ──────────────────────────────────────────────────────────────────
final <- (p1) /
  (p2 | p3 | p4) +
  plot_layout(heights = c(1.3, 1)) +
  plot_annotation(
    title    = "SPY Block Order Analysis",
    subtitle = paste0("$300M+ prints | 0.5%+ deviation | ",
                      format(min(ev$trade_date), "%b %Y"), " - ",
                      format(max(ev$trade_date), "%b %Y")),
    caption  = paste0("648 events | Alpaca SIP | Generated ", Sys.Date()),
    theme = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title      = element_text(color = TXT,      size = 14, face = "bold", hjust = 0.5),
      plot.subtitle   = element_text(color = "#7fa8c4", size = 9,  hjust = 0.5),
      plot.caption    = element_text(color = "#4a6a80", size = 7,  hjust = 1)
    )
  )

ggsave(OUT_PATH, final, width = 16, height = 11, dpi = 150, bg = BG)
cat("Saved:", OUT_PATH, "\n")
