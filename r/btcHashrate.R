#!/usr/bin/env Rscript
# btcHashrate.R - BTC network hashrate over time
# Usage: Rscript btcHashrate.R <out_png>

suppressPackageStartupMessages({
  library(tidyverse)
  library(scales)
  library(zoo)
})

args    <- commandArgs(trailingOnly = TRUE)
out_png <- if (length(args) >= 1) args[1] else "/tmp/btcHashrate.png"

# ── load cache ─────────────────────────────────────────────────────────────────
cache_path <- path.expand("~/discordBot/outputs/markets/cache/HASHRATE.csv")
if (!file.exists(cache_path)) stop("HASHRATE cache not found: ", cache_path)

df <- read_csv(cache_path, show_col_types = FALSE) %>%
  mutate(date = as.Date(date)) %>%
  filter(!is.na(HASHRATE_val)) %>%
  arrange(date)

# ── unit scaling: pick best unit (TH/s -> EH/s) ───────────────────────────────
# cache stores log(hashrate_in_TH/s) from blockchain.com API
# 1 EH/s = 1,000,000 TH/s, so divide by 1e6 to get EH/s
df$hr_scaled <- exp(df$HASHRATE_val) / 1e6
unit         <- "EH/s"

# 30-day rolling average
df$roll30 <- as.numeric(rollmean(df$hr_scaled, k = 30, fill = NA, align = "right"))

latest     <- tail(df, 1)
latest_val <- round(latest$hr_scaled, 1)
latest_dt  <- format(latest$date, "%b %d, %Y")

# ── theme (matches bot palette) ────────────────────────────────────────────────
BG     <- "#02233F"
GRID   <- "#274066"
CYAN   <- "#00FFFF"
WHITE  <- "#FFFFFF"
ORANGE <- "#FF8C00"

myTheme <- theme(
  legend.position   = "none",
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.3),
  panel.grid.minor  = element_line(color = GRID, linewidth = 0.15),
  axis.ticks        = element_line(color = GRID),
  axis.text         = element_text(color = WHITE, size = 9),
  axis.title        = element_text(color = WHITE, size = 10),
  plot.title        = element_text(color = WHITE, hjust = 0.5, size = 13, face = "bold"),
  plot.subtitle     = element_text(color = WHITE, hjust = 0.5, size = 9),
  plot.caption      = element_text(color = GRID,  hjust = 1,   size = 7),
  strip.background  = element_rect(fill = BG, color = NA),
  strip.text        = element_text(color = WHITE)
)

# ── plot ───────────────────────────────────────────────────────────────────────
p <- ggplot(df, aes(x = date)) +
  geom_line(aes(y = hr_scaled), color = CYAN, alpha = 0.35, linewidth = 0.4) +
  geom_line(aes(y = roll30),    color = ORANGE, linewidth = 0.9, na.rm = TRUE) +
  # latest value annotation
  annotate("point", x = latest$date, y = latest$hr_scaled,
           color = WHITE, size = 2.5) +
  annotate("text",
           x = latest$date, y = latest$hr_scaled,
           label = sprintf("%s %s", format(latest_val, big.mark=","), unit),
           color = WHITE, hjust = 1.1, vjust = -0.5, size = 3.2) +
  scale_x_date(date_breaks = "1 year", date_labels = "%Y", expand = c(0.02, 0)) +
  scale_y_continuous(
    labels = label_comma(suffix = paste0(" ", unit)),
    expand = c(0.05, 0)
  ) +
  labs(
    title    = "Bitcoin Network Hashrate",
    subtitle = sprintf("Daily  |  30-day avg (orange)  |  latest: %s %s as of %s",
                       format(latest_val, big.mark=","), unit, latest_dt),
    x        = NULL,
    y        = paste0("Hashrate (", unit, ")"),
    caption  = "Source: Blockchain.com | JHCV"
  ) +
  myTheme

# ── save ───────────────────────────────────────────────────────────────────────
ggsave(out_png, plot = p, width = 12, height = 5, dpi = 150, bg = BG)
cat(sprintf("[btcHashrate] saved %s\n", out_png))
