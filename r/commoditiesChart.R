# commoditiesChart.R - Commodities Dashboard
# Layout: all = 3x4 (Metals / Energy / Softs)
#         category filter = 1x4 single row
# Usage: Rscript commoditiesChart.R [output_path] [days] [category: all|Metals|Energy|Softs]

suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(patchwork)
})

##### Config #####
BG        <- "#02233F"
GRID      <- "#274066"
ACCENT    <- "#00bfff"
ORANGE    <- "#ff8c00"
CACHE_DIR <- path.expand("~/discordBot/outputs/markets/cache")
CACHE_TTL <- 3600

args     <- commandArgs(trailingOnly = TRUE)
OUTPUT   <- if (length(args) >= 1) args[1] else
            path.expand("~/discordBot/outputs/markets/commoditiesChart.png")
DAYS     <- if (length(args) >= 2) as.integer(args[2]) else 90
CATEGORY <- if (length(args) >= 3) args[3] else "all"  # all | Metals | Energy | Softs

RANGE_STR <- if (DAYS <= 90) "3mo" else if (DAYS <= 180) "6mo" else
             if (DAYS <= 365) "1y"  else if (DAYS <= 730) "2y"  else
             if (DAYS <= 1825) "5y" else "10y"

dir.create(CACHE_DIR,       showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(OUTPUT), showWarnings = FALSE, recursive = TRUE)

##### Commodity definitions #####
ALL_COMMODITIES <- list(
  list(symbol = "GC=F",  name = "Gold",        row = "Metals"),
  list(symbol = "SI=F",  name = "Silver",       row = "Metals"),
  list(symbol = "HG=F",  name = "Copper",       row = "Metals"),
  list(symbol = "PL=F",  name = "Platinum",     row = "Metals"),
  list(symbol = "CL=F",  name = "WTI Crude",    row = "Energy"),
  list(symbol = "BZ=F",  name = "Brent Crude",  row = "Energy"),
  list(symbol = "NG=F",  name = "Nat Gas",      row = "Energy"),
  list(symbol = "RB=F",  name = "RBOB Gas",     row = "Energy"),
  list(symbol = "ZC=F",  name = "Corn",         row = "Softs"),
  list(symbol = "ZW=F",  name = "Wheat",        row = "Softs"),
  list(symbol = "ZS=F",  name = "Soybeans",     row = "Softs"),
  list(symbol = "KC=F",  name = "Coffee",       row = "Softs")
)

# Filter to requested category
COMMODITIES <- if (CATEGORY == "all") ALL_COMMODITIES else
               Filter(function(x) x$row == CATEGORY, ALL_COMMODITIES)

if (length(COMMODITIES) == 0) {
  stop(paste("Unknown category:", CATEGORY, "- use all, Metals, Energy, or Softs"))
}

# Row accent colours
ROW_COLORS <- c(Metals = "#c0c0c0", Energy = ORANGE, Softs = "#66cc66")

# Single-category title accent
TITLE_COLOR <- if (CATEGORY == "all") "white" else ROW_COLORS[[CATEGORY]]

##### Theme - base size scales up for 4-panel single-row view #####
BASE_SIZE <- if (CATEGORY == "all") 9 else 11

make_navy_theme <- function(accent_col) {
  theme_minimal(base_size = BASE_SIZE) +
    theme(
      plot.background  = element_rect(fill = BG,   color = NA),
      panel.background = element_rect(fill = BG,   color = NA),
      panel.grid.major = element_line(color = GRID, linewidth = 0.3),
      panel.grid.minor = element_blank(),
      axis.ticks       = element_line(color = GRID),
      axis.text        = element_text(color = "white",
                                      size = if (CATEGORY == "all") 6.5 else 8),
      axis.text.x      = element_text(color = "white",
                                      size = if (CATEGORY == "all") 6 else 7.5,
                                      angle = if (DAYS > 400) 0 else 45,
                                      hjust = if (DAYS > 400) 0.5 else 1),
      axis.title       = element_blank(),
      plot.title       = element_text(color = accent_col, hjust = 0.5,
                                      face = "bold",
                                      size = if (CATEGORY == "all") 10 else 13),
      plot.subtitle    = element_text(color = "white", hjust = 0.5,
                                      size = if (CATEGORY == "all") 7.5 else 9.5),
      plot.caption     = element_text(color = "white",
                                      size = if (CATEGORY == "all") 6.5 else 8),
      legend.position  = "none"
    )
}

##### Fetch + Cache #####
fetch_commodity <- function(symbol) {
  safe_sym   <- gsub("[^A-Za-z0-9]", "_", symbol)
  cache_file <- file.path(CACHE_DIR,
                          paste0("commodities_", safe_sym, "_", RANGE_STR, ".csv"))

  if (file.exists(cache_file)) {
    age <- as.numeric(difftime(Sys.time(), file.info(cache_file)$mtime, units = "secs"))
    if (age < CACHE_TTL) {
      cat("Cache hit:", symbol, "(age:", round(age), "s)\n")
      return(read_csv(cache_file, col_types = cols(), show_col_types = FALSE))
    }
  }

  cat("Fetching:", symbol, "\n")
  encoded <- URLencode(symbol, reserved = TRUE)
  url <- paste0(
    "https://query1.finance.yahoo.com/v8/finance/chart/",
    encoded, "?interval=1d&range=", RANGE_STR
  )

  tryCatch({
    resp <- GET(url, add_headers("User-Agent" = "Mozilla/5.0"))
    if (status_code(resp) != 200) {
      message("HTTP ", status_code(resp), " for ", symbol); return(NULL)
    }
    dat    <- fromJSON(content(resp, "text", encoding = "UTF-8"), simplifyVector = FALSE)
    result <- dat$chart$result[[1]]
    ts     <- unlist(result$timestamp)
    closes <- unlist(result$indicators$quote[[1]]$close)
    if (is.null(ts) || length(ts) == 0) return(NULL)
    n      <- min(length(ts), length(closes))
    df <- tibble(
      date  = as.Date(as.POSIXct(ts[seq_len(n)], origin = "1970-01-01", tz = "UTC")),
      close = as.numeric(closes[seq_len(n)])
    ) %>% filter(!is.na(close)) %>% arrange(date)
    write_csv(df, cache_file)
    df
  }, error = function(e) {
    message("Error fetching ", symbol, ": ", e$message); NULL
  })
}

##### Date-axis breaks by DAYS #####
x_breaks <- if (DAYS <= 120) "1 month" else if (DAYS <= 400) "3 months" else "1 year"
x_labels <- if (DAYS <= 400) "%b '%y" else "'%y"

##### Annotation size - larger for 4-panel view #####
ANNOT_SIZE <- if (CATEGORY == "all") 2.7 else 3.4

##### Build single panel #####
make_panel <- function(comm_def) {
  symbol     <- comm_def$symbol
  name       <- comm_def$name
  row_label  <- comm_def$row
  line_color <- ROW_COLORS[[row_label]]
  df         <- fetch_commodity(symbol)

  if (is.null(df) || nrow(df) < 2) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5,
                 label = paste0(name, "\nUnavailable"),
                 color = "white", size = 5, hjust = 0.5) +
        theme_void() +
        theme(plot.background = element_rect(fill = BG, color = GRID, linewidth = 0.5))
    )
  }

  latest      <- tail(df$close, 1)
  prev_close  <- df$close[nrow(df) - 1]
  pct_chg     <- (latest - prev_close) / prev_close * 100
  chg_color   <- if (pct_chg >= 0) "#00c853" else "#ff1744"
  chg_label   <- sprintf("%+.2f%%", pct_chg)
  price_label <- sprintf("%.2f", latest)

  y_rng <- range(df$close, na.rm = TRUE)
  y_pad <- diff(y_rng) * 0.14
  if (y_pad == 0) y_pad <- 1

  ggplot(df, aes(x = date, y = close)) +
    geom_line(color = line_color, linewidth = 0.85) +
    annotate("text",
             x = min(df$date) + as.numeric(diff(range(df$date))) * 0.02,
             y = y_rng[2] + y_pad * 0.25,
             label = paste0(price_label, "  ", chg_label),
             color = chg_color, size = ANNOT_SIZE, hjust = 0, fontface = "bold") +
    scale_x_date(date_breaks = x_breaks, date_labels = x_labels, minor_breaks = waiver()) +
    scale_y_continuous(labels = comma) +
    coord_cartesian(ylim = c(y_rng[1] - y_pad * 0.2, y_rng[2] + y_pad)) +
    labs(title = name) +
    make_navy_theme(line_color)
}

##### Build dashboard #####
range_str2 <- switch(RANGE_STR,
  "3mo" = "3 Months", "6mo" = "6 Months", "1y" = "1 Year",
  "2y"  = "2 Years",  "5y"  = "5 Years",  "10y" = "10 Years", RANGE_STR)

today_str <- format(Sys.Date(), "%B %d, %Y")

if (CATEGORY == "all") {
  cat("Building 12-panel commodities dashboard...\n")
  panels <- lapply(COMMODITIES, make_panel)
  row1 <- panels[[1]]  | panels[[2]]  | panels[[3]]  | panels[[4]]
  row2 <- panels[[5]]  | panels[[6]]  | panels[[7]]  | panels[[8]]
  row3 <- panels[[9]]  | panels[[10]] | panels[[11]] | panels[[12]]
  dashboard <- (row1 / row2 / row3) +
    plot_annotation(
      title    = "Commodities Dashboard",
      subtitle = paste0(today_str, " - ", range_str2,
                        "  |  Metals  |  Energy  |  Softs"),
      caption  = "Source: Yahoo Finance | JHCV",
      theme    = theme(
        plot.background = element_rect(fill = BG, color = NA),
        plot.title      = element_text(color = "white",      hjust = 0.5, face = "bold", size = 17),
        plot.subtitle   = element_text(color = "white",      hjust = 0.5, size = 11),
        plot.caption    = element_text(color = "white",      size = 9)
      )
    )
  out_w <- 1600 / 150
  out_h <- 1200 / 150

} else {
  cat("Building 4-panel", CATEGORY, "dashboard...\n")
  panels <- lapply(COMMODITIES, make_panel)
  row1 <- panels[[1]] | panels[[2]] | panels[[3]] | panels[[4]]
  dashboard <- row1 +
    plot_annotation(
      title    = paste0("Commodities - ", CATEGORY),
      subtitle = paste0(today_str, " - ", range_str2),
      caption  = "Source: Yahoo Finance | JHCV",
      theme    = theme(
        plot.background = element_rect(fill = BG, color = NA),
        plot.title      = element_text(color = TITLE_COLOR, hjust = 0.5, face = "bold", size = 17),
        plot.subtitle   = element_text(color = "white",     hjust = 0.5, size = 11),
        plot.caption    = element_text(color = "white",     size = 9)
      )
    )
  out_w <- 1400 / 150
  out_h <-  620 / 150
}

ggsave(OUTPUT, plot = dashboard, width = out_w, height = out_h, dpi = 150, bg = BG)
cat("Saved:", OUTPUT, "\n")
