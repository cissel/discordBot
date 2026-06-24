#!/usr/bin/env Rscript
# marketForecast.R - animated forecast GIF for stocks, crypto, and FRED macro
#
# Usage: Rscript marketForecast.R <csv_path> <category> <symbol> <horizon_bars>
#                                  <model_type> <mc_sims> <out_gif_path> <display_symbol>
#
# category  : stocks | crypto | economic
# model_type: GJR-GARCH(1,1)-t | EGARCH(1,1)-t | auto.ARIMA (SARIMA)
# mc_sims   : 500 recommended

suppressPackageStartupMessages({
  library(tidyverse)
  library(scales)
  library(gganimate)
  library(gifski)
  library(rugarch)
  library(forecast)
  library(lubridate)
  library(zoo)
})

# ── args ──────────────────────────────────────────────────────────────────────

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 8) {
  stop("Usage: Rscript marketForecast.R <csv_path> <category> <symbol> <horizon_bars> <model_type> <mc_sims> <out_gif_path> <display_symbol>")
}

csv_path     <- args[1]
category     <- args[2]
symbol       <- args[3]
horizon_bars <- as.integer(args[4])
model_type   <- args[5]
mc_sims      <- as.integer(args[6])
out_gif_path <- args[7]
display_sym  <- args[8]

cat(sprintf("[marketForecast] category=%s symbol=%s horizon=%d sims=%d\n",
            category, display_sym, horizon_bars, mc_sims))

# ── theme ─────────────────────────────────────────────────────────────────────

BG      <- "#02233F"
GRID    <- "#274066"
CYAN    <- "#00FFFF"
WHITE   <- "#FFFFFF"
ORANGE  <- "#FF8C00"
RED     <- "#FF4444"
GREEN   <- "#44FF88"
YELLOW  <- "#FFD700"

myTheme <- theme(
  plot.background   = element_rect(fill = BG, color = NA),
  panel.background  = element_rect(fill = BG, color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.3),
  panel.grid.minor  = element_line(color = GRID, linewidth = 0.15),
  axis.ticks        = element_line(color = GRID),
  axis.text         = element_text(color = WHITE, size = 8),
  axis.title        = element_text(color = WHITE, size = 9),
  plot.title        = element_text(color = WHITE, size = 11, hjust = 0.5, face = "bold"),
  plot.subtitle     = element_text(color = WHITE, size = 9,  hjust = 0.5),
  plot.caption      = element_text(color = GRID,  size = 7,  hjust = 1),
  legend.background = element_rect(fill = BG, color = NA),
  legend.text       = element_text(color = WHITE, size = 8),
  legend.title      = element_text(color = WHITE, size = 8),
  strip.background  = element_rect(fill = GRID, color = NA),
  strip.text        = element_text(color = WHITE, size = 8)
)

# ── load data ─────────────────────────────────────────────────────────────────

df <- read_csv(csv_path, show_col_types = FALSE)

# normalize date/time column name
if ("timestamp" %in% names(df)) {
  df <- df |> rename(date = timestamp)
}

df$date <- as.POSIXct(df$date, tz = "America/New_York")

# ── dispatch by category ──────────────────────────────────────────────────────

if (category %in% c("stocks", "crypto")) {

  # ── log returns ─────────────────────────────────────────────────────────────
  df <- df |>
    arrange(date) |>
    mutate(log_ret = log(close / lag(close))) |>
    filter(!is.na(log_ret))

  if (nrow(df) < 50) {
    stop("Not enough data to fit model (need at least 50 bars after differencing)")
  }

  returns_vec <- df$log_ret
  price_vec   <- df$close
  dates_vec   <- df$date
  last_price  <- tail(price_vec, 1)
  last_date   <- tail(dates_vec, 1)

  # ── GARCH model spec ─────────────────────────────────────────────────────────
  # GJR-GARCH for stocks (captures leverage effect - negative shocks raise vol more)
  # EGARCH for crypto (handles extreme kurtosis better via log variance)
  # If model_type is NNETAR, skip GARCH entirely and go straight to nnetar branch

  if (model_type == "NNETAR") {

    cat("[marketForecast] Fitting NNETAR model...\n")
    suppressPackageStartupMessages({ library(forecast) })

    price_ts <- ts(price_vec, frequency = 1)

    # single fit on full dataset - rolling origin is too slow for NNETAR on Pi
    nn_fit <- nnetar(price_ts, size = 10, repeats = 20)

    cat(sprintf("[marketForecast] Generating NNETAR forecast h=%d with noise simulation...\n", horizon_bars))

    # point forecast for median line
    nn_fcst <- tryCatch(
      forecast(nn_fit, h = horizon_bars),
      error = function(e) { cat(sprintf("[marketForecast] NNETAR forecast failed: %s\n", e$message)); NULL }
    )
    if (is.null(nn_fcst)) stop("NNETAR forecast failed")

    # simulate paths by injecting residual noise - fast alternative to PI bootstrap
    # scales uncertainty with sqrt(h) like a random walk, grounded in model residuals
    bar_seconds <- as.numeric(difftime(dates_vec[min(10, nrow(df))],
                                       dates_vec[1], units = "secs")) /
                   (min(10, nrow(df)) - 1)

    future_dates <- seq(last_date + bar_seconds,
                        by = bar_seconds,
                        length.out = horizon_bars)

    resid_sd   <- sd(residuals(nn_fit), na.rm = TRUE)
    n_paths    <- 200
    set.seed(42)
    sim_mat    <- matrix(NA, nrow = horizon_bars, ncol = n_paths)
    point_fcst <- as.numeric(nn_fcst$mean)

    for (s in 1:n_paths) {
      noise <- cumsum(rnorm(horizon_bars, mean = 0, sd = resid_sd))
      sim_mat[, s] <- point_fcst + noise
    }

    # convert simulated return paths to price paths
    pct_mat <- apply(sim_mat, 1, function(x)
      quantile(x, probs = c(0.10, 0.25, 0.50, 0.75, 0.90), na.rm = TRUE))

    bands <- data.frame(
      date = future_dates,
      p50  = point_fcst,
      lo50 = pct_mat[2, ],
      hi50 = pct_mat[4, ],
      lo80 = pct_mat[1, ],
      hi80 = pct_mat[5, ]
    )

    # animate paths drawing forward (same style as GARCH MC paths animation)
    hist_tail <- tail(df, min(60, nrow(df)))

    n_frames   <- min(horizon_bars, 60)
    frame_step <- max(1, floor(horizon_bars / n_frames))
    frame_ids  <- seq(frame_step, horizon_bars, by = frame_step)
    if (tail(frame_ids, 1) != horizon_bars) frame_ids <- c(frame_ids, horizon_bars)

    anim_bands <- data.frame()
    for (fi in seq_along(frame_ids)) {
      fid <- frame_ids[fi]
      anim_bands <- rbind(anim_bands, bands[1:fid, ] |> mutate(frame = fi))
    }

    hist_rep <- do.call(rbind, lapply(seq_along(frame_ids), function(fi) {
      hist_tail |> mutate(frame = fi)
    }))

    p_nn <- ggplot() +

      geom_line(data = hist_rep,
                aes(x = date, y = close),
                color = CYAN, linewidth = 0.8) +

      geom_ribbon(data = anim_bands,
                  aes(x = date, ymin = lo80, ymax = hi80),
                  fill = WHITE, alpha = 0.08) +

      geom_ribbon(data = anim_bands,
                  aes(x = date, ymin = lo50, ymax = hi50),
                  fill = CYAN, alpha = 0.15) +

      geom_line(data = anim_bands,
                aes(x = date, y = p50),
                color = ORANGE, linewidth = 1.2, linetype = "dashed") +

      labs(
        title    = sprintf("%s - NNETAR Forecast", display_sym),
        subtitle = sprintf("Neural Network AR | residual noise bands | %d paths", n_paths),
        x        = NULL,
        y        = "Price",
        caption  = "Source: Alpaca Markets | Not financial advice | JHCV"
      ) +

      scale_y_continuous(labels = comma) +
      myTheme +
      transition_manual(frame)

    cat("[marketForecast] Rendering NNETAR animation...\n")
    nn_gif <- sub("\\.gif$", "_nnetar.gif", out_gif_path)
    animate(p_nn,
            nframes  = length(frame_ids),
            fps      = 12,
            width    = 800,
            height   = 450,
            renderer = gifski_renderer(nn_gif),
            res      = 96)
    cat(sprintf("[marketForecast] NNETAR animation saved: %s\n", nn_gif))

    cat(sprintf("OUTPUT_JSON:%s\n",
      jsonlite::toJSON(list(
        gif1       = nn_gif,
        gif2       = nn_gif,
        distpng    = "",
        p50_end    = round(tail(bands$p50,  1), 4),
        p10_end    = round(tail(bands$lo80, 1), 4),
        p90_end    = round(tail(bands$hi80, 1), 4),
        p25_end    = round(tail(bands$lo50, 1), 4),
        p75_end    = round(tail(bands$hi50, 1), 4),
        last_price = round(tail(price_vec, 1), 4),
        mu_ret     = round(mean(returns_vec), 6),
        sig_ret    = round(sd(returns_vec), 6),
        kurt       = round(mean(((returns_vec - mean(returns_vec)) / sd(returns_vec))^4) - 3, 4)
      ), auto_unbox = TRUE)))

  } else {   # end NNETAR branch - begin GARCH branch

  if (model_type == "EGARCH(1,1)-t") {
    spec <- ugarchspec(
      variance.model   = list(model = "eGARCH", garchOrder = c(1, 1)),
      mean.model       = list(armaOrder = c(1, 0), include.mean = TRUE),
      distribution.model = "std"   # student-t innovations
    )
  } else {
    # GJR-GARCH (default for stocks)
    spec <- ugarchspec(
      variance.model   = list(model = "gjrGARCH", garchOrder = c(1, 1)),
      mean.model       = list(armaOrder = c(1, 0), include.mean = TRUE),
      distribution.model = "std"
    )
  }

  cat("[marketForecast] Fitting GARCH model...\n")
  fit <- tryCatch(
    ugarchfit(spec = spec, data = returns_vec, solver = "hybrid"),
    error = function(e) {
      cat(sprintf("[marketForecast] WARN: hybrid solver failed (%s), trying nlminb\n", e$message))
      ugarchfit(spec = spec, data = returns_vec, solver = "nlminb")
    }
  )

  # extract fitted conditional volatility for display
  fitted_sigma <- sigma(fit)
  fitted_dates <- dates_vec

  # ── Monte Carlo simulation ────────────────────────────────────────────────────
  cat(sprintf("[marketForecast] Running %d MC simulations x %d steps...\n",
              mc_sims, horizon_bars))

  set.seed(42)
  sim <- ugarchsim(fit, n.sim = horizon_bars, n.start = 0,
                   m.sim = mc_sims, startMethod = "sample")

  sim_rets <- fitted(sim)   # matrix: horizon_bars x mc_sims

  # convert returns to price paths
  # P_t = P_0 * exp(sum of log returns)
  sim_prices <- matrix(NA, nrow = horizon_bars + 1, ncol = mc_sims)
  sim_prices[1, ] <- last_price

  for (s in 1:mc_sims) {
    for (t in 2:(horizon_bars + 1)) {
      sim_prices[t, s] <- sim_prices[t-1, s] * exp(sim_rets[t-1, s])
    }
  }

  # build future date sequence matching bar frequency
  bar_seconds <- as.numeric(difftime(dates_vec[min(10, nrow(df))],
                                     dates_vec[1], units = "secs")) /
                 (min(10, nrow(df)) - 1)

  future_dates <- seq(last_date + bar_seconds,
                      by = bar_seconds,
                      length.out = horizon_bars)

  # ── percentile bands ──────────────────────────────────────────────────────────
  pct_rows <- apply(sim_prices[-1, ], 1, function(x)
    quantile(x, probs = c(0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)))

  bands <- data.frame(
    date = future_dates,
    p05  = pct_rows[1, ],
    p10  = pct_rows[2, ],
    p25  = pct_rows[3, ],
    p50  = pct_rows[4, ],
    p75  = pct_rows[5, ],
    p90  = pct_rows[6, ],
    p95  = pct_rows[7, ]
  )

  # ── individual path data (sample 100 for viz to keep GIF small) ──────────────
  path_sample <- min(100, mc_sims)
  path_idx    <- sample(1:mc_sims, path_sample)

  paths_long <- data.frame()
  for (s in seq_along(path_idx)) {
    tmp <- data.frame(
      date  = future_dates,
      price = sim_prices[-1, path_idx[s]],
      sim   = s
    )
    paths_long <- rbind(paths_long, tmp)
  }

  # ── returns distribution data ─────────────────────────────────────────────────
  # fit student-t to returns for overlay
  t_fit <- tryCatch(
    MASS::fitdistr(returns_vec, "t"),
    error = function(e) NULL
  )

  ret_hist_df <- data.frame(log_ret = returns_vec)

  # ── ANIMATION 1: MC paths fanning out (real-time draw) ───────────────────────
  cat("[marketForecast] Building animation 1 (MC paths)...\n")

  # historical price tail (last ~60 bars or all if shorter)
  hist_tail <- tail(df, min(60, nrow(df)))

  # for animation: reveal paths progressively
  n_frames   <- min(horizon_bars, 60)   # cap frames for file size
  frame_step <- max(1, floor(horizon_bars / n_frames))
  frame_ids  <- seq(frame_step, horizon_bars, by = frame_step)
  if (tail(frame_ids, 1) != horizon_bars) frame_ids <- c(frame_ids, horizon_bars)

  anim_paths <- data.frame()
  anim_bands <- data.frame()

  for (fi in seq_along(frame_ids)) {
    fid  <- frame_ids[fi]
    tmp_paths <- paths_long |> filter(date <= future_dates[fid]) |>
      mutate(frame = fi)
    tmp_bands <- bands[1:fid, ] |> mutate(frame = fi)
    anim_paths <- rbind(anim_paths, tmp_paths)
    anim_bands <- rbind(anim_bands, tmp_bands)
  }

  # static historical data (same for all frames)
  hist_rep <- do.call(rbind, lapply(seq_along(frame_ids), function(fi) {
    hist_tail |> mutate(frame = fi)
  }))

  p_anim1 <- ggplot() +

    # historical price
    geom_line(data = hist_rep,
              aes(x = date, y = close),
              color = CYAN, linewidth = 0.8) +

    # 90% band
    geom_ribbon(data = anim_bands,
                aes(x = date, ymin = p05, ymax = p95),
                fill = WHITE, alpha = 0.06) +

    # 80% band
    geom_ribbon(data = anim_bands,
                aes(x = date, ymin = p10, ymax = p90),
                fill = WHITE, alpha = 0.08) +

    # 50% IQR band
    geom_ribbon(data = anim_bands,
                aes(x = date, ymin = p25, ymax = p75),
                fill = CYAN, alpha = 0.15) +

    # individual paths (faint)
    geom_line(data = anim_paths,
              aes(x = date, y = price, group = sim),
              color = WHITE, alpha = 0.04, linewidth = 0.3) +

    # median path
    geom_line(data = anim_bands,
              aes(x = date, y = p50),
              color = ORANGE, linewidth = 1.0, linetype = "dashed") +

    labs(
      title    = sprintf("%s - Monte Carlo Price Forecast", display_sym),
      subtitle = sprintf("GJR-GARCH(1,1) + student-t | %d paths | 50/80/90%% bands", mc_sims),
      x        = NULL,
      y        = "Price",
      caption  = "Model: rugarch | Source: Alpaca Markets | Not financial advice | JHCV"
    ) +

    scale_y_continuous(labels = comma) +

    myTheme +

    transition_manual(frame)

  cat("[marketForecast] Rendering animation 1...\n")
  gif1_path <- sub("\\.gif$", "_mcpaths.gif", out_gif_path)
  animate(p_anim1,
          nframes   = length(frame_ids),
          fps       = 12,
          width     = 800,
          height    = 450,
          renderer  = gifski_renderer(gif1_path),
          res       = 96)
  cat(sprintf("[marketForecast] Animation 1 saved: %s\n", gif1_path))

  # ── ANIMATION 2: rolling forecast origin (fcstanim style) ────────────────────
  cat("[marketForecast] Building animation 2 (rolling origin)...\n")

  # step through history weekly (every 5 bars for daily, every ~20 for intraday)
  bar_step   <- max(1, round(nrow(df) / 40))   # ~40 animation frames from history
  origin_idx <- seq(max(100, bar_step), nrow(df), by = bar_step)
  if (tail(origin_idx, 1) != nrow(df)) origin_idx <- c(origin_idx, nrow(df))

  rolling_bands <- data.frame()   # forecast cones - accumulate across origins
  rolling_actuals <- data.frame() # actual prices over forecast windows - accumulate
  rolling_hist  <- data.frame()   # historical price up to each origin

  # 2 sub-frames per origin:
  #   phase 1: full forecast cone shown, no actual yet for this origin
  #   phase 2: same cone + actual price drawn over it for this origin
  # Both phases include ALL prior origins' cones + actuals (accumulating)
  global_frame <- 0L
  accum_bands   <- data.frame()   # running accumulator between loop iterations
  accum_actuals <- data.frame()   # running accumulator between loop iterations

  cat(sprintf("[marketForecast] Rolling origins: %d origins (2 phases each = %d frames)...\n",
              length(origin_idx), length(origin_idx) * 2))

  for (fi in seq_along(origin_idx)) {
    oi <- origin_idx[fi]

    sub_ret   <- df$log_ret[1:oi]
    sub_price <- df$close[1:oi]
    sub_dates <- df$date[1:oi]
    sub_last  <- tail(sub_price, 1)
    sub_ldate <- tail(sub_dates, 1)

    # refit model on this subset
    sub_fit <- tryCatch(
      ugarchfit(spec = spec, data = sub_ret, solver = "hybrid"),
      error = function(e) tryCatch(
        ugarchfit(spec = spec, data = sub_ret, solver = "nlminb"),
        error = function(e2) NULL
      )
    )

    if (is.null(sub_fit)) next

    # skip windows with unstable fits (NaN sigma)
    if (any(is.nan(sigma(sub_fit)))) next

    sub_sim <- tryCatch(
      ugarchsim(sub_fit, n.sim = horizon_bars, n.start = 0,
                m.sim = mc_sims, startMethod = "sample"),
      error = function(e) NULL
    )

    if (is.null(sub_sim)) next

    sub_rets_mat <- fitted(sub_sim)

    # skip if simulation produced all-NaN paths (split/outlier contamination)
    if (all(is.nan(sub_rets_mat))) next
    sub_prices_m <- matrix(NA, nrow = horizon_bars + 1, ncol = mc_sims)
    sub_prices_m[1, ] <- sub_last

    for (s in 1:mc_sims) {
      for (t in 2:(horizon_bars + 1)) {
        sub_prices_m[t, s] <- sub_prices_m[t-1, s] * exp(sub_rets_mat[t-1, s])
      }
    }

    sub_future_dates <- seq(sub_ldate + bar_seconds,
                            by = bar_seconds,
                            length.out = horizon_bars)

    sub_pct <- apply(sub_prices_m[-1, ], 1, function(x)
      quantile(x, probs = c(0.10, 0.25, 0.50, 0.75, 0.90), na.rm = TRUE))

    # forecast cone for this origin
    this_bands <- data.frame(
      date   = sub_future_dates,
      p10    = sub_pct[1, ],
      p25    = sub_pct[2, ],
      p50    = sub_pct[3, ],
      p75    = sub_pct[4, ],
      p90    = sub_pct[5, ],
      origin = as.character(as.Date(sub_ldate)),
      fi     = fi
    )

    # actual prices that fell in the forecast window (may be empty for recent origins)
    actual_mask <- df$date > sub_ldate & df$date <= max(sub_future_dates)
    if (sum(actual_mask) > 0) {
      this_actual <- data.frame(
        date   = df$date[actual_mask],
        price  = df$close[actual_mask],
        origin = as.character(as.Date(sub_ldate)),
        fi     = fi
      )
    } else {
      this_actual <- data.frame(date = as.POSIXct(character(0)),
                                price = numeric(0),
                                origin = character(0),
                                fi = integer(0))
    }

    # ── phase 1: new cone + all prior cones/actuals; no actual for this origin yet ─
    global_frame   <- global_frame + 1L
    # accum_bands holds all prior origins' bands (no frame col yet); tag them
    all_prior_bands <- if (nrow(accum_bands) > 0)
                         accum_bands |> mutate(frame = global_frame)
                       else data.frame()
    frame1_bands   <- rbind(all_prior_bands, this_bands |> mutate(frame = global_frame))
    frame1_hist    <- data.frame(date = sub_dates, price = sub_price,
                                 origin = as.character(as.Date(sub_ldate)),
                                 fi = fi, frame = global_frame)
    frame1_actuals <- if (nrow(accum_actuals) > 0)
                        accum_actuals |> mutate(frame = global_frame)
                      else data.frame()

    rolling_bands   <- rbind(rolling_bands,   frame1_bands)
    rolling_hist    <- rbind(rolling_hist,    frame1_hist)
    rolling_actuals <- rbind(rolling_actuals, frame1_actuals)

    # ── phase 2: same cone set + actual for THIS origin revealed ─────────────────
    global_frame    <- global_frame + 1L
    frame2_bands    <- frame1_bands  |> mutate(frame = global_frame)
    frame2_hist     <- frame1_hist   |> mutate(frame = global_frame)
    new_accum_acts  <- rbind(if (nrow(accum_actuals) > 0) accum_actuals else data.frame(),
                             this_actual)
    frame2_actuals  <- new_accum_acts |> mutate(frame = global_frame)

    rolling_bands   <- rbind(rolling_bands,   frame2_bands)
    rolling_hist    <- rbind(rolling_hist,    frame2_hist)
    rolling_actuals <- rbind(rolling_actuals, frame2_actuals)

    # grow accumulators: add this origin's bands/actuals (no frame col to strip)
    accum_bands   <- rbind(accum_bands, this_bands)
    accum_actuals <- if (nrow(new_accum_acts) > 0)
                       new_accum_acts |> select(-any_of("frame"))
                     else data.frame()
  }

  if (nrow(rolling_bands) > 0) {

    all_y <- c(price_vec, rolling_bands$p10, rolling_bands$p90)
  y_lo  <- min(all_y, na.rm = TRUE) * 0.97
  y_hi  <- max(all_y, na.rm = TRUE) * 1.03
  x_lo  <- min(dates_vec)
  x_hi  <- max(rolling_bands$date, na.rm = TRUE)

  # frame -> origin label map (take one row per frame - origin is same within a phase pair)
  frame_labels <- rolling_hist |>
    group_by(frame) |>
    slice(1) |>
    ungroup() |>
    select(frame, origin) |>
    deframe()  # named vector: frame -> origin string

  p_anim2 <- ggplot() +

      # historical price line (grows as origin advances)
      geom_line(data = rolling_hist,
                aes(x = date, y = price, group = frame),
                color = CYAN, linewidth = 0.7) +

      # 80% forecast band (all prior origins stay visible)
      geom_ribbon(data = rolling_bands,
                  aes(x = date, ymin = p10, ymax = p90, group = interaction(frame, origin)),
                  fill = WHITE, alpha = 0.06) +

      # IQR forecast band
      geom_ribbon(data = rolling_bands,
                  aes(x = date, ymin = p25, ymax = p75, group = interaction(frame, origin)),
                  fill = CYAN, alpha = 0.12) +

      # median forecast line
      geom_line(data = rolling_bands,
                aes(x = date, y = p50, group = interaction(frame, origin)),
                color = ORANGE, linewidth = 0.7, linetype = "dashed", alpha = 0.7) +

      # actual price over forecast window - GREEN line, appears in phase 2
      { if (nrow(rolling_actuals) > 0)
          geom_line(data = rolling_actuals,
                    aes(x = date, y = price, group = interaction(frame, origin)),
                    color = GREEN, linewidth = 1.0, alpha = 0.9)
      } +

      labs(
        title    = sprintf("%s - Rolling Forecast vs Actual", display_sym),
        subtitle = "Origin: {frame_labels[as.character(current_frame)]} | Orange=forecast median | Green=actual | Bands=50/80%% CI",
        x        = NULL,
        y        = "Price",
        caption  = "GJR-GARCH(1,1)-t rolling refit | All prior forecasts remain visible | Not financial advice | JHCV"
      ) +

      scale_y_continuous(labels = comma) +

      coord_cartesian(xlim = c(x_lo, x_hi), ylim = c(y_lo, y_hi)) +

      myTheme +

      transition_manual(frame) +
      ease_aes("linear")

    cat("[marketForecast] Rendering animation 2...\n")
    gif2_path <- sub("\\.gif$", "_rolling.gif", out_gif_path)
    n_total_frames <- max(rolling_bands$frame, na.rm = TRUE)
    animate(p_anim2,
            nframes  = n_total_frames,
            fps      = 4,
            width    = 800,
            height   = 450,
            renderer = gifski_renderer(gif2_path),
            res      = 96)
    cat(sprintf("[marketForecast] Animation 2 saved: %s\n", gif2_path))

  } else {
    cat("[marketForecast] WARN: rolling animation skipped (no valid fits)\n")
    gif2_path <- gif1_path
  }

  # ── returns distribution panel ────────────────────────────────────────────────
  cat("[marketForecast] Building returns distribution plot...\n")

  ret_seq <- seq(quantile(returns_vec, 0.001), quantile(returns_vec, 0.999), length.out = 200)

  if (!is.null(t_fit)) {
    t_density <- dt((ret_seq - t_fit$estimate["m"]) / t_fit$estimate["s"],
                    df = t_fit$estimate["df"]) / t_fit$estimate["s"]
    normal_density <- dnorm(ret_seq, mean = mean(returns_vec), sd = sd(returns_vec))

    dist_overlay <- data.frame(
      x         = rep(ret_seq, 2),
      y         = c(t_density, normal_density),
      dist_type = c(rep("Student-t fit", 200), rep("Normal (reference)", 200))
    )
  }

  # key stats for annotation
  ann_text <- sprintf(
    "mu=%.4f  sigma=%.4f\nskew=%.3f  kurt=%.3f\nADF p<0.01",
    mean(returns_vec), sd(returns_vec),
    mean(((returns_vec - mean(returns_vec)) / sd(returns_vec))^3),
    mean(((returns_vec - mean(returns_vec)) / sd(returns_vec))^4) - 3
  )

  p_dist <- ggplot(ret_hist_df, aes(x = log_ret)) +

    geom_histogram(aes(y = after_stat(density)),
                   bins = 60,
                   fill = CYAN, alpha = 0.5, color = NA) +

    { if (!is.null(t_fit))
        geom_line(data = dist_overlay,
                  aes(x = x, y = y, color = dist_type, linetype = dist_type),
                  linewidth = 0.8)
    } +

    { if (!is.null(t_fit))
        scale_color_manual(values = c("Student-t fit" = ORANGE,
                                      "Normal (reference)" = RED),
                           name = NULL)
    } +

    { if (!is.null(t_fit))
        scale_linetype_manual(values = c("Student-t fit" = "solid",
                                         "Normal (reference)" = "dashed"),
                              name = NULL)
    } +

    annotate("text", x = Inf, y = Inf, hjust = 1.1, vjust = 1.5,
             label = ann_text, color = WHITE, size = 2.8,
             family = "mono") +

    labs(
      title    = sprintf("%s - Log Return Distribution", display_sym),
      subtitle = sprintf("n=%d observations | Fat tails expected", length(returns_vec)),
      x        = "Log Return",
      y        = "Density",
      caption  = "Orange = student-t fit | Red = normal reference | JHCV"
    ) +

    myTheme +

    theme(legend.position = "bottom")

  dist_png_path <- sub("\\.gif$", "_dist.png", out_gif_path)
  ggsave(dist_png_path, p_dist,
         width = 8, height = 4, dpi = 120,
         bg = BG)
  cat(sprintf("[marketForecast] Distribution plot saved: %s\n", dist_png_path))

  # ── output paths JSON ─────────────────────────────────────────────────────────
  cat(sprintf("OUTPUT_JSON:%s\n",
    jsonlite::toJSON(list(
      gif1    = gif1_path,
      gif2    = gif2_path,
      distpng = dist_png_path,
      p50_end = round(tail(bands$p50, 1), 4),
      p10_end = round(tail(bands$p10, 1), 4),
      p90_end = round(tail(bands$p90, 1), 4),
      p25_end = round(tail(bands$p25, 1), 4),
      p75_end = round(tail(bands$p75, 1), 4),
      last_price = round(last_price, 4),
      mu_ret  = round(mean(returns_vec), 6),
      sig_ret = round(sd(returns_vec), 6),
      kurt    = round(mean(((returns_vec - mean(returns_vec)) / sd(returns_vec))^4), 4)
    ), auto_unbox = TRUE)))

  } # end else (GARCH branch)

# ── ECONOMIC (FRED / SARIMA) ──────────────────────────────────────────────────

} else if (category == "economic") {

  df$date <- as.Date(df$date)
  df <- df |> arrange(date) |> filter(!is.na(value))

  # detect frequency from date gaps
  date_diffs <- as.numeric(diff(df$date))
  med_diff   <- median(date_diffs, na.rm = TRUE)

  if (med_diff <= 8) {
    freq <- 52   # weekly
  } else if (med_diff <= 35) {
    freq <- 12   # monthly
  } else {
    freq <- 4    # quarterly
  }

  start_year  <- year(df$date[1])
  start_month <- month(df$date[1])

  val_ts <- ts(df$value,
               start     = c(start_year, start_month),
               frequency = freq)

  # ── auto.arima fit ────────────────────────────────────────────────────────────
  cat("[marketForecast] Fitting auto.arima SARIMA model...\n")
  model <- auto.arima(val_ts,
                      stepwise       = FALSE,
                      approximation  = FALSE,
                      seasonal       = TRUE,
                      max.p          = 3,
                      max.q          = 3,
                      max.P          = 2,
                      max.Q          = 2,
                      ic             = "aicc",
                      lambda         = "auto")

  cat(sprintf("[marketForecast] Model selected: %s\n", as.character(model)))

  # ── rolling vintage animation (direct port from fcstanim) ────────────────────
  cat("[marketForecast] Building FRED rolling vintage animation...\n")

  min_train   <- max(24, round(nrow(df) * 0.5))
  step_size   <- max(1, round(nrow(df) / 40))
  origin_idx  <- seq(min_train, nrow(df), by = step_size)
  if (tail(origin_idx, 1) != nrow(df)) origin_idx <- c(origin_idx, nrow(df))

  vintage_fcst <- data.frame()
  vintage_hist <- data.frame()

  for (fi in seq_along(origin_idx)) {
    oi <- origin_idx[fi]

    sub_ts <- ts(df$value[1:oi],
                 start     = c(start_year, start_month),
                 frequency = freq)

    sub_model <- tryCatch(
      auto.arima(sub_ts, stepwise = TRUE, approximation = TRUE,
                 seasonal = TRUE, lambda = "auto"),
      error = function(e) NULL
    )

    if (is.null(sub_model)) next

    fcst <- tryCatch(
      forecast(sub_model, h = horizon_bars, level = c(50, 80, 95)),
      error = function(e) NULL
    )

    if (is.null(fcst)) next

    # build date sequence for forecast
    last_obs_date <- df$date[oi]
    if (freq == 12) {
      fcst_dates <- seq(last_obs_date %m+% months(1),
                        by = "months", length.out = horizon_bars)
    } else if (freq == 52) {
      fcst_dates <- seq(last_obs_date + 7,
                        by = "weeks", length.out = horizon_bars)
    } else {
      fcst_dates <- seq(last_obs_date %m+% months(3),
                        by = "quarter", length.out = horizon_bars)
    }

    tmp_f <- data.frame(
      date        = fcst_dates,
      mean        = as.numeric(fcst$mean),
      lo50        = as.numeric(fcst$lower[, 1]),
      hi50        = as.numeric(fcst$upper[, 1]),
      lo80        = as.numeric(fcst$lower[, 2]),
      hi80        = as.numeric(fcst$upper[, 2]),
      lo95        = as.numeric(fcst$lower[, 3]),
      hi95        = as.numeric(fcst$upper[, 3]),
      origin_date = as.character(last_obs_date),
      frame       = fi
    )

    tmp_h <- data.frame(
      date  = df$date[1:oi],
      value = df$value[1:oi],
      frame = fi
    )

    vintage_fcst <- rbind(vintage_fcst, tmp_f)
    vintage_hist <- rbind(vintage_hist, tmp_h)
  }

  if (nrow(vintage_fcst) == 0) {
    stop("No valid ARIMA fits produced - check data quality")
  }

  p_fred <- ggplot() +

    # actual historical series
    geom_line(data = vintage_hist,
              aes(x = date, y = value),
              color = CYAN, linewidth = 0.8) +

    # 95% CI band
    geom_ribbon(data = vintage_fcst,
                aes(x = date, ymin = lo95, ymax = hi95),
                fill = WHITE, alpha = 0.06) +

    # 80% CI band
    geom_ribbon(data = vintage_fcst,
                aes(x = date, ymin = lo80, ymax = hi80),
                fill = WHITE, alpha = 0.09) +

    # 50% CI band
    geom_ribbon(data = vintage_fcst,
                aes(x = date, ymin = lo50, ymax = hi50),
                fill = CYAN, alpha = 0.18) +

    # mean forecast line
    geom_line(data = vintage_fcst,
              aes(x = date, y = mean),
              color = ORANGE, linewidth = 1.0, linetype = "dashed") +

    labs(
      title    = sprintf("%s - SARIMA Forecast", display_sym),
      subtitle = "Forecast as of: {closest_state}",
      x        = NULL,
      y        = "Value",
      caption  = "auto.ARIMA (SARIMA) | Source: FRED / St. Louis Fed | Not financial advice | JHCV"
    ) +

    scale_y_continuous(labels = comma) +
    scale_x_date(date_labels = "%Y") +

    myTheme +

    transition_states(frame, transition_length = 1, state_length = 0) +
    ease_aes("linear")

  cat("[marketForecast] Rendering FRED animation...\n")
  gif1_path <- sub("\\.gif$", "_rolling.gif", out_gif_path)
  animate(p_fred,
          nframes  = length(origin_idx) * 2,
          fps      = 8,
          width    = 800,
          height   = 450,
          renderer = gifski_renderer(gif1_path),
          res      = 96)
  cat(sprintf("[marketForecast] FRED animation saved: %s\n", gif1_path))

  # static final forecast
  last_fcst  <- vintage_fcst |> filter(frame == max(frame))
  full_hist  <- data.frame(date = df$date, value = df$value)

  p_static <- ggplot() +

    geom_line(data = full_hist,
              aes(x = date, y = value),
              color = CYAN, linewidth = 0.8) +

    geom_ribbon(data = last_fcst,
                aes(x = date, ymin = lo95, ymax = hi95),
                fill = WHITE, alpha = 0.07) +

    geom_ribbon(data = last_fcst,
                aes(x = date, ymin = lo80, ymax = hi80),
                fill = WHITE, alpha = 0.10) +

    geom_ribbon(data = last_fcst,
                aes(x = date, ymin = lo50, ymax = hi50),
                fill = CYAN, alpha = 0.20) +

    geom_line(data = last_fcst,
              aes(x = date, y = mean),
              color = ORANGE, linewidth = 1.0, linetype = "dashed") +

    labs(
      title   = sprintf("%s - Current SARIMA Forecast", display_sym),
      subtitle = sprintf("Model: %s | %d obs", as.character(model), nrow(df)),
      x        = NULL,
      y        = "Value",
      caption  = "50/80/95% CI | Not financial advice | JHCV"
    ) +

    scale_y_continuous(labels = comma) +
    scale_x_date(date_labels = "%Y") +

    myTheme

  static_png <- sub("\\.gif$", "_static.png", out_gif_path)
  ggsave(static_png, p_static,
         width = 8, height = 4, dpi = 120, bg = BG)

  last_mean <- round(tail(last_fcst$mean, 1), 4)
  last_lo80 <- round(tail(last_fcst$lo80, 1), 4)
  last_hi80 <- round(tail(last_fcst$hi80, 1), 4)

  cat(sprintf("OUTPUT_JSON:%s\n",
    jsonlite::toJSON(list(
      gif1       = gif1_path,
      gif2       = gif1_path,
      staticpng  = static_png,
      mean_end   = last_mean,
      lo80_end   = last_lo80,
      hi80_end   = last_hi80,
      last_value = round(tail(df$value, 1), 4),
      model_str  = as.character(model)
    ), auto_unbox = TRUE)))

} else {
  stop(sprintf("Unknown category: %s", category))
}

cat("[marketForecast] Done.\n")
