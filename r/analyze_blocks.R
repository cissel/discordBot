# analyze_blocks.R
# -----------------
# Loads block_events.csv and block_outcomes.csv, tests whether price
# tends to gravitate toward large off-market block prints over
# various forward time windows.
#
# Tests performed:
#   1. Binomial test: does price reach block level > 50% of the time?
#   2. One-sample t-test: is mean % movement toward block level > 0?
#   3. Permutation test: is reach rate significantly above random?
#   4. Stratified analysis by direction (block above vs below market)
#   5. Stratified analysis by exchange (ADF vs lit)
#   6. Batch timestamp flagging and sensitivity analysis

library(tidyverse)
library(scales)
library(ggplot2)

##### Paths #####
OUT_DIR      <- "/Users/jamescissel/discordBot/outputs/research"
OUTCOMES_CSV <- file.path(OUT_DIR, "block_outcomes.csv")
EVENTS_CSV   <- file.path(OUT_DIR, "block_events.csv")
PLOTS_DIR    <- file.path(OUT_DIR, "plots")
dir.create(PLOTS_DIR, showWarnings = FALSE)

##### Theme #####
myTheme <- theme(
  legend.position   = "bottom",
  plot.background   = element_rect(fill = "#02233F"),
  panel.background  = element_rect(fill = "#02233F"),
  panel.grid        = element_line(color = "#274066"),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white"),
  axis.title        = element_text(color = "white"),
  plot.title        = element_text(color = "white", hjust = .5, size = 14),
  plot.subtitle     = element_text(color = "white", hjust = .5),
  plot.caption      = element_text(color = "#888888"),
  strip.background  = element_rect(fill = "#02233F"),
  strip.text        = element_text(color = "white"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "white"),
  legend.title      = element_text(color = "white")
)

##### Load Data #####
cat("Loading data...\n")

if (!file.exists(OUTCOMES_CSV)) {
  stop(paste("Outcomes file not found:", OUTCOMES_CSV,
             "\nRun pull_tape.py and pull_forward_bars.py first."))
}

# Load outcomes
df_out <- read.csv(OUTCOMES_CSV, stringsAsFactors = FALSE)

# Load events to get exchange and timestamp metadata
df_ev <- read.csv(EVENTS_CSV, stringsAsFactors = FALSE) |>
  mutate(
    time = as.POSIXct(time, format = "%Y-%m-%d %H:%M:%OS",
                      tz = "America/New_York"),
    time_min  = as.integer(format(time, "%M")),
    time_sec  = as.integer(format(time, "%S")),
    # Batch-reported: prints at exactly :00 or :30 minutes with zero seconds
    # These are almost always end-of-day tape batch reports, not real-time prints
    is_batch  = (time_min %in% c(0, 30)) & (time_sec == 0),
    exchange_type = ifelse(exchange == "D", "ADF (Dark Pool)", "Lit Exchange")
  ) |>
  select(time, exchange, exchange_type, is_batch)

# Join outcomes with event metadata on block_time
df <- df_out |>
  mutate(
    across(starts_with("reached_"), ~ .x == "True"),
    block_time      = as.POSIXct(block_time, format = "%Y-%m-%d %H:%M:%OS",
                                 tz = "America/New_York"),
    trade_date      = as.Date(trade_date),
    direction       = as.factor(direction),
    gap_pct         = abs(block_price - market_price) / market_price * 100,
    dollar_value_bn = dollar_value / 1e9,
    time_min        = as.integer(format(block_time, "%M")),
    time_sec        = as.integer(format(block_time, "%S")),
    is_batch        = (time_min %in% c(0, 30)) & (time_sec == 0),
    exchange_type   = ifelse(exchange == "D", "ADF (Dark Pool)", "Lit Exchange")
  )


cat(sprintf("Loaded %d block events.\n", nrow(df)))
cat(sprintf("Date range:  %s to %s\n", min(df$trade_date), max(df$trade_date)))
cat(sprintf("Direction:   %d above market / %d below market\n",
            sum(df$direction == "above_market"),
            sum(df$direction == "below_market")))
cat(sprintf("Exchange:    %d ADF (dark pool) / %d lit exchange\n",
            sum(df$exchange == "D", na.rm = TRUE),
            sum(df$exchange != "D", na.rm = TRUE)))
cat(sprintf("Batch flags: %d / %d events have suspicious timestamps\n\n",
            sum(df$is_batch, na.rm = TRUE), nrow(df)))

##### Helper: run all tests for one window on a subset #####
run_tests <- function(data, window_label, label = "") {
  reach_col  <- paste0("reached_", window_label)
  toward_col <- paste0("pct_toward_", window_label)
  
  reached <- data[[reach_col]]
  toward  <- data[[toward_col]]
  
  valid   <- !is.na(reached) & !is.na(toward)
  reached <- reached[valid]
  toward  <- toward[valid]
  
  n         <- length(reached)
  n_reached <- sum(reached)
  
  if (n < 5) {
    cat(sprintf("  [%s] %s window: insufficient data (n=%d), skipping.\n",
                label, toupper(window_label), n))
    return(invisible(NULL))
  }
  
  cat(sprintf("\n──── %s | %s window (n=%d) ────\n",
              ifelse(label == "", "ALL", label),
              toupper(window_label), n))
  cat(sprintf("  Reached block level: %d / %d (%.1f%%)\n",
              n_reached, n, n_reached / n * 100))
  
  # Binomial test
  binom <- binom.test(n_reached, n, p = 0.5, alternative = "greater")
  cat(sprintf("  Binomial test (H0: reach = 50%%): p = %.4f %s\n",
              binom$p.value,
              ifelse(binom$p.value < 0.05, "*** SIGNIFICANT", "")))
  
  # T-test
  tryCatch({
    ttest <- t.test(toward, mu = 0, alternative = "greater")
    cat(sprintf("  T-test %% toward (H0: mean = 0): mean = %.1f%%, p = %.4f %s\n",
                mean(toward, na.rm = TRUE),
                ttest$p.value,
                ifelse(ttest$p.value < 0.05, "*** SIGNIFICANT", "")))
  }, error = function(e) {
    cat(sprintf("  T-test skipped (data constant): mean = %.1f%%\n",
                mean(toward, na.rm = TRUE)))
  })
  
  # Permutation test
  set.seed(42)
  perm_rates  <- replicate(10000, mean(sample(c(TRUE, FALSE), n, replace = TRUE)))
  actual_rate <- n_reached / n
  perm_p      <- mean(perm_rates >= actual_rate)
  cat(sprintf("  Permutation test: p = %.4f %s\n",
              perm_p,
              ifelse(perm_p < 0.05, "*** SIGNIFICANT", "")))
  
  invisible(list(
    window          = window_label,
    label           = label,
    n               = n,
    n_reached       = n_reached,
    rate            = actual_rate,
    binom_p         = binom$p.value,
    perm_p          = perm_p,
    mean_pct_toward = mean(toward, na.rm = TRUE)
  ))
}

run_all_windows <- function(data, label = "") {
  windows <- c("1d", "3d", "1w", "2w", "1mo")
  results <- purrr::map(windows, ~run_tests(data, .x, label = label))
  results <- purrr::compact(results)
  if (length(results) == 0) return(NULL)
  bind_rows(purrr::map(results, as.data.frame)) |>
    mutate(label = label)
}

##### Run All Analyses #####
cat("═══════════════════════════════════════════════════════\n")
cat("  1. FULL SAMPLE\n")
cat("═══════════════════════════════════════════════════════\n")
res_all <- run_all_windows(df, "All Events")

cat("\n═══════════════════════════════════════════════════════\n")
cat("  2. STRATIFIED BY DIRECTION\n")
cat("═══════════════════════════════════════════════════════\n")
res_above <- run_all_windows(filter(df, direction == "above_market"), "Block Above Market")
res_below <- run_all_windows(filter(df, direction == "below_market"), "Block Below Market")

cat("\n═══════════════════════════════════════════════════════\n")
cat("  3. STRATIFIED BY EXCHANGE\n")
cat("═══════════════════════════════════════════════════════\n")
res_adf <- run_all_windows(filter(df, exchange == "D"),  "ADF Dark Pool")
res_lit <- run_all_windows(filter(df, exchange != "D"),  "Lit Exchange")

cat("\n═══════════════════════════════════════════════════════\n")
cat("  4. SENSITIVITY: EXCLUDING BATCH TIMESTAMPS\n")
cat("═══════════════════════════════════════════════════════\n")
df_clean <- filter(df, !is_batch | is.na(is_batch))
cat(sprintf("  Events after exclusion: %d / %d\n", nrow(df_clean), nrow(df)))
res_clean <- run_all_windows(df_clean, "Excl. Batch Timestamps")

cat("\n═══════════════════════════════════════════════════════\n")
cat("  5. SENSITIVITY: ADF ONLY, EXCLUDING BATCH TIMESTAMPS\n")
cat("═══════════════════════════════════════════════════════\n")
df_adf_clean <- filter(df, exchange == "D", !is_batch | is.na(is_batch))
cat(sprintf("  Events remaining: %d\n", nrow(df_adf_clean)))
res_adf_clean <- run_all_windows(df_adf_clean, "ADF Excl. Batch")

##### Compile Results #####
all_results <- bind_rows(
  res_all, res_above, res_below,
  res_adf, res_lit,
  res_clean, res_adf_clean
) |>
  mutate(
    window = factor(window,
                    levels = c("1d","3d","1w","2w","1mo"),
                    labels = c("1 Day","3 Days","1 Week","2 Weeks","1 Month"))
  )

##### Plot 1: Reach Rate — All vs ADF vs Lit #####
p1 <- all_results |>
  filter(label %in% c("All Events", "ADF Dark Pool", "Lit Exchange")) |>
  ggplot(aes(x = window, y = rate * 100, fill = label)) +
  geom_col(position = "dodge", width = 0.7) +
  geom_hline(yintercept = 50, color = "white", linetype = "dashed", linewidth = 0.7) +
  annotate("text", x = 0.6, y = 52.5, label = "Random (50%)",
           color = "white", size = 3, hjust = 0) +
  scale_fill_manual(values = c(
    "All Events"    = "#4FC3F7",
    "ADF Dark Pool" = "#FFB74D",
    "Lit Exchange"  = "#81C784"
  )) +
  scale_y_continuous(limits = c(0, 112), labels = function(x) paste0(x, "%")) +
  labs(
    title    = "SPY Block Print — Reach Rate by Exchange Type",
    subtitle = "Does price reach the block level within each forward window?",
    x = "Forward Window", y = "Reach Rate", fill = NULL,
    caption  = paste("n =", nrow(df), "total block events")
  ) +
  myTheme

ggsave(file.path(PLOTS_DIR, "reach_rate_by_exchange.png"), p1,
       width = 10, height = 5.5, dpi = 300)
cat("\nSaved: reach_rate_by_exchange.png\n")

##### Plot 2: Sensitivity — Batch Exclusion #####
p2 <- all_results |>
  filter(label %in% c("All Events", "Excl. Batch Timestamps", "ADF Excl. Batch")) |>
  ggplot(aes(x = window, y = rate * 100, fill = label)) +
  geom_col(position = "dodge", width = 0.7) +
  geom_hline(yintercept = 50, color = "white", linetype = "dashed", linewidth = 0.7) +
  scale_fill_manual(values = c(
    "All Events"             = "#4FC3F7",
    "Excl. Batch Timestamps" = "#FFB74D",
    "ADF Excl. Batch"        = "#CE93D8"
  )) +
  scale_y_continuous(limits = c(0, 112), labels = function(x) paste0(x, "%")) +
  labs(
    title    = "SPY Block Print — Sensitivity to Batch Timestamp Exclusion",
    subtitle = "Do results hold after removing suspicious batch-reported prints?",
    x = "Forward Window", y = "Reach Rate", fill = NULL,
    caption  = paste("Batch events excluded:", sum(df$is_batch, na.rm = TRUE))
  ) +
  myTheme

ggsave(file.path(PLOTS_DIR, "reach_rate_sensitivity.png"), p2,
       width = 10, height = 5.5, dpi = 300)
cat("Saved: reach_rate_sensitivity.png\n")

##### Plot 3: % Toward Block — ADF vs Lit #####
toward_long <- df |>
  select(exchange_type, is_batch, starts_with("pct_toward_")) |>
  pivot_longer(
    cols = starts_with("pct_toward_"),
    names_to = "window", values_to = "pct_toward"
  ) |>
  mutate(
    window = factor(str_remove(window, "pct_toward_"),
                    levels = c("1d","3d","1w","2w","1mo"),
                    labels = c("1 Day","3 Days","1 Week","2 Weeks","1 Month"))
  ) |>
  filter(!is.na(pct_toward), !is.na(exchange_type))

p3 <- ggplot(toward_long, aes(x = window, y = pct_toward, fill = exchange_type)) +
  geom_boxplot(alpha = 0.8, outlier.color = "white", outlier.size = 0.8) +
  geom_hline(yintercept = 0,   color = "white",   linetype = "dashed") +
  geom_hline(yintercept = 100, color = "#81C784", linetype = "dotted", linewidth = 0.6) +
  scale_fill_manual(values = c(
    "ADF (Dark Pool)" = "#FFB74D",
    "Lit Exchange"    = "#81C784"
  )) +
  scale_y_continuous(labels = function(x) paste0(x, "%")) +
  labs(
    title    = "SPY Block Print — % Movement Toward Block Price",
    subtitle = "ADF dark pool vs lit exchange prints",
    x = "Forward Window", y = "% of Gap Closed Toward Block Price",
    fill = "Exchange Type",
    caption  = "100% = price reached or exceeded block level"
  ) +
  myTheme

ggsave(file.path(PLOTS_DIR, "pct_toward_by_exchange.png"), p3,
       width = 10, height = 5.5, dpi = 300)
cat("Saved: pct_toward_by_exchange.png\n")

##### Plot 4: Event Timeline #####
p4 <- ggplot(df,
             aes(x = trade_date, y = gap_pct,
                 color = exchange_type,
                 shape = ifelse(is_batch, "Batch (suspicious)", "Normal"),
                 size  = dollar_value_bn)) +
  geom_point(alpha = 0.8) +
  scale_color_manual(values = c(
    "ADF (Dark Pool)" = "#FFB74D",
    "Lit Exchange"    = "#81C784"
  )) +
  scale_shape_manual(values = c("Batch (suspicious)" = 4, "Normal" = 16)) +
  scale_size_continuous(name = "Dollar Value ($B)", range = c(2, 9)) +
  scale_y_continuous(labels = function(x) paste0(x, "%")) +
  labs(
    title    = "SPY Block Events Over Time",
    subtitle = "X marks = suspicious batch-reported timestamps",
    x = "Date", y = "Deviation from Market Price (%)",
    color = "Exchange Type", shape = "Timestamp"
  ) +
  myTheme

ggsave(file.path(PLOTS_DIR, "event_timeline.png"), p4,
       width = 11, height = 5.5, dpi = 300)
cat("Saved: event_timeline.png\n")

##### Summary Table #####
cat("\n═══════════════════════════════════════════════════════\n")
cat("  SUMMARY TABLE\n")
cat("═══════════════════════════════════════════════════════\n")
print(
  all_results |>
    select(label, window, n, n_reached, rate, binom_p, perm_p, mean_pct_toward) |>
    mutate(
      rate            = scales::percent(rate, accuracy = 0.1),
      binom_p         = round(binom_p, 4),
      perm_p          = round(perm_p, 4),
      mean_pct_toward = round(mean_pct_toward, 1),
      sig             = case_when(
        binom_p < 0.01 | perm_p < 0.01 ~ "***",
        binom_p < 0.05 | perm_p < 0.05 ~ "**",
        binom_p < 0.10 | perm_p < 0.10 ~ "*",
        TRUE ~ ""
      )
    ) |>
    rename(
      Group           = label,
      Window          = window,
      N               = n,
      "N Reached"     = n_reached,
      "Reach Rate"    = rate,
      "Binomial p"    = binom_p,
      "Permut. p"     = perm_p,
      "Mean % Toward" = mean_pct_toward,
      Sig             = sig
    ),
  row.names = FALSE
)

##### Batch Event Detail #####
cat("\n═══════════════════════════════════════════════════════\n")
cat("  BATCH TIMESTAMP EVENTS (flagged for review)\n")
cat("═══════════════════════════════════════════════════════\n")
batch_events <- df |>
  filter(is_batch) |>
  select(trade_date, block_time, block_price, market_price,
         gap_pct, size, dollar_value_bn, exchange, direction)

if (nrow(batch_events) > 0) {
  print(batch_events, row.names = FALSE)
} else {
  cat("No batch timestamps detected.\n")
}

cat("\nAll plots saved to:", PLOTS_DIR, "\n")
cat("Analysis complete.\n")