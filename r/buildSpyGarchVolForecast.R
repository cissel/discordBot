#!/usr/bin/env Rscript
# buildSpyGarchVolForecast.R
# ============================
# Walk-forward (zero-lookahead) 1-day-ahead annualized vol forecast from a
# GJR-GARCH(1,1)-t model on SPY daily log/simple returns, to replace the
# backward-looking 21-day realized vol used in spyVolTargetWFCV.py's
# vol-target overlay denominator.
#
# Uses rugarch::ugarchroll (moving window, periodic refit) -- same model
# family (GJR-GARCH-t) already used in production for r/marketForecast.R,
# so no new package risk. Moving window means each day's forecast only
# ever sees PRIOR returns (standard walk-forward backtest semantics,
# matches the WFCV discipline used everywhere else in this pipeline).
#
# Output: outputs/features/markets/spy_garch_vol_forecast.csv
#   columns: date, garch_vol_ann (annualized 1-day-ahead conditional vol
#   forecast, made using only data strictly before `date`)
#
# Usage:
#   Rscript r/buildSpyGarchVolForecast.R

suppressPackageStartupMessages({
  library(rugarch)
})

FEAT_PATH <- path.expand("~/discordBot/outputs/features/markets/spy_features.csv")
OUT_PATH  <- path.expand("~/discordBot/outputs/features/markets/spy_garch_vol_forecast.csv")

WINDOW_SIZE  <- 750   # ~3 trading years, moving estimation window
REFIT_EVERY  <- 21    # refit monthly (cadence/cost tradeoff)
ANNUAL       <- 252

df <- read.csv(FEAT_PATH, stringsAsFactors = FALSE)
df$date <- as.Date(df$date)
df <- df[order(df$date), ]
ret_df <- df[!is.na(df$SPY_ret), c("date", "SPY_ret")]
cat(sprintf("[buildSpyGarchVolForecast] %d return obs, %s -> %s\n",
            nrow(ret_df), ret_df$date[1], tail(ret_df$date, 1)))

# We want 1-step-ahead forecasts covering the WFCV OOS window (4x252 =
# 1008 days, the same pooled OOS window as spyVolTargetWFCV.py) plus a
# little slack. forecast.length counts BACKWARD from the end of the
# series, so request enough to cover the full OOS window with margin.
FORECAST_LENGTH <- 1060
stopifnot(nrow(ret_df) > WINDOW_SIZE + FORECAST_LENGTH)

ret_vec <- ret_df$SPY_ret
names(ret_vec) <- as.character(ret_df$date)

spec <- ugarchspec(
  variance.model   = list(model = "gjrGARCH", garchOrder = c(1, 1)),
  mean.model       = list(armaOrder = c(1, 0), include.mean = TRUE),
  distribution.model = "std"
)

cat(sprintf("[buildSpyGarchVolForecast] Rolling GJR-GARCH-t: window=%d refit_every=%d forecast_length=%d\n",
            WINDOW_SIZE, REFIT_EVERY, FORECAST_LENGTH))
t0 <- Sys.time()
roll <- ugarchroll(
  spec, data = ret_vec, n.ahead = 1,
  forecast.length = FORECAST_LENGTH,
  refit.every = REFIT_EVERY, refit.window = "moving", window.size = WINDOW_SIZE,
  solver = "hybrid", calculate.VaR = FALSE
)
cat(sprintf("[buildSpyGarchVolForecast] Done in %.1fs\n", as.numeric(Sys.time() - t0, units = "secs")))

fc <- as.data.frame(roll)
# ugarchroll's as.data.frame rownames are the forecast ORIGIN's date label
# (names(ret_vec) at the position being forecast FROM) -- but critically,
# Sigma here is the forecast *for the next day*, so we need to shift the
# date label forward by one trading day to attach it to the day it's
# actually predicting (the day whose realized vol we're trying to scale
# against). Confirm by construction: ugarchroll forecasts origin+1.
origin_dates <- as.Date(rownames(fc))
all_dates <- ret_df$date
origin_idx <- match(origin_dates, all_dates)
stopifnot(!any(is.na(origin_idx)))
target_idx <- origin_idx + 1
valid <- target_idx <= nrow(ret_df)
target_dates <- all_dates[target_idx[valid]]

out <- data.frame(
  date = target_dates,
  garch_vol_ann = fc$Sigma[valid] * sqrt(ANNUAL)
)
out <- out[order(out$date), ]
out <- out[!duplicated(out$date), ]

write.csv(out, OUT_PATH, row.names = FALSE)
cat(sprintf("[buildSpyGarchVolForecast] Saved %d rows -> %s\n", nrow(out), OUT_PATH))
cat(sprintf("[buildSpyGarchVolForecast] Date range: %s -> %s\n", out$date[1], tail(out$date, 1)))
cat(sprintf("[buildSpyGarchVolForecast] garch_vol_ann summary: median=%.4f mean=%.4f min=%.4f max=%.4f\n",
            median(out$garch_vol_ann), mean(out$garch_vol_ann),
            min(out$garch_vol_ann), max(out$garch_vol_ann)))
