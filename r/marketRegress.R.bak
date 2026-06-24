#!/usr/bin/env Rscript
# marketRegress.R - multivariate OLS regression on asset returns
#
# Usage:
#   Rscript marketRegress.R <csv_path> <target_col> <regressor_cols> <out_png_path> <title_str>
#
#   csv_path      : path to aligned CSV with date + target + regressors (all log-returns)
#   target_col    : column name of the dependent variable (e.g. "BTC_ret")
#   regressor_cols: comma-separated list of regressors (e.g. "SPY_ret,GOLD_ret,DXY_ret")
#   out_png_path  : output PNG path
#   title_str     : display title for the embed (e.g. "BTC ~ SPY + GOLD + DXY")
#
# Outputs a 3x2 diagnostics panel PNG and prints OUTPUT_JSON:{...} to stdout.

suppressPackageStartupMessages({
  library(tidyverse)
  library(scales)
  library(lmtest)
  library(sandwich)
  library(corrplot)
  library(broom)
  library(zoo)
})

# ── args ──────────────────────────────────────────────────────────────────────

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: marketRegress.R <csv_path> <target_col> <regressor_cols> <out_png_path> <title_str>")
}

csv_path      <- args[1]
target_col    <- args[2]
regressor_str <- args[3]
out_png       <- args[4]
title_str     <- args[5]

regressors <- trimws(strsplit(regressor_str, ",")[[1]])
cat(sprintf("[marketRegress] target=%s regressors=%s n_vars=%d\n",
            target_col, regressor_str, length(regressors)))

# ── theme ─────────────────────────────────────────────────────────────────────

BG     <- "#02233F"
GRID   <- "#274066"
CYAN   <- "#00FFFF"
WHITE  <- "#FFFFFF"
ORANGE <- "#FF8C00"
RED    <- "#FF4444"
GREEN  <- "#44FF88"
YELLOW <- "#FFD700"
LTBLUE <- "#7EC8E3"

myTheme <- theme(
  plot.background   = element_rect(fill = BG,   color = NA),
  panel.background  = element_rect(fill = BG,   color = NA),
  panel.grid.major  = element_line(color = GRID, linewidth = 0.3),
  panel.grid.minor  = element_line(color = GRID, linewidth = 0.15),
  axis.ticks        = element_line(color = GRID),
  axis.text         = element_text(color = WHITE, size = 7),
  axis.title        = element_text(color = WHITE, size = 8),
  plot.title        = element_text(color = WHITE, size = 9,  hjust = 0.5, face = "bold"),
  plot.subtitle     = element_text(color = WHITE, size = 7.5, hjust = 0.5),
  plot.caption      = element_text(color = GRID,  size = 6,  hjust = 1),
  legend.background = element_rect(fill = BG, color = NA),
  legend.text       = element_text(color = WHITE, size = 7),
  legend.title      = element_text(color = WHITE, size = 7),
  strip.background  = element_rect(fill = GRID, color = NA),
  strip.text        = element_text(color = WHITE, size = 7)
)

# ── load + validate data ──────────────────────────────────────────────────────

df <- read_csv(csv_path, show_col_types = FALSE)

# date column
date_col <- intersect(c("date", "timestamp"), names(df))[1]
if (!is.na(date_col)) {
  df[[date_col]] <- as.Date(df[[date_col]])
  df <- df |> rename(date = all_of(date_col))
}

missing_cols <- setdiff(c(target_col, regressors), names(df))
if (length(missing_cols) > 0) {
  stop(sprintf("Columns not found in CSV: %s\nAvailable: %s",
               paste(missing_cols, collapse=", "), paste(names(df), collapse=", ")))
}

# drop NAs
model_cols <- c(target_col, regressors)
df_model   <- df |> select(all_of(c("date", model_cols))) |> drop_na()

n_obs <- nrow(df_model)
cat(sprintf("[marketRegress] n_obs=%d after dropping NAs\n", n_obs))

if (n_obs < max(20, length(regressors) * 5)) {
  stop(sprintf("Too few observations (%d) for %d regressors. Need at least %d.",
               n_obs, length(regressors), max(20, length(regressors)*5)))
}

# ── fit OLS ───────────────────────────────────────────────────────────────────

formula_str <- paste(target_col, "~", paste(regressors, collapse = " + "))
cat(sprintf("[marketRegress] formula: %s\n", formula_str))

fit      <- lm(as.formula(formula_str), data = df_model)
fit_sum  <- summary(fit)
tidy_fit <- tidy(fit)

# Newey-West HAC standard errors (robust to autocorrelation + heteroskedasticity)
# lag truncation: Newey-West rule of thumb floor(4*(n/100)^(2/9))
nw_lag   <- max(1, floor(4 * (n_obs / 100)^(2/9)))
hac_vcov <- sandwich::NeweyWest(fit, lag = nw_lag, prewhite = FALSE)
nw_test  <- lmtest::coeftest(fit, vcov = hac_vcov)
nw_df    <- as.data.frame(nw_test[, , drop = FALSE])
colnames(nw_df) <- c("estimate", "std_err_nw", "t_stat_nw", "p_nw")
nw_df$term <- rownames(nw_df)

tidy_fit <- tidy_fit |>
  left_join(nw_df |> select(term, std_err_nw, t_stat_nw, p_nw), by = "term")

# ── diagnostics ───────────────────────────────────────────────────────────────

r2        <- fit_sum$r.squared
adj_r2    <- fit_sum$adj.r.squared
fstat     <- fit_sum$fstatistic
f_p       <- pf(fstat[1], fstat[2], fstat[3], lower.tail = FALSE)
aic_val   <- AIC(fit)
bic_val   <- BIC(fit)

# Breusch-Pagan test (heteroskedasticity)
bp_test   <- lmtest::bptest(fit)
bp_stat   <- bp_test$statistic[[1]]
bp_p      <- bp_test$p.value[[1]]

# Durbin-Watson (autocorrelation)
dw_test   <- lmtest::dwtest(fit)
dw_stat   <- dw_test$statistic[[1]]
dw_p      <- dw_test$p.value[[1]]

# VIF (manual - 1/(1-R^2_j) for each regressor)
vif_vals <- numeric(length(regressors))
names(vif_vals) <- regressors
if (length(regressors) > 1) {
  for (j in seq_along(regressors)) {
    vj       <- regressors[j]
    others   <- setdiff(regressors, vj)
    f_vif    <- as.formula(paste(vj, "~", paste(others, collapse = " + ")))
    fit_vif  <- lm(f_vif, data = df_model)
    r2_j     <- summary(fit_vif)$r.squared
    vif_vals[j] <- if (r2_j >= 1) Inf else 1 / (1 - r2_j)
  }
} else {
  vif_vals[1] <- NA_real_
}

residuals_df <- df_model |>
  mutate(
    fitted   = fitted(fit),
    resid    = residuals(fit),
    std_resid = resid / sd(resid),
    obs      = row_number()
  )

# ── plots ─────────────────────────────────────────────────────────────────────

# 1. Actual vs Fitted
p1 <- ggplot(residuals_df, aes(x = fitted, y = .data[[target_col]])) +
  geom_point(color = CYAN, alpha = 0.4, size = 0.8) +
  geom_abline(slope = 1, intercept = 0, color = ORANGE, linetype = "dashed", linewidth = 0.7) +
  geom_smooth(method = "lm", se = FALSE, color = GREEN, linewidth = 0.6) +
  labs(title = "Actual vs Fitted", x = "Fitted", y = "Actual") +
  myTheme

# 2. Residuals over time
p2 <- ggplot(residuals_df, aes(x = date, y = resid)) +
  geom_hline(yintercept = 0, color = ORANGE, linetype = "dashed", linewidth = 0.5) +
  geom_line(color = CYAN, alpha = 0.7, linewidth = 0.4) +
  labs(title = "Residuals Over Time", x = NULL, y = "Residual") +
  myTheme

# 3. Residual distribution (histogram + normal overlay)
res_sd   <- sd(residuals_df$resid)
res_mean <- mean(residuals_df$resid)
p3 <- ggplot(residuals_df, aes(x = resid)) +
  geom_histogram(aes(y = after_stat(density)), bins = 40,
                 fill = CYAN, alpha = 0.6, color = NA) +
  stat_function(fun = dnorm, args = list(mean = res_mean, sd = res_sd),
                color = ORANGE, linewidth = 0.7) +
  labs(title = "Residual Distribution", x = "Residual", y = "Density") +
  myTheme

# 4. Coefficient plot (NW robust t-stats with 95% CI)
coef_plot_df <- tidy_fit |>
  filter(term != "(Intercept)") |>
  mutate(
    lo95 = estimate - 1.96 * std_err_nw,
    hi95 = estimate + 1.96 * std_err_nw,
    sig  = ifelse(p_nw < 0.05, "p<0.05", "n.s."),
    label = term
  )
p4 <- ggplot(coef_plot_df, aes(x = reorder(label, estimate), y = estimate, color = sig)) +
  geom_hline(yintercept = 0, color = ORANGE, linetype = "dashed", linewidth = 0.5) +
  geom_errorbar(aes(ymin = lo95, ymax = hi95), width = 0.25, linewidth = 0.7) +
  geom_point(size = 2.5) +
  scale_color_manual(values = c("p<0.05" = GREEN, "n.s." = RED), name = NULL) +
  coord_flip() +
  labs(title = "Coefficients (NW-HAC 95% CI)", x = NULL, y = "Estimate") +
  myTheme +
  theme(legend.position = "bottom")

# 5. Correlation matrix of regressors (+ target)
cor_cols <- c(target_col, regressors)
cor_mat  <- cor(df_model[, cor_cols], use = "pairwise.complete.obs")
cor_df   <- as.data.frame(as.table(cor_mat)) |>
  rename(Var1 = Var1, Var2 = Var2, value = Freq) |>
  mutate(label = sprintf("%.2f", value))

p5 <- ggplot(cor_df, aes(x = Var1, y = Var2, fill = value)) +
  geom_tile(color = BG, linewidth = 0.5) +
  geom_text(aes(label = label), color = WHITE, size = 2.5) +
  scale_fill_gradient2(low = RED, mid = BG, high = GREEN,
                       midpoint = 0, limits = c(-1, 1), name = "r") +
  labs(title = "Correlation Matrix", x = NULL, y = NULL) +
  theme_minimal() +
  theme(
    plot.background  = element_rect(fill = BG, color = NA),
    panel.background = element_rect(fill = BG, color = NA),
    axis.text        = element_text(color = WHITE, size = 7),
    axis.text.x      = element_text(angle = 30, hjust = 1),
    plot.title       = element_text(color = WHITE, size = 9, hjust = 0.5, face = "bold"),
    legend.text      = element_text(color = WHITE, size = 6),
    legend.title     = element_text(color = WHITE, size = 6),
    panel.grid       = element_blank()
  )

# 6. VIF bar chart
vif_df <- tibble(
  var = names(vif_vals),
  vif = as.numeric(vif_vals)
) |> filter(!is.na(vif) & is.finite(vif))

if (nrow(vif_df) > 0) {
  p6 <- ggplot(vif_df, aes(x = reorder(var, vif), y = vif,
                            fill = cut(vif, breaks = c(0, 5, 10, Inf),
                                       labels = c("Low (<5)", "Moderate (5-10)", "High (>10)")))) +
    geom_col(alpha = 0.85) +
    geom_hline(yintercept = 5,  color = YELLOW, linetype = "dashed", linewidth = 0.5) +
    geom_hline(yintercept = 10, color = RED,    linetype = "dashed", linewidth = 0.5) +
    scale_fill_manual(values = c("Low (<5)" = GREEN, "Moderate (5-10)" = YELLOW, "High (>10)" = RED),
                      name = "VIF") +
    coord_flip() +
    labs(title = "Variance Inflation Factors", x = NULL, y = "VIF") +
    myTheme +
    theme(legend.position = "bottom")
} else {
  # single regressor - no VIF meaningful
  p6 <- ggplot() +
    annotate("text", x = 0.5, y = 0.5,
             label = "VIF requires\n2+ regressors",
             color = WHITE, size = 4, hjust = 0.5) +
    labs(title = "Variance Inflation Factors") +
    myTheme +
    theme(axis.text = element_blank(), axis.ticks = element_blank(),
          panel.grid = element_blank())
}

# ── assemble panel ─────────────────────────────────────────────────────────────

library(gridExtra)
library(grid)

header <- textGrob(
  label = title_str,
  gp    = gpar(col = WHITE, fontsize = 12, fontface = "bold"),
  hjust = 0.5
)

subhdr_lines <- c(
  sprintf("n=%d  |  R2=%.4f  |  adj-R2=%.4f  |  F(%.0f,%.0f)=%.2f  p=%.4f",
          n_obs, r2, adj_r2, fstat[2], fstat[3], fstat[1], f_p),
  sprintf("AIC=%.1f  BIC=%.1f  |  BP het: stat=%.2f p=%.3f  |  DW auto: stat=%.2f p=%.3f  |  NW lag=%d",
          aic_val, bic_val, bp_stat, bp_p, dw_stat, dw_p, nw_lag)
)
subhdr <- textGrob(
  label = paste(subhdr_lines, collapse = "\n"),
  gp    = gpar(col = WHITE, fontsize = 6.5),
  hjust = 0.5
)

footer <- textGrob(
  label = "Source: Alpaca Markets / FRED / Blockchain.com / ECB via Frankfurter | NW-HAC robust SEs | not financial advice | JHCV",
  gp    = gpar(col = GRID, fontsize = 5.5, fontface = "plain"),
  hjust = 1,
  x     = unit(1, "npc")
)

png(out_png, width = 1400, height = 1000, res = 120, bg = BG)
grid.arrange(
  header, subhdr,
  p1, p2,
  p3, p4,
  p5, p6,
  footer,
  nrow   = 6,
  ncol   = 2,
  layout_matrix = rbind(c(1,1), c(2,2), c(3,4), c(5,6), c(7,8), c(9,9)),
  heights = c(0.8, 0.6, 3, 3, 3, 0.4)
)
dev.off()

cat(sprintf("[marketRegress] wrote %s\n", out_png))

# ── output JSON ───────────────────────────────────────────────────────────────

coef_list <- tidy_fit |>
  select(term, estimate, std.error, statistic, p.value, std_err_nw, t_stat_nw, p_nw) |>
  rename(se_ols = std.error, t_ols = statistic, p_ols = p.value) |>
  mutate(across(where(is.numeric), ~round(.x, 6)))

vif_list <- if (length(regressors) > 1) {
  as.list(setNames(round(as.numeric(vif_vals), 3), names(vif_vals)))
} else {
  list()
}

out_json <- list(
  n_obs     = n_obs,
  r2        = round(r2, 6),
  adj_r2    = round(adj_r2, 6),
  f_stat    = round(fstat[1], 4),
  f_df1     = fstat[2],
  f_df2     = fstat[3],
  f_p       = round(f_p, 6),
  aic       = round(aic_val, 2),
  bic       = round(bic_val, 2),
  bp_stat   = round(bp_stat, 4),
  bp_p      = round(bp_p, 4),
  dw_stat   = round(dw_stat, 4),
  dw_p      = round(dw_p, 4),
  nw_lag    = nw_lag,
  vif       = vif_list,
  coef      = coef_list,
  png_path  = out_png
)

cat("OUTPUT_JSON:", jsonlite::toJSON(out_json, auto_unbox = TRUE), "\n")
