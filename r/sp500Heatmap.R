# sp500Heatmap.R by JHCV
# Full S&P 500 heatmap in Finviz style - every stock as its own tile,
# grouped by GICS sector, sized by live market cap (shares * today's close).

##### Required Packages #####
suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(dplyr)
  library(readr)
  library(ggplot2)
  library(scales)
  library(treemapify)
})
#####

##### Args #####
args        <- commandArgs(trailingOnly = TRUE)
output_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/markets/sp500Heatmap.png"
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

##### Directories #####
BG         <- "#02233F"
cache_dir  <- path.expand("~/discordBot/outputs/markets/cache")
output_dir <- dirname(output_path)
dir.create(cache_dir,  recursive = TRUE, showWarnings = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
#####

##### Fetch S&P 500 Constituent List (cache 24h) #####
constituents_cache <- file.path(cache_dir, "sp500_constituents.csv")

fetch_constituents <- function() {
  url  <- "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
  resp <- tryCatch(
    GET(url, timeout(30)),
    error = function(e) NULL
  )
  if (is.null(resp) || http_error(resp)) {
    stop("Failed to fetch S&P 500 constituent list from GitHub")
  }
  raw_text <- content(resp, "text", encoding = "UTF-8")
  df       <- read_csv(I(raw_text), show_col_types = FALSE)
  # Normalise column names - GitHub CSV uses: Symbol, Name, Sector
  # The "GICS Sector" column may vary; handle both possibilities
  if ("GICS Sector" %in% names(df)) {
    df <- df %>% rename(sector = `GICS Sector`)
  } else if ("Sector" %in% names(df)) {
    df <- df %>% rename(sector = Sector)
  } else {
    stop("Cannot find sector column in S&P 500 CSV")
  }
  if ("Security" %in% names(df)) {
    df <- df %>% rename(name = Security)
  } else if ("Name" %in% names(df)) {
    df <- df %>% rename(name = Name)
  }
  # Note: Alpaca accepts dot-tickers as-is (e.g. BRK.B, BF.B) - no replacement needed
  df <- df %>%
    select(symbol = Symbol, name, sector) %>%
    filter(!is.na(symbol), !is.na(sector))
  write_csv(df, constituents_cache)
  return(df)
}

use_constituents_cache <- FALSE
if (file.exists(constituents_cache)) {
  age_h <- as.numeric(difftime(Sys.time(), file.mtime(constituents_cache), units = "hours"))
  if (age_h <= 24) use_constituents_cache <- TRUE
}

if (use_constituents_cache) {
  message("Using cached S&P 500 constituent list")
  df_constituents <- read_csv(constituents_cache, show_col_types = FALSE)
} else {
  message("Fetching S&P 500 constituent list from GitHub...")
  df_constituents <- fetch_constituents()
  message("Fetched ", nrow(df_constituents), " constituents")
}
#####
##### Fetch shares outstanding from financials CSV (cache 24h) #####
# Market cap in financials CSV / static price = shares outstanding (stable over time)
# tile_weight = shares_outstanding * today_close  (live market cap)
financials_cache <- file.path(cache_dir, "sp500_financials.csv")

fetch_financials <- function() {
  url  <- "https://raw.githubusercontent.com/datasets/s-and-p-500-companies-financials/main/data/constituents-financials.csv"
  resp <- tryCatch(GET(url, timeout(30)), error = function(e) NULL)
  if (is.null(resp) || http_error(resp)) {
    stop("Failed to fetch S&P 500 financials from GitHub")
  }
  raw_text <- content(resp, "text", encoding = "UTF-8")
  df       <- read_csv(I(raw_text), show_col_types = FALSE)
  df <- df %>%
    transmute(
      symbol = Symbol,
      static_price  = suppressWarnings(as.numeric(Price)),
      static_mcap   = suppressWarnings(as.numeric(`Market Cap`))
    ) %>%
    filter(!is.na(static_price), static_price > 0,
           !is.na(static_mcap),  static_mcap  > 0) %>%
    mutate(shares_outstanding = static_mcap / static_price)
  write_csv(df, financials_cache)
  return(df)
}

use_fin_cache <- file.exists(financials_cache) &&
  as.numeric(difftime(Sys.time(), file.mtime(financials_cache), units = "hours")) <= 24

if (use_fin_cache) {
  message("Using cached financials (shares outstanding)")
  df_financials <- read_csv(financials_cache, show_col_types = FALSE)
} else {
  message("Fetching S&P 500 financials (shares outstanding)...")
  df_financials <- tryCatch(fetch_financials(), error = function(e) {
    message("Financials fetch failed: ", conditionMessage(e), " - will fall back to equal weighting")
    NULL
  })
}
#####

##### Fetch Alpaca Snapshots (cache 30 min) #####
snapshots_cache <- file.path(cache_dir, "sp500_snapshots.csv")

fetch_snapshots <- function(tickers) {
  # Include SPY for subtitle even if not in constituents list
  all_tickers <- unique(c(tickers, "SPY"))
  batches     <- split(all_tickers, ceiling(seq_along(all_tickers) / 200))

  results <- list()
  for (i in seq_along(batches)) {
    batch      <- batches[[i]]
    sym_string <- paste(batch, collapse = ",")
    url <- paste0(
      "https://data.alpaca.markets/v2/stocks/snapshots",
      "?symbols=", URLencode(sym_string, reserved = FALSE),
      "&feed=sip"
    )
    message(sprintf("  Batch %d/%d (%d symbols)...", i, length(batches), length(batch)))
    resp <- tryCatch(
      GET(
        url,
        add_headers(
          "APCA-API-KEY-ID"     = alpaca_key,
          "APCA-API-SECRET-KEY" = alpaca_secret
        ),
        timeout(60)
      ),
      error = function(e) {
        message("  Request error: ", conditionMessage(e))
        NULL
      }
    )
    if (is.null(resp) || http_error(resp)) {
      message("  Skipping batch ", i, " - HTTP error")
      next
    }
    raw    <- content(resp, "text", encoding = "UTF-8")
    parsed <- tryCatch(fromJSON(raw, simplifyVector = FALSE), error = function(e) NULL)
    if (is.null(parsed) || length(parsed) == 0) next

    for (sym in names(parsed)) {
      snap <- parsed[[sym]]
      daily     <- snap$dailyBar
      prev      <- snap$prevDailyBar
      if (is.null(daily) || is.null(prev)) next
      close_val <- tryCatch(as.numeric(daily[["c"]]),  error = function(e) NA_real_)
      prev_val  <- tryCatch(as.numeric(prev[["c"]]),   error = function(e) NA_real_)
      if (is.na(close_val) || is.na(prev_val) || prev_val == 0) next
      pct <- (close_val - prev_val) / prev_val * 100
      results[[length(results) + 1]] <- data.frame(
        symbol     = sym,
        pct_change = pct,
        close      = close_val,
        stringsAsFactors = FALSE
      )
    }
  }

  if (length(results) == 0) stop("No snapshot data returned from Alpaca")
  df <- bind_rows(results)
  write_csv(df, snapshots_cache)
  return(df)
}

use_snap_cache <- FALSE
if (file.exists(snapshots_cache)) {
  age_min <- as.numeric(difftime(Sys.time(), file.mtime(snapshots_cache), units = "mins"))
  if (age_min <= 30) use_snap_cache <- TRUE
}

all_symbols <- df_constituents$symbol
if (use_snap_cache) {
  message("Using cached Alpaca snapshot data")
  df_snaps <- read_csv(snapshots_cache, show_col_types = FALSE)
} else {
  message("Fetching Alpaca snapshots for ", length(all_symbols), " symbols (+ SPY)...")
  df_snaps <- fetch_snapshots(all_symbols)
  message("Got data for ", nrow(df_snaps), " symbols")
}
#####

##### Extract SPY for Subtitle #####
spy_row <- df_snaps %>% filter(symbol == "SPY")
if (nrow(spy_row) > 0) {
  spy_pct  <- spy_row$pct_change[1]
  spy_sign <- if (spy_pct >= 0) "+" else ""
  spy_label <- paste0("SPY: ", spy_sign, sprintf("%.2f", spy_pct), "%")
} else {
  spy_label <- "SPY: N/A"
}
date_label    <- format(Sys.Date(), "%B %d, %Y")
subtitle_text <- paste0(date_label, " | ", spy_label)
#####

##### Join Constituents + Snapshots #####
df_plot <- df_constituents %>%
  inner_join(df_snaps %>% select(symbol, pct_change, close), by = "symbol") %>%
  filter(!is.na(pct_change), !is.na(close), close > 0)

# Compute tile_weight = live market cap (shares_outstanding * today_close)
# Fall back to equal-within-sector if financials unavailable
if (!is.null(df_financials) && nrow(df_financials) > 0) {
  df_plot <- df_plot %>%
    left_join(df_financials %>% select(symbol, shares_outstanding), by = "symbol") %>%
    mutate(
      tile_weight = ifelse(
        !is.na(shares_outstanding) & shares_outstanding > 0,
        shares_outstanding * close,   # live market cap
        median(shares_outstanding * close, na.rm = TRUE)  # fallback: sector median
      )
    )
  message(sprintf("Live market cap weighting: %d stocks with shares data",
                  sum(!is.na(df_plot$shares_outstanding))))
} else {
  # Equal weighting within sector as fallback
  df_plot <- df_plot %>%
    group_by(sector) %>%
    mutate(tile_weight = 1 / n()) %>%
    ungroup()
  message("Using equal-within-sector weighting (financials unavailable)")
}

df_plot <- df_plot %>% filter(tile_weight > 0)

message("Plotting ", nrow(df_plot), " stocks")

if (nrow(df_plot) == 0) {
  stop("No stocks to plot after joining constituents and snapshot data")
}
#####

##### Color Scale Limits #####
lim <- max(abs(df_plot$pct_change), na.rm = TRUE)
lim <- max(lim, 3.0)
#####

##### Build Treemap Plot #####
p <- ggplot(df_plot,
            aes(
              area     = tile_weight,
              fill     = pct_change,
              label    = symbol,
              subgroup = sector
            )) +

  # Base treemap tiles
  geom_treemap(colour = BG, size = 0.5) +

  # Sector group borders
  geom_treemap_subgroup_border(colour = "#02233F", size = 3) +

  # Sector label (large, faint, centred)
  geom_treemap_subgroup_text(
    colour   = "white",
    alpha    = 0.25,
    fontface = "bold",
    size     = 14,
    place    = "centre"
  ) +

  # Stock ticker label
  geom_treemap_text(
    aes(label = symbol),
    colour   = "white",
    fontface = "bold",
    size     = 7,
    place    = "centre",
    reflow   = FALSE
  ) +

  # Pct change label (bottom-right of tile)
  geom_treemap_text(
    aes(label = sprintf("%+.1f%%", pct_change)),
    colour   = "white",
    alpha    = 0.85,
    size     = 5,
    place    = "bottomright",
    reflow   = FALSE
  ) +

  # Diverging color scale
  scale_fill_gradientn(
    colours = c("#8B0000", "#cc2222", "#444444", "#22aa44", "#006400"),
    values  = rescale(c(-lim, -lim * 0.3, 0, lim * 0.3, lim)),
    limits  = c(-lim, lim),
    name    = "% Chg"
  ) +

  labs(
    title    = "S&P 500 Heatmap",
    subtitle = subtitle_text,
    caption  = "Source: Alpaca Markets | JHCV"
  ) +

  theme_void() +
  theme(
    plot.background  = element_rect(fill = BG, color = NA),
    plot.title       = element_text(color = "white", size = 18, face = "bold",
                                    hjust = 0.5, margin = margin(t = 10, b = 4)),
    plot.subtitle    = element_text(color = "white", size = 11,
                                    hjust = 0.5, margin = margin(b = 6)),
    plot.caption     = element_text(color = "white", size = 8,
                                    hjust = 1, margin = margin(t = 4, b = 6)),
    plot.margin      = margin(8, 10, 6, 10),
    legend.position  = "right",
    legend.background = element_rect(fill = BG, color = NA),
    legend.text       = element_text(color = "white", size = 8),
    legend.title      = element_text(color = "white", size = 9),
    legend.key.height = unit(40, "pt"),
    legend.key.width  = unit(12, "pt")
  )
#####

##### Save Output #####
message("Saving heatmap to: ", output_path)
ggsave(
  filename = output_path,
  plot     = p,
  width    = 1600,
  height   = 900,
  units    = "px",
  dpi      = 150,
  bg       = BG
)
message("Done.")
#####
