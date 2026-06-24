# blockPriceChart.R
# SPY daily close with block order events overlaid
# Usage: Rscript r/blockPriceChart.R [output_path]

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
              path.expand("~/discordBot/outputs/markets/block_price_chart.png")
dir.create(dirname(OUT_PATH), recursive = TRUE, showWarnings = FALSE)

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
MUTED  <- "#a0b8cc"

# ── theme ─────────────────────────────────────────────────────────────────────
navy <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID,     linewidth = 0.35),
  panel.grid.minor  = element_line(color = "#1a3a5c", linewidth = 0.15),
  axis.text         = element_text(color = MUTED,    size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = TXT,      size = 12, face = "bold", hjust = 0.5),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 8,  hjust = 0.5),
  plot.caption      = element_text(color = "#4a6a80", size = 7,  hjust = 1),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 8, face = "bold"),
  legend.background = element_rect(fill = BG),
  legend.text       = element_text(color = MUTED,    size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = BG),
  plot.margin       = margin(8, 12, 8, 12)
)

# ── load price data ───────────────────────────────────────────────────────────
vwap_path <- path.expand("~/discordBot/outputs/markets/cache/spy_vwap_daily.csv")
bars_path <- path.expand("~/discordBot/outputs/markets/SPY_1y_bars.csv")

price <- read_csv(vwap_path, show_col_types = FALSE) %>%
  mutate(date = as.Date(date)) %>%
  select(date, close = close_price)

# fill in any recent dates not yet in vwap cache from 1y bars
bars_recent <- read_csv(bars_path, show_col_types = FALSE) %>%
  mutate(date = as.Date(date)) %>%
  select(date, close) %>%
  filter(date > max(price$date))

price <- bind_rows(price, bars_recent) %>%
  arrange(date) %>%
  distinct(date, .keep_all = TRUE)

# ── load block events ─────────────────────────────────────────────────────────
ev_path <- path.expand("~/discordBot/outputs/research/block_events.csv")
oc_path <- path.expand("~/discordBot/outputs/research/block_outcomes.csv")

ev <- read_csv(ev_path, show_col_types = FALSE) %>%
  mutate(
    trade_date    = as.Date(trade_date),
    dollar_bn     = dollar_value / 1e9,
    deviation_pct = deviation * 100,
    exchange_lbl  = case_when(
      exchange == "D" ~ "Dark Pool",
      exchange == "P" ~ "ADF / OTC",
      exchange == "M" ~ "FINRA",
      TRUE            ~ exchange
    )
  )

oc <- read_csv(oc_path, show_col_types = FALSE) %>%
  mutate(trade_date = as.Date(trade_date))

df <- ev %>%
  left_join(
    oc %>% select(trade_date, dollar_value, direction, reached_1w, reached_1mo),
    by = c("trade_date", "dollar_value"),
    relationship = "many-to-many"
  ) %>%
  distinct()

# restrict price to block period + 30 day buffer
blk_start <- min(df$trade_date) - 30
blk_end   <- max(df$trade_date) + 5
price_blk  <- price %>% filter(date >= blk_start & date <= blk_end)

# 20-day moving average on price
price_blk <- price_blk %>%
  arrange(date) %>%
  mutate(ma20 = zoo::rollmean(close, k = 20, fill = NA, align = "right"))

# fallback if zoo not available
if (!requireNamespace("zoo", quietly = TRUE)) {
  price_blk <- price_blk %>%
    mutate(ma20 = stats::filter(close, rep(1/20, 20), sides = 1) %>% as.numeric())
}

# largest events for labels (top 12 by dollar)
top_events <- df %>%
  slice_max(dollar_bn, n = 12, with_ties = FALSE)

# ── Panel 1: price + block dots ───────────────────────────────────────────────
p1 <- ggplot() +
  # price line
  geom_line(data = price_blk, aes(x = date, y = close),
            color = ACCENT, linewidth = 0.8, alpha = 0.9) +
  # ma20
  geom_line(data = price_blk %>% filter(!is.na(ma20)),
            aes(x = date, y = ma20),
            color = "#4a6a80", linewidth = 0.5, linetype = "dashed") +
  # block prints - all events, sized by dollar, colored by exchange
  geom_point(data = df,
             aes(x = trade_date, y = price,
                 size  = dollar_bn,
                 color = exchange_lbl,
                 shape = direction),
             alpha = 0.75) +
  # vertical dashed line from print price to close on that day
  geom_segment(data = df %>% left_join(price_blk, by = c("trade_date" = "date")),
               aes(x = trade_date, xend = trade_date,
                   y = price, yend = close,
                   color = exchange_lbl),
               linewidth = 0.25, alpha = 0.35, linetype = "solid") +
  # labels for largest prints
  geom_text_repel(
    data = top_events,
    aes(x = trade_date, y = price,
        label = paste0("$", round(dollar_bn, 1), "B\n", round(deviation_pct, 1), "%")),
    color = YELLOW, size = 2.3, max.overlaps = 10,
    segment.color = "#4a6a80", segment.size = 0.3,
    box.padding = 0.4, point.padding = 0.3,
    min.segment.length = 0.2
  ) +
  scale_color_manual(
    values = c("Dark Pool" = PURPLE, "ADF / OTC" = GREEN, "FINRA" = ORANGE),
    name   = "Venue"
  ) +
  scale_shape_manual(
    values = c("above_market" = 24, "below_market" = 25),
    name   = "Block vs Market",
    labels = c("above_market" = "Above market", "below_market" = "Below market")
  ) +
  scale_size_continuous(
    range  = c(1.5, 9),
    name   = "Size ($B)",
    labels = function(x) paste0("$", round(x, 1), "B")
  ) +
  scale_x_date(date_labels = "%b '%y", date_breaks = "1 month",
               expand = expansion(mult = c(0.01, 0.02))) +
  scale_y_continuous(labels = dollar_format(accuracy = 1)) +
  labs(
    title    = "SPY Price History with Block Order Events",
    subtitle = paste0(nrow(df), " qualifying blocks | $300M+ | >= 0.5% deviation | ",
                      "triangle up = above market, triangle down = below market"),
    x = NULL, y = "SPY Price ($)"
  ) +
  navy +
  theme(
    legend.position = "right",
    axis.text.x     = element_text(angle = 35, hjust = 1),
    panel.grid.minor.x = element_blank()
  ) +
  guides(
    color = guide_legend(override.aes = list(size = 3)),
    size  = guide_legend(override.aes = list(shape = 16))
  )

# ── Panel 2: daily block dollar volume as bar chart ───────────────────────────
daily_vol <- df %>%
  group_by(trade_date) %>%
  summarise(
    total_bn   = sum(dollar_bn, na.rm = TRUE),
    n_events   = n(),
    avg_dev    = mean(deviation_pct, na.rm = TRUE),
    .groups    = "drop"
  )

p2 <- ggplot(daily_vol, aes(x = trade_date, y = total_bn, fill = avg_dev)) +
  geom_col(width = 1, alpha = 0.85) +
  scale_fill_gradient2(
    low      = TEAL,
    mid      = ORANGE,
    high     = RED,
    midpoint = 1.1,
    name     = "Avg Dev %",
    labels   = function(x) paste0(round(x, 1), "%")
  ) +
  scale_x_date(date_labels = "%b '%y", date_breaks = "1 month",
               expand = expansion(mult = c(0.01, 0.02))) +
  scale_y_continuous(labels = function(x) paste0("$", round(x, 1), "B")) +
  labs(
    title    = "Daily Block Order Volume",
    subtitle = "Total notional ($B) per day, colored by avg deviation",
    x = NULL, y = "Total ($B)"
  ) +
  navy +
  theme(
    axis.text.x        = element_text(angle = 35, hjust = 1),
    panel.grid.minor.x = element_blank(),
    legend.position    = "right"
  )

# ── assemble ──────────────────────────────────────────────────────────────────
final <- p1 / p2 +
  plot_layout(heights = c(3, 1)) +
  plot_annotation(
    caption = paste0("Alpaca SIP feed | Generated ", Sys.Date(),
                     " | dashed line = 20-day MA | segments connect print to day's close"),
    theme = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.caption    = element_text(color = "#4a6a80", size = 7, hjust = 1)
    )
  )

ggsave(OUT_PATH, final, width = 16, height = 11, dpi = 150, bg = BG)
cat("Saved:", OUT_PATH, "\n")
