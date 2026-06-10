#!/usr/bin/env Rscript
# btcNUPL.R - BTC Net Unrealized Profit/Loss (NUPL) chart
# NUPL = 1 - (1 / MVRV) — derived from CoinMetrics CapMVRVCur
# Design: static colored background bands + NUPL line colored by zone + price overlay
# Usage: Rscript btcNUPL.R [output.png]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(httr)
  library(jsonlite)
})

# ── paths ──────────────────────────────────────────────────────────────────────
args      <- commandArgs(trailingOnly = TRUE)
out_png   <- if (length(args) >= 1) args[1] else
               path.expand("~/discordBot/outputs/markets/btcNUPL.png")
cache_dir <- path.expand("~/discordBot/outputs/markets/cache")
cache_csv <- file.path(cache_dir, "BTC_nupl_daily.csv")
dir.create(cache_dir, showWarnings = FALSE, recursive = TRUE)

# ── fetch / cache logic ────────────────────────────────────────────────────────
need_fetch <- TRUE
if (file.exists(cache_csv)) {
  age_hours <- as.numeric(difftime(Sys.time(), file.mtime(cache_csv), units = "hours"))
  if (age_hours < 24) {
    need_fetch <- FALSE
    cat(sprintf("[btcNUPL] cache fresh (%.1f h old), skipping fetch\n", age_hours))
  } else {
    cat(sprintf("[btcNUPL] cache stale (%.1f h old), re-fetching\n", age_hours))
  }
}

if (need_fetch) {
  url <- paste0(
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "?assets=btc&metrics=CapMVRVCur,PriceUSD",
    "&frequency=1d&start_time=2010-07-18&page_size=10000"
  )
  cat("[btcNUPL] fetching from CoinMetrics ...\n")
  resp <- tryCatch(httr::GET(url, httr::timeout(30)), error = function(e) NULL)

  ok <- !is.null(resp) && httr::status_code(resp) == 200
  if (ok) {
    raw    <- httr::content(resp, as = "text", encoding = "UTF-8")
    parsed <- jsonlite::fromJSON(raw, simplifyDataFrame = TRUE)
    df_raw <- parsed$data

    df_raw <- df_raw %>%
      rename(date = time) %>%
      mutate(
        date       = as.Date(substr(date, 1, 10)),
        CapMVRVCur = suppressWarnings(as.numeric(CapMVRVCur)),
        PriceUSD   = suppressWarnings(as.numeric(PriceUSD))
      )

    write_csv(df_raw, cache_csv)
    cat(sprintf("[btcNUPL] saved %d rows to cache\n", nrow(df_raw)))
  } else {
    code <- if (!is.null(resp)) httr::status_code(resp) else "no response"
    cat(sprintf("[btcNUPL] WARNING: fetch failed (%s)", code))
    if (file.exists(cache_csv)) {
      cat(" - using stale cache\n")
    } else {
      stop(" - no cache available, aborting")
    }
  }
}

# ── load & compute NUPL ───────────────────────────────────────────────────────
df <- read_csv(cache_csv, show_col_types = FALSE) %>%
  mutate(
    date       = as.Date(date),
    CapMVRVCur = suppressWarnings(as.numeric(CapMVRVCur)),
    PriceUSD   = suppressWarnings(as.numeric(PriceUSD))
  ) %>%
  filter(!is.na(CapMVRVCur), CapMVRVCur != 0, !is.na(PriceUSD)) %>%
  mutate(nupl = 1 - (1 / CapMVRVCur)) %>%
  arrange(date)

cat(sprintf("[btcNUPL] %d rows after NUPL calculation\n", nrow(df)))

# ── zone classification ────────────────────────────────────────────────────────
df <- df %>%
  mutate(
    zone = case_when(
      nupl < -0.25               ~ "Capitulation",
      nupl >= -0.25 & nupl < 0   ~ "Hope/Fear",
      nupl >= 0    & nupl < 0.25 ~ "Optimism/Anxiety",
      nupl >= 0.25 & nupl < 0.5  ~ "Belief/Denial",
      nupl >= 0.5                ~ "Euphoria/Greed",
      TRUE                       ~ NA_character_
    ),
    zone = factor(zone, levels = c(
      "Capitulation", "Hope/Fear", "Optimism/Anxiety",
      "Belief/Denial", "Euphoria/Greed"
    ))
  )

zone_colors <- c(
  "Capitulation"     = "#e74c3c",
  "Hope/Fear"        = "#e67e22",
  "Optimism/Anxiety" = "#f1c40f",
  "Belief/Denial"    = "#2ecc71",
  "Euphoria/Greed"   = "#3498db"
)

# ── price overlay: map log10(price) into the NUPL y range ─────────────────────
# Use fixed NUPL display range: -0.75 to 1.0
nupl_lo <- -0.75
nupl_hi <-  1.00

log_price <- log10(df$PriceUSD)
lp_min    <- floor(min(log_price, na.rm = TRUE))    # ~0
lp_max    <- ceiling(max(log_price, na.rm = TRUE))  # ~6

price_to_nupl <- function(p) {
  nupl_lo + (log10(p) - lp_min) / (lp_max - lp_min) * (nupl_hi - nupl_lo)
}
nupl_to_price <- function(n) {
  10 ^ (lp_min + (n - nupl_lo) / (nupl_hi - nupl_lo) * (lp_max - lp_min))
}

df <- df %>% mutate(price_scaled = price_to_nupl(PriceUSD))

# ── static background band data ────────────────────────────────────────────────
x_min <- min(df$date)
x_max <- max(df$date)

bands <- data.frame(
  zone = factor(c(
    "Capitulation", "Hope/Fear", "Optimism/Anxiety",
    "Belief/Denial", "Euphoria/Greed"
  ), levels = c(
    "Capitulation", "Hope/Fear", "Optimism/Anxiety",
    "Belief/Denial", "Euphoria/Greed"
  )),
  ymin = c(nupl_lo, -0.25,  0.00, 0.25, 0.50),
  ymax = c(-0.25,    0.00,  0.25, 0.50, nupl_hi)
)

# ── palette / theme ───────────────────────────────────────────────────────────
BG    <- "#0d1117"
GRID  <- "#21262d"
WHITE <- "#e6edf3"
GREY  <- "#8b949e"
CYAN  <- "#00bfff"

myTheme <- theme_minimal(base_size = 10) +
  theme(
    plot.background    = element_rect(fill = BG,   color = NA),
    panel.background   = element_rect(fill = BG,   color = NA),
    panel.grid.major   = element_line(color = GRID, linewidth = 0.3),
    panel.grid.minor   = element_blank(),
    axis.ticks         = element_line(color = GRID),
    axis.text          = element_text(color = WHITE, size = 9),
    axis.title         = element_text(color = WHITE, size = 10),
    axis.title.y.right = element_text(color = GREY,  size = 10),
    axis.text.y.right  = element_text(color = GREY,  size = 8),
    plot.title         = element_text(color = WHITE, hjust = 0.5, size = 13, face = "bold"),
    plot.subtitle      = element_text(color = GREY,  hjust = 0.5, size = 9),
    plot.caption       = element_text(color = GREY,  hjust = 1,   size = 7),
    legend.background  = element_rect(fill = BG,   color = NA),
    legend.key         = element_rect(fill = BG,   color = NA),
    legend.text        = element_text(color = WHITE, size = 8),
    legend.title       = element_text(color = WHITE, size = 9),
    legend.position    = "bottom",
    legend.key.width   = unit(1.4, "cm"),
    legend.key.height  = unit(0.35, "cm"),
    strip.background   = element_rect(fill = BG, color = NA),
    strip.text         = element_text(color = WHITE),
    plot.margin        = margin(8, 12, 6, 8)
  )

# ── halving dates ─────────────────────────────────────────────────────────────
halvings <- as.Date(c("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"))

# ── build plot ────────────────────────────────────────────────────────────────
p <- ggplot() +

  # static background bands (full width, full zone height)
  geom_rect(
    data = bands,
    aes(xmin = x_min, xmax = x_max, ymin = ymin, ymax = ymax, fill = zone),
    alpha = 0.30,
    inherit.aes = FALSE
  ) +

  # halving lines
  geom_vline(
    xintercept = as.numeric(halvings),
    color = "#ffffff", linewidth = 0.45, linetype = "dotted", alpha = 0.5
  ) +

  # zone boundary dashed lines
  geom_hline(
    yintercept = c(-0.25, 0, 0.25, 0.5),
    linetype = "dashed", color = "#555555", linewidth = 0.3
  ) +

  # BTC price overlay (log-scaled into NUPL space)
  geom_line(
    data = df,
    aes(x = date, y = price_scaled),
    color = GREY, linewidth = 0.65, alpha = 0.70,
    inherit.aes = FALSE
  ) +

  # NUPL line colored by zone
  geom_line(
    data = df,
    aes(x = date, y = nupl, color = zone, group = 1),
    linewidth = 0.9, alpha = 0.95,
    inherit.aes = FALSE
  ) +

  # halving labels at top
  annotate(
    "text",
    x = halvings, y = nupl_hi - 0.04,
    label = c("H1", "H2", "H3", "H4"),
    color = "#ffffff", size = 2.8, alpha = 0.65, hjust = -0.15
  ) +

  # fills for legend
  scale_fill_manual(
    name   = "Sentiment Zone",
    values = zone_colors,
    guide  = guide_legend(nrow = 1, override.aes = list(alpha = 0.6))
  ) +

  # NUPL line colors (same palette)
  scale_color_manual(
    values = zone_colors,
    guide  = "none"
  ) +

  scale_x_date(
    date_breaks = "1 year", date_labels = "%Y", expand = c(0.01, 0)
  ) +

  scale_y_continuous(
    name   = "NUPL",
    limits = c(nupl_lo, nupl_hi),
    breaks = seq(-0.75, 1.0, by = 0.25),
    labels = number_format(accuracy = 0.01),
    expand = c(0, 0),
    sec.axis = sec_axis(
      transform = ~ nupl_to_price(.),
      name      = "BTC Price (USD)",
      labels    = label_dollar(scale_cut = cut_short_scale(), accuracy = NULL)
    )
  ) +

  labs(
    title    = "BTC NUPL - Net Unrealized Profit/Loss",
    subtitle = paste0("Derived from MVRV ratio (CoinMetrics) - Latest: ",
                      format(max(df$date), "%b %d, %Y"),
                      "  NUPL = ", round(tail(df$nupl, 1), 3),
                      "  [", as.character(tail(df$zone, 1)), "]"),
    x        = NULL,
    caption  = "Source: CoinMetrics | JHCV"
  ) +

  myTheme

# ── save ───────────────────────────────────────────────────────────────────────
dir.create(dirname(out_png), showWarnings = FALSE, recursive = TRUE)
ggsave(out_png, plot = p, width = 12, height = 6, dpi = 150, bg = BG)
cat(sprintf("[btcNUPL] saved -> %s\n", out_png))
