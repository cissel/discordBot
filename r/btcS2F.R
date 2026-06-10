# ============================================================
#  BTC Stock-to-Flow (S2F) Power Law Chart
#  Model: ln(price) = a + b * ln(S2F)  (PlanB original)
#  Data: CoinMetrics Community API (no key)
# ============================================================

library(ggplot2)
library(dplyr)
library(readr)
library(scales)
library(httr)
library(jsonlite)

args     <- commandArgs(trailingOnly = TRUE)
out_path <- if (length(args) >= 1) args[1] else
  path.expand("~/discordBot/outputs/markets/btcS2F.png")

dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

# ── fetch / cache daily price ─────────────────────────────────────────────────
cache_path <- path.expand("~/discordBot/outputs/markets/cache/BTC_s2f_daily.csv")
dir.create(dirname(cache_path), recursive = TRUE, showWarnings = FALSE)

needs_fetch <- TRUE
if (file.exists(cache_path)) {
  age_hours <- as.numeric(difftime(Sys.time(), file.mtime(cache_path), units = "hours"))
  if (age_hours < 24) needs_fetch <- FALSE
}

if (needs_fetch) {
  api_url <- paste0(
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "?assets=btc&metrics=PriceUSD&frequency=1d",
    "&start_time=2010-07-18&page_size=10000"
  )
  resp <- tryCatch(GET(api_url, timeout(30)), error = function(e) NULL)
  if (is.null(resp) || status_code(resp) != 200) stop("Failed to fetch CoinMetrics data")
  raw   <- content(resp, as = "text", encoding = "UTF-8")
  parsed <- fromJSON(raw)
  df_raw <- as.data.frame(parsed$data)
  df_raw <- df_raw %>%
    rename(date = time, price = PriceUSD) %>%
    mutate(date  = as.Date(substr(date, 1, 10)),
           price = as.numeric(price)) %>%
    filter(!is.na(price)) %>%
    arrange(date)
  write_csv(df_raw, cache_path)
} else {
  df_raw <- read_csv(cache_path, show_col_types = FALSE) %>%
    mutate(date = as.Date(date))
}

# ── halving schedule (block reward halving dates + subsidy) ──────────────────
# Includes two future halvings: ~Apr 2028, ~Apr 2032 (estimated ~4yr intervals)
halvings <- data.frame(
  date    = as.Date(c("2009-01-03", "2012-11-28", "2016-07-09",
                       "2020-05-11", "2024-04-20",
                       "2028-04-18", "2032-04-15")),
  subsidy = c(50, 25, 12.5, 6.25, 3.125, 1.5625, 0.78125),
  future  = c(FALSE, FALSE, FALSE, FALSE, FALSE, TRUE, TRUE)
)

# ── build S2F series ──────────────────────────────────────────────────────────
# Supply at date t: cumulative blocks * subsidy in each era
# Simplified: use linear issuance per era (144 blocks/day)
BLOCKS_PER_DAY <- 144

get_subsidy <- function(d) {
  d <- as.Date(d)
  halvings$subsidy[max(which(halvings$date <= d))]
}

# cumulative supply (approx) - start from genesis
df <- df_raw %>%
  mutate(
    subsidy    = sapply(date, get_subsidy),
    # days since genesis
    days_since = as.numeric(date - as.Date("2009-01-03")),
    # approximate circulating supply using era-based issuance
    supply     = sapply(date, function(d) {
      d <- as.Date(d)
      supply_sum <- 0
      for (i in seq_len(nrow(halvings))) {
        era_start <- halvings$date[i]
        era_end   <- if (i < nrow(halvings)) halvings$date[i + 1] - 1 else d
        era_end   <- min(era_end, d)
        if (era_end < era_start) next
        days_in_era <- as.numeric(era_end - era_start) + 1
        supply_sum  <- supply_sum + days_in_era * BLOCKS_PER_DAY * halvings$subsidy[i]
      }
      supply_sum
    }),
    # annual new issuance = subsidy * blocks/day * 365
    issuance   = subsidy * BLOCKS_PER_DAY * 365,
    s2f        = supply / issuance
  ) %>%
  filter(s2f > 0, price > 0)

# ── fit log-linear S2F model: ln(price) = a + b*ln(s2f) ─────────────────────
# Use data from 2012 onwards (enough supply history)
df_fit <- df %>% filter(date >= as.Date("2012-01-01"))
fit    <- lm(log(price) ~ log(s2f), data = df_fit)
a_coef <- coef(fit)[1]
b_coef <- coef(fit)[2]

df <- df %>%
  mutate(s2f_model = exp(a_coef + b_coef * log(s2f)))

# ── forecast: extend model through next 2 halvings (to end of H6 era) ────────
forecast_end <- as.Date("2036-04-01")   # well past H6 halving
forecast_dates <- seq(max(df$date) + 1, forecast_end, by = "day")

# compute supply + S2F for each future date (same supply calc, halvings now includes future)
all_halvings <- halvings   # already includes 2028/2032

get_supply_future <- function(d) {
  d <- as.Date(d)
  supply_sum <- 0
  for (i in seq_len(nrow(all_halvings))) {
    era_start <- all_halvings$date[i]
    era_end   <- if (i < nrow(all_halvings)) all_halvings$date[i + 1] - 1 else d
    era_end   <- min(era_end, d)
    if (era_end < era_start) next
    days_in_era <- as.numeric(era_end - era_start) + 1
    supply_sum  <- supply_sum + days_in_era * BLOCKS_PER_DAY * all_halvings$subsidy[i]
  }
  supply_sum
}

get_subsidy_future <- function(d) {
  d <- as.Date(d)
  all_halvings$subsidy[max(which(all_halvings$date <= d))]
}

df_forecast <- data.frame(date = forecast_dates) %>%
  mutate(
    supply   = sapply(date, get_supply_future),
    subsidy  = sapply(date, get_subsidy_future),
    issuance = subsidy * BLOCKS_PER_DAY * 365,
    s2f      = supply / issuance,
    s2f_model_forecast = exp(a_coef + b_coef * log(s2f))
  )

# peak model price per future halving era (for annotation)
era_peaks <- data.frame(
  halving = c("H5 (~2028)", "H6 (~2032)"),
  date    = as.Date(c("2028-04-18", "2032-04-15")),
  subsidy = c(1.5625, 0.78125)
) %>%
  mutate(
    supply_at  = sapply(date, get_supply_future),
    issuance_at = subsidy * BLOCKS_PER_DAY * 365,
    s2f_at     = supply_at / issuance_at,
    model_peak = exp(a_coef + b_coef * log(s2f_at))
  )

# ── color price by deviation from model ──────────────────────────────────────
df <- df %>%
  mutate(
    ratio      = price / s2f_model,
    color_score = pmin(pmax(log(ratio) / log(10), -1), 1)  # clamp -1..1
  )

# current stats
latest       <- tail(df, 1)
current_price <- latest$price
model_price   <- latest$s2f_model
current_s2f   <- latest$s2f
pct_vs_model  <- round((current_price / model_price - 1) * 100, 1)
direction     <- if (pct_vs_model >= 0) "above" else "below"

# ── halving vertical lines (past + future) ───────────────────────────────────
halving_lines_past   <- halvings %>% filter(!future, date >= min(df$date))
halving_lines_future <- halvings %>% filter(future)

# ── theme ─────────────────────────────────────────────────────────────────────
myTheme <- theme(
  plot.background  = element_rect(fill = "#02233F", color = NA),
  panel.background = element_rect(fill = "#02233F"),
  panel.grid       = element_line(color = "#274066"),
  axis.ticks       = element_line(color = "#274066"),
  axis.text        = element_text(color = "white", size = 9),
  axis.title       = element_text(color = "white"),
  plot.title       = element_text(color = "white", hjust = 0.5, size = 13),
  plot.subtitle    = element_text(color = "white", hjust = 0.5, size = 10),
  plot.caption     = element_text(color = "white", size = 8),
  legend.background = element_rect(fill = "#02233F"),
  legend.text      = element_text(color = "white"),
  legend.title     = element_text(color = "white"),
  legend.key       = element_rect(fill = "#02233F")
)

# ── plot ──────────────────────────────────────────────────────────────────────
y_label_pos <- max(df$price) * 3   # position for halving labels

p <- ggplot(df, aes(x = date)) +
  # past halving markers (solid gold dashed)
  geom_vline(data = halving_lines_past,
             aes(xintercept = date),
             color = "#FFD700", linetype = "dashed", linewidth = 0.4, alpha = 0.7) +
  # future halving markers (dimmer, dotted)
  geom_vline(data = halving_lines_future,
             aes(xintercept = date),
             color = "#FFD700", linetype = "dotted", linewidth = 0.4, alpha = 0.45) +
  # actual price (colored by model deviation)
  geom_line(aes(y = price, color = color_score), linewidth = 0.6) +
  scale_color_gradientn(
    colors = c("#ef5350", "#ff9800", "#FFD700", "#26a69a", "#1565C0"),
    values = scales::rescale(c(-1, -0.3, 0, 0.3, 1)),
    name   = "vs Model",
    breaks = c(-1, 0, 1),
    labels = c("90% below", "At model", "10x above"),
    guide  = guide_colorbar(barwidth = 0.5, barheight = 6,
                             title.position = "top", title.hjust = 0.5)
  ) +
  # historical S2F model line
  geom_line(aes(y = s2f_model), color = "white", linewidth = 0.9,
            linetype = "solid", alpha = 0.85) +
  # forecast model line (dashed, slightly dimmer)
  geom_line(data = df_forecast, aes(x = date, y = s2f_model_forecast),
            color = "white", linewidth = 0.8, linetype = "dashed", alpha = 0.55,
            inherit.aes = FALSE) +
  # forecast era peak annotations
  geom_point(data = era_peaks, aes(x = date, y = model_peak),
             color = "#FFD700", size = 2.5, shape = 18, inherit.aes = FALSE) +
  geom_text(data = era_peaks,
            aes(x = date, y = model_peak,
                label = paste0(halving, "\n$",
                               formatC(model_peak, format = "f", digits = 0, big.mark = ","))),
            color = "#FFD700", size = 2.8, hjust = -0.12, vjust = 0.5,
            inherit.aes = FALSE) +
  # current price dot
  geom_point(data = latest, aes(x = date, y = price),
             color = "white", size = 3, shape = 21,
             fill = "#FFD700", stroke = 1.5) +
  # past halving labels
  geom_text(data = halving_lines_past,
            aes(x = date, y = y_label_pos,
                label = paste0("H", seq_len(nrow(halving_lines_past)))),
            color = "#FFD700", size = 3, vjust = 0, hjust = -0.2) +
  scale_y_log10(
    labels = scales::dollar_format(largest_with_cents = 1),
    breaks = c(0.1, 1, 10, 100, 1000, 10000, 100000, 1000000, 10000000),
    limits = c(0.05, NA)
  ) +
  scale_x_date(date_breaks = "2 years", date_labels = "%Y",
               limits = c(min(df$date), as.Date("2036-06-01"))) +
  labs(
    title    = "BTC Stock-to-Flow (S2F) Power Law Model",
    subtitle = paste0(
      "$", formatC(current_price, format = "f", digits = 0, big.mark = ","),
      " | S2F: ", round(current_s2f, 0),
      " | Model: $", formatC(model_price, format = "f", digits = 0, big.mark = ","),
      " (", ifelse(pct_vs_model >= 0, "+", ""), pct_vs_model, "% ", direction, ")"
    ),
    x       = NULL,
    y       = "Price (USD, log scale)",
    caption = "Model: ln(P) = a + b*ln(S2F) | Dashed = model forecast | Dotted lines = future halvings | Source: CoinMetrics | JHCV"
  ) +
  myTheme +
  theme(legend.position = "right")

ggsave(out_path, p, width = 10, height = 6, dpi = 300)
