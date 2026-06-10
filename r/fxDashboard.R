# fxDashboard.R - FX Dashboard (Major Pairs vs USD)
# Usage: Rscript fxDashboard.R [output_path]

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
CACHE_TTL  <- 3600

args   <- commandArgs(trailingOnly = TRUE)
OUTPUT <- if (length(args) >= 1) args[1] else
          path.expand("~/discordBot/outputs/markets/fxDashboard.png")

HISTORY_DAYS <- if (length(args) >= 2) as.integer(args[2]) else 90
CACHE_FILE <- path.expand(paste0("~/discordBot/outputs/markets/cache/fx_dashboard_", HISTORY_DAYS, "d.csv"))

dir.create(dirname(CACHE_FILE), showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(OUTPUT),     showWarnings = FALSE, recursive = TRUE)

##### Pair definitions #####
# Each entry: display name, how to derive rate vs USD
# Strategy A: base=USD, currency=XXX  => rate = XXX per USD  (USD/XXX convention)
# Strategy B: base=EUR, currency=USD  => rate = USD per EUR  (EUR/USD convention)
# Strategy C: base=GBP, currency=USD  => rate = USD per GBP  (GBP/USD convention)
# Strategy D: base=AUD, currency=USD  => rate = USD per AUD  (AUD/USD convention)
PAIRS <- list(
  list(pair = "EUR/USD", base = "EUR", target = "USD"),
  list(pair = "GBP/USD", base = "GBP", target = "USD"),
  list(pair = "USD/JPY", base = "USD", target = "JPY"),
  list(pair = "USD/CHF", base = "USD", target = "CHF"),
  list(pair = "USD/CAD", base = "USD", target = "CAD"),
  list(pair = "AUD/USD", base = "AUD", target = "USD"),
  list(pair = "USD/CNY", base = "USD", target = "CNY"),
  list(pair = "USD/MXN", base = "USD", target = "MXN")
)

# Group by base to minimize API calls
# Unique bases: USD, EUR, GBP, AUD
BASES        <- unique(sapply(PAIRS, `[[`, "base"))
# HISTORY_DAYS is set from args above

##### Theme #####
navy_theme <- theme_minimal(base_size = 10) +
  theme(
    plot.background   = element_rect(fill = BG,   color = NA),
    panel.background  = element_rect(fill = BG,   color = NA),
    panel.grid.major  = element_line(color = GRID, linewidth = 0.3),
    panel.grid.minor  = element_blank(),
    axis.ticks        = element_line(color = GRID),
    axis.text         = element_text(color = "white", size = 7),
    axis.title        = element_blank(),
    plot.title        = element_text(color = "white", hjust = 0.5,
                                     face = "bold", size = 11),
    plot.subtitle     = element_text(color = "white", hjust = 0.5, size = 9),
    plot.caption      = element_text(color = "white", size = 7),
    legend.position   = "none"
  )

##### Fetch history for one base currency #####
fetch_base_history <- function(base_cur, start_date, end_date) {
  url <- paste0(
    "https://api.frankfurter.dev/v1/",
    format(start_date, "%Y-%m-%d"), "..",
    format(end_date,   "%Y-%m-%d"),
    "?from=", base_cur
  )
  cat("Fetching FX history base=", base_cur, "\n")
  tryCatch({
    resp <- GET(url, add_headers("User-Agent" = "Mozilla/5.0"))
    if (status_code(resp) != 200) {
      message("HTTP ", status_code(resp), " for base=", base_cur)
      return(NULL)
    }
    dat   <- fromJSON(content(resp, "text", encoding = "UTF-8"),
                      simplifyVector = TRUE)
    rates <- dat$rates
    if (is.null(rates) || length(rates) == 0) return(NULL)

    # rates is a named list: date -> named numeric vector
    rows <- lapply(names(rates), function(d) {
      r <- rates[[d]]
      if (is.null(r) || length(r) == 0) return(NULL)
      tibble(
        date     = as.Date(d),
        base     = base_cur,
        currency = names(r),
        rate     = as.numeric(r)
      )
    })
    bind_rows(Filter(Negate(is.null), rows))
  }, error = function(e) {
    message("Error fetching base=", base_cur, ": ", e$message)
    NULL
  })
}

##### Load or fetch all FX data #####
load_or_fetch_fx <- function() {
  if (file.exists(CACHE_FILE)) {
    age <- as.numeric(difftime(Sys.time(),
                               file.info(CACHE_FILE)$mtime, units = "secs"))
    if (age < CACHE_TTL) {
      cat("Using cached FX data (age:", round(age), "s)\n")
      return(read_csv(CACHE_FILE, col_types = cols(), show_col_types = FALSE))
    }
  }

  end_date   <- Sys.Date()
  start_date <- end_date - HISTORY_DAYS

  all_data <- lapply(BASES, function(b) {
    fetch_base_history(b, start_date, end_date)
  })
  combined <- bind_rows(Filter(Negate(is.null), all_data))

  if (nrow(combined) == 0) {
    message("No FX data fetched.")
    return(NULL)
  }

  # Build pair-level data for the 8 pairs we need
  pair_rows <- lapply(PAIRS, function(pd) {
    sub <- combined %>%
      filter(base == pd$base, currency == pd$target) %>%
      arrange(date) %>%
      mutate(pair = pd$pair) %>%
      select(date, pair, rate)
    sub
  })
  df <- bind_rows(pair_rows)
  write_csv(df, CACHE_FILE)
  df
}

fx_data <- load_or_fetch_fx()

if (is.null(fx_data) || nrow(fx_data) == 0) {
  stop("No FX data available.")
}

##### Build single panel #####
make_fx_panel <- function(pair_name) {
  df <- fx_data %>% filter(pair == pair_name) %>% arrange(date)

  if (nrow(df) < 2) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5,
                 label = paste0(pair_name, "\nUnavailable"),
                 color = "white", size = 5, hjust = 0.5) +
        theme_void() +
        theme(plot.background = element_rect(fill = BG, color = GRID,
                                             linewidth = 0.5))
    )
  }

  latest     <- tail(df$rate, 1)
  prev_rate  <- df$rate[nrow(df) - 1]
  pct_chg    <- (latest - prev_rate) / prev_rate * 100
  chg_color  <- if (pct_chg >= 0) "#00c853" else "#ff1744"

  # Determine decimal places by magnitude
  dec <- if (latest >= 100) 2 else if (latest >= 1) 4 else 5
  fmt_rate <- function(x) formatC(x, digits = dec, format = "f")

  rate_label <- fmt_rate(latest)
  chg_label  <- sprintf("%+.3f%%", pct_chg)

  y_rng <- range(df$rate, na.rm = TRUE)
  y_pad <- diff(y_rng) * 0.12
  if (y_pad == 0) y_pad <- latest * 0.01

  p <- ggplot(df, aes(x = date, y = rate)) +
    geom_line(color = ACCENT, linewidth = 0.9) +
    annotate("text",
             x = min(df$date) + diff(range(df$date)) * 0.02,
             y = y_rng[2] + y_pad * 0.5,
             label = paste0(rate_label, "  ", chg_label),
             color = chg_color, size = 3, hjust = 0,
             fontface = "bold") +
    scale_x_date(
      date_breaks = if (HISTORY_DAYS <= 120) "1 month" else if (HISTORY_DAYS <= 400) "3 months" else if (HISTORY_DAYS <= 800) "6 months" else "1 year",
      date_labels = if (HISTORY_DAYS <= 400) "%b '%y" else "'%y",
      minor_breaks = waiver()
    ) +
    scale_y_continuous(labels = function(x) formatC(x, digits = dec, format = "f")) +
    coord_cartesian(ylim = c(y_rng[1] - y_pad, y_rng[2] + y_pad)) +
    labs(title = pair_name) +
    navy_theme

  p
}

##### Build dashboard #####
cat("Building FX dashboard...\n")
pair_names <- sapply(PAIRS, `[[`, "pair")
panels     <- lapply(pair_names, make_fx_panel)

period_label <- if (HISTORY_DAYS <= 90) "3M" else if (HISTORY_DAYS <= 180) "6M" else if (HISTORY_DAYS <= 365) "1Y" else if (HISTORY_DAYS <= 730) "2Y" else if (HISTORY_DAYS <= 1825) "5Y" else "10Y"
today_str <- paste0(format(Sys.Date(), "%B %d, %Y"), " (", period_label, ")")

dashboard <- (panels[[1]] | panels[[2]] | panels[[3]] | panels[[4]]) /
             (panels[[5]] | panels[[6]] | panels[[7]] | panels[[8]]) +
  plot_annotation(
    title    = "FX Dashboard",
    subtitle = today_str,
    caption  = "Source: Frankfurter (ECB) | JHCV",
    theme    = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title      = element_text(color = "white", hjust = 0.5,
                                     face = "bold", size = 18),
      plot.subtitle   = element_text(color = "white", hjust = 0.5, size = 12),
      plot.caption    = element_text(color = "white", size = 9)
    )
  )

ggsave(
  OUTPUT, plot = dashboard,
  width = 1400 / 150, height = 900 / 150, dpi = 150,
  bg = BG
)
cat("Saved:", OUTPUT, "\n")
