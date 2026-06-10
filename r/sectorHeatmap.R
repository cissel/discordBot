# sectorHeatmap.R by JHCV
# S&P 500 sector ETF heatmap using Alpaca Markets data

##### Required Packages #####
suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
})
#####

##### Args #####
args        <- commandArgs(trailingOnly = TRUE)
output_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/markets/sectorHeatmap.png"
output_path <- path.expand(output_path)
#####

##### Load API Keys #####
readRenviron("~/discordBot/.env")
alpaca_key    <- Sys.getenv("APCA_API_KEY_ID")
alpaca_secret <- Sys.getenv("APCA_API_SECRET_KEY")

if (alpaca_key == "" || alpaca_secret == "") {
  stop("Alpaca API keys not found in ~/discordBot/.env")
}
#####

##### Sector Definitions #####
sector_map <- list(
  XLK  = "Technology",
  XLF  = "Financials",
  XLE  = "Energy",
  XLV  = "Health Care",
  XLI  = "Industrials",
  XLC  = "Communication",
  XLY  = "Consumer Disc",
  XLP  = "Consumer Staples",
  XLRE = "Real Estate",
  XLU  = "Utilities",
  XLB  = "Materials"
)

sector_weights <- c(
  XLK  = 0.30,
  XLF  = 0.13,
  XLV  = 0.12,
  XLY  = 0.10,
  XLI  = 0.09,
  XLC  = 0.09,
  XLP  = 0.06,
  XLE  = 0.04,
  XLRE = 0.03,
  XLU  = 0.03,
  XLB  = 0.03
)

all_tickers  <- c(names(sector_map), "SPY")
sector_syms  <- paste(all_tickers, collapse = ",")
#####

##### Cache Setup #####
cache_dir  <- path.expand("~/discordBot/outputs/markets/cache")
cache_file <- file.path(cache_dir, "sector_bars.csv")
output_dir <- dirname(output_path)

dir.create(cache_dir,  recursive = TRUE, showWarnings = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
#####

##### Fetch Data (with caching) #####
fetch_bars <- function() {
  # Use a 10-day window to guarantee data even on weekends/holidays
  start_date <- format(Sys.Date() - 10, "%Y-%m-%d")
  url <- paste0(
    "https://data.alpaca.markets/v2/stocks/bars",
    "?symbols=", sector_syms,
    "&timeframe=1Day",
    "&start=", start_date,
    "&feed=sip",
    "&sort=asc"
  )

  resp <- GET(
    url,
    add_headers(
      "APCA-API-KEY-ID"     = alpaca_key,
      "APCA-API-SECRET-KEY" = alpaca_secret
    ),
    timeout(30)
  )

  if (http_error(resp)) {
    stop(paste("Alpaca API error:", status_code(resp), content(resp, "text", encoding = "UTF-8")))
  }

  raw       <- content(resp, "text", encoding = "UTF-8")
  parsed    <- fromJSON(raw, simplifyVector = FALSE)
  bars_list <- parsed$bars

  if (is.null(bars_list) || length(bars_list) == 0) {
    stop("No bar data returned from Alpaca API")
  }

  rows <- list()
  for (ticker in names(bars_list)) {
    bars <- bars_list[[ticker]]
    if (length(bars) < 2) next
    # bars are ordered oldest -> newest; take last two
    n        <- length(bars)
    prev_bar <- bars[[n - 1]]
    curr_bar <- bars[[n]]
    rows[[length(rows) + 1]] <- data.frame(
      symbol     = ticker,
      date       = substr(curr_bar$t, 1, 10),
      prev_close = as.numeric(prev_bar$c),
      curr_close = as.numeric(curr_bar$c),
      stringsAsFactors = FALSE
    )
  }

  if (length(rows) == 0) {
    stop("No bar data returned from Alpaca API")
  }

  df <- bind_rows(rows)
  write_csv(df, cache_file)
  return(df)
}

# Check cache freshness (30 min threshold)
use_cache <- FALSE
if (file.exists(cache_file)) {
  cache_age_min <- as.numeric(difftime(Sys.time(), file.mtime(cache_file), units = "mins"))
  if (cache_age_min <= 30) {
    use_cache <- TRUE
  }
}

if (use_cache) {
  df_bars <- read_csv(cache_file, show_col_types = FALSE)
} else {
  df_bars <- fetch_bars()
}
#####

##### Compute % Change #####
df_bars <- df_bars %>%
  mutate(pct_change = (curr_close - prev_close) / prev_close * 100)
#####

##### Separate SPY from Sectors #####
spy_row <- df_bars %>% filter(symbol == "SPY")
sec_df  <- df_bars %>% filter(symbol != "SPY")

if (nrow(spy_row) == 0) {
  spy_pct  <- NA
  spy_date <- Sys.Date()
} else {
  spy_pct  <- spy_row$pct_change[1]
  spy_date <- as.Date(spy_row$date[1])
}

# Build sector data frame with labels and weights
sec_df <- sec_df %>%
  mutate(
    sector_name = unlist(sector_map[symbol]),
    weight      = sector_weights[symbol]
  ) %>%
  filter(!is.na(sector_name))
#####

##### Subtitle Text #####
date_label <- format(spy_date, "%B %d, %Y")

if (!is.na(spy_pct)) {
  spy_sign  <- if (spy_pct >= 0) "+" else ""
  spy_label <- paste0("SPY: ", spy_sign, round(spy_pct, 2), "%")
} else {
  spy_label <- "SPY: N/A"
}

subtitle_text <- paste0(date_label, " | ", spy_label)
#####

##### Color Scale Helpers #####
bg_color   <- "#02233F"
text_color <- "white"

# Determine symmetric limit for diverging scale
max_abs <- max(abs(sec_df$pct_change), na.rm = TRUE)
max_abs <- max(max_abs, 0.5)  # at least 0.5 to avoid degenerate scale
#####

##### Build Plot #####
use_treemap <- requireNamespace("treemapify", quietly = TRUE)

if (use_treemap) {
  suppressPackageStartupMessages(library(treemapify))

  p <- ggplot(sec_df,
              aes(area   = weight,
                  fill   = pct_change,
                  label  = sector_name,
                  subgroup = symbol)) +
    treemapify::geom_treemap(color = bg_color, size = 3) +
    treemapify::geom_treemap_text(
      aes(label = sector_name),
      colour    = text_color,
      fontface  = "bold",
      size      = 14,
      place     = "centre",
      reflow    = TRUE,
      padding.x = grid::unit(3, "mm"),
      padding.y = grid::unit(8, "mm")
    ) +
    treemapify::geom_treemap_text(
      aes(label = paste0(symbol, "\n", sprintf("%+.2f%%", pct_change))),
      colour    = text_color,
      size      = 9,
      place     = "bottomleft",
      padding.x = grid::unit(2, "mm"),
      padding.y = grid::unit(2, "mm")
    ) +
    scale_fill_gradientn(
      colours = c("#cc2222", "#661111", "white", "#116622", "#22cc44"),
      values  = rescale(c(-max_abs, -max_abs * 0.25, 0, max_abs * 0.25, max_abs)),
      limits  = c(-max_abs, max_abs),
      name    = "% Change"
    )

} else {
  # Fallback: geom_tile 4-col x 3-row grid
  sec_df <- sec_df %>%
    arrange(desc(weight)) %>%
    mutate(
      row_idx  = ((row_number() - 1) %/% 4),
      col_idx  = ((row_number() - 1) %%  4),
      tile_x   = col_idx,
      tile_y   = -row_idx
    )

  pct_label <- function(x) {
    sign_str <- if (x >= 0) "+" else ""
    paste0(sign_str, round(x, 2), "%")
  }

  sec_df <- sec_df %>%
    rowwise() %>%
    mutate(pct_label = pct_label(pct_change)) %>%
    ungroup()

  p <- ggplot(sec_df, aes(x = tile_x, y = tile_y, fill = pct_change)) +
    geom_tile(color = bg_color, linewidth = 2) +
    geom_text(
      aes(label = sector_name),
      color    = text_color,
      fontface = "bold",
      size     = 4.2,
      vjust    = 0.2
    ) +
    geom_text(
      aes(label = paste0(symbol, "  ", pct_label)),
      color  = text_color,
      size   = 3.0,
      vjust  = 1.8
    ) +
    scale_fill_gradientn(
      colours = c("#cc2222", "#661111", "white", "#116622", "#22cc44"),
      values  = rescale(c(-max_abs, -max_abs * 0.25, 0, max_abs * 0.25, max_abs)),
      limits  = c(-max_abs, max_abs),
      name    = "% Change"
    ) +
    scale_x_continuous(expand = expansion(add = 0.1)) +
    scale_y_continuous(expand = expansion(add = 0.1)) +
    coord_fixed(ratio = 0.65)
}

##### Apply Theme #####
p <- p +
  labs(
    title    = "S&P 500 Sector Performance",
    subtitle = subtitle_text,
    caption  = "Source: Alpaca Markets | JHCV"
  ) +
  theme_void(base_size = 12) +
  theme(
    plot.background  = element_rect(fill = bg_color,  color = NA),
    panel.background = element_rect(fill = bg_color,  color = NA),
    plot.title       = element_text(
      color    = text_color,
      face     = "bold",
      size     = 18,
      hjust    = 0.5,
      margin   = margin(t = 12, b = 4)
    ),
    plot.subtitle    = element_text(
      color    = text_color,
      size     = 12,
      hjust    = 0.5,
      margin   = margin(b = 8)
    ),
    plot.caption     = element_text(
      color    = text_color,
      size     = 9,
      hjust    = 1,
      margin   = margin(t = 6, b = 6)
    ),
    plot.margin      = margin(10, 14, 6, 14),
    legend.position  = "right",
    legend.background = element_rect(fill = bg_color, color = NA),
    legend.text       = element_text(color = text_color, size = 9),
    legend.title      = element_text(color = text_color, size = 10),
    legend.key.height = unit(40, "pt"),
    legend.key.width  = unit(12, "pt")
  )
#####

##### Save Output #####
ggsave(
  filename = output_path,
  plot     = p,
  width    = 1200,
  height   = 700,
  units    = "px",
  dpi      = 150,
  bg       = bg_color
)

message("Saved sector heatmap to: ", output_path)
#####
