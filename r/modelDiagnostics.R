# modelDiagnostics.R
# Fantasy model diagnostics - navy theme
# Usage: Rscript modelDiagnostics.R [output_path]
# Default output: outputs/sports/mlb/fantasy/model_diagnostics.png

library(tidyverse)
library(ggplot2)
library(patchwork)

args     <- commandArgs(trailingOnly = TRUE)
OUT_PATH <- if (length(args) >= 1) args[1] else
            path.expand("~/discordBot/outputs/sports/mlb/fantasy/model_diagnostics.png")
FEAT_DIR <- path.expand("~/discordBot/outputs/features/sports")

# ── theme ─────────────────────────────────────────────────────────────────────
navy <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#1a3a5c", linewidth = 0.4),
  panel.grid.minor  = element_line(color = "#122840", linewidth = 0.2),
  axis.text         = element_text(color = "#a0b8cc", size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = "white",   size = 11, face = "bold", hjust = 0),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 8,  hjust = 0),
  plot.caption      = element_text(color = "#4a6a80", size = 7),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 8, face = "bold"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "#a0b8cc", size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = "#02233F"),
  plot.margin       = margin(8, 10, 8, 10)
)

ACCENT  <- "#4fc3f7"
GREEN   <- "#69f0ae"
RED     <- "#ef5350"
ORANGE  <- "#ffa726"
YELLOW  <- "#fff176"

# ── load data ─────────────────────────────────────────────────────────────────
# Focus on the 4 best models (GBM weekly, Ridge daily for each player type)
MODELS <- list(
  list(pt = "batters",  hz = "weekly", mt = "gbm",   label = "Batters - Weekly (GBM)"),
  list(pt = "batters",  hz = "daily",  mt = "ridge",  label = "Batters - Daily (Ridge)"),
  list(pt = "pitchers", hz = "weekly", mt = "gbm",   label = "Pitchers - Weekly (GBM)"),
  list(pt = "pitchers", hz = "daily",  mt = "ridge",  label = "Pitchers - Daily (Ridge)")
)

load_eval <- function(pt, hz, mt) {
  path <- file.path(FEAT_DIR, paste0("eval_", pt, "_", hz, "_", mt, ".csv"))
  if (!file.exists(path)) return(NULL)
  read_csv(path, show_col_types = FALSE) %>%
    mutate(model_label = paste(tools::toTitleCase(pt), "-",
                               tools::toTitleCase(hz), paste0("(", toupper(mt), ")")))
}

eval_all <- map_dfr(MODELS, ~load_eval(.x$pt, .x$hz, .x$mt))
log_df   <- read_csv(file.path(FEAT_DIR, "experiment_log_copy.csv"), show_col_types = FALSE)

# ── panel 1: Actual vs Predicted scatter (faceted by model) ──────────────────
# Sample max 400 pts per model to keep plot readable
set.seed(42)
scatter_df <- eval_all %>%
  group_by(model_label) %>%
  slice_sample(n = 400, replace = FALSE) %>%
  ungroup()

# Shared axis limits per model
axis_lims <- scatter_df %>%
  group_by(model_label) %>%
  summarise(lo = min(actual, predicted), hi = max(actual, predicted))

p1 <- ggplot(scatter_df, aes(x = predicted, y = actual)) +
  geom_abline(slope = 1, intercept = 0, color = YELLOW, linewidth = 0.6, linetype = "dashed") +
  geom_point(alpha = 0.35, size = 0.9, color = ACCENT) +
  geom_smooth(method = "loess", se = FALSE, color = GREEN, linewidth = 0.8, span = 0.6) +
  facet_wrap(~model_label, scales = "free", ncol = 2) +
  labs(
    title    = "Actual vs Predicted Fantasy Points",
    subtitle = "Dashed = perfect prediction  |  Blue line = LOESS fit  |  Val set (last 14 days)",
    x = "Predicted", y = "Actual"
  ) +
  navy

# ── panel 2: Residual distribution (density + rug) ───────────────────────────
p2 <- ggplot(eval_all, aes(x = residual, fill = model_label, color = model_label)) +
  geom_density(alpha = 0.25, linewidth = 0.7) +
  geom_vline(xintercept = 0, color = YELLOW, linewidth = 0.7, linetype = "dashed") +
  scale_fill_manual(values  = c(ACCENT, GREEN, ORANGE, RED)) +
  scale_color_manual(values = c(ACCENT, GREEN, ORANGE, RED)) +
  labs(
    title    = "Residual Distribution",
    subtitle = "Centered near 0 = unbiased  |  Narrow = more precise",
    x        = "Residual (Actual - Predicted)",
    y        = "Density",
    fill     = NULL, color = NULL
  ) +
  navy +
  theme(legend.position = "bottom",
        legend.text = element_text(size = 7))

# ── panel 3: Residuals vs Predicted (heteroskedasticity check) ───────────────
p3 <- ggplot(scatter_df, aes(x = predicted, y = residual)) +
  geom_hline(yintercept = 0, color = YELLOW, linewidth = 0.6, linetype = "dashed") +
  geom_point(alpha = 0.25, size = 0.7, color = ACCENT) +
  geom_smooth(method = "loess", se = TRUE, color = GREEN, fill = "#1a3a5c",
              linewidth = 0.8, span = 0.7) +
  facet_wrap(~model_label, scales = "free_x", ncol = 2) +
  labs(
    title    = "Residuals vs Predicted",
    subtitle = "Flat green line = well-calibrated  |  Funnel shape = heteroskedasticity",
    x = "Predicted", y = "Residual"
  ) +
  navy

# ── panel 4: Spearman / RMSE progression across runs (experiment log) ─────────
# Best model per combo per run (keep ridge for daily, gbm for weekly)
best_models <- log_df %>%
  mutate(run_n = row_number()) %>%
  group_by(player_type, horizon, model_type) %>%
  mutate(run_idx = row_number()) %>%
  ungroup() %>%
  filter(
    (horizon == "weekly" & model_type == "gbm") |
    (horizon == "daily"  & model_type == "ridge")
  ) %>%
  mutate(
    label = paste0(tools::toTitleCase(player_type), " - ",
                   tools::toTitleCase(horizon)),
    run_label = paste0("Run ", run_idx)
  )

p4a <- ggplot(best_models, aes(x = run_idx, y = val_spearman,
                                color = label, group = label)) +
  geom_line(linewidth = 1.0) +
  geom_point(size = 2.5) +
  scale_color_manual(values = c(ACCENT, GREEN, ORANGE, RED)) +
  scale_x_continuous(breaks = scales::pretty_breaks()) +
  labs(
    title    = "Spearman (Ranking Accuracy) by Run",
    subtitle = "Higher = better player ranking  |  This is the number to chase",
    x = "Training Run", y = "Val Spearman", color = NULL
  ) +
  navy +
  theme(legend.position = "bottom",
        legend.text = element_text(size = 7))

p4b <- ggplot(best_models, aes(x = run_idx, y = val_rmse,
                                color = label, group = label)) +
  geom_line(linewidth = 1.0) +
  geom_point(size = 2.5) +
  scale_color_manual(values = c(ACCENT, GREEN, ORANGE, RED)) +
  scale_x_continuous(breaks = scales::pretty_breaks()) +
  labs(
    title    = "Validation RMSE by Run",
    subtitle = "Lower = more accurate point estimates",
    x = "Training Run", y = "Val RMSE", color = NULL
  ) +
  navy +
  theme(legend.position = "bottom",
        legend.text = element_text(size = 7))

p4 <- p4a + p4b

# ── panel 5: MAE by position (batters weekly GBM) ────────────────────────────
pos_err <- eval_all %>%
  filter(grepl("Weekly.*GBM|GBM.*Weekly", model_label),
         grepl("Batter", model_label),
         !is.na(fantasy_position)) %>%
  group_by(fantasy_position) %>%
  summarise(
    mae  = mean(abs_error, na.rm = TRUE),
    rmse = sqrt(mean(residual^2, na.rm = TRUE)),
    n    = n(),
    .groups = "drop"
  ) %>%
  arrange(desc(mae))

p5 <- ggplot(pos_err, aes(x = reorder(fantasy_position, mae), y = mae)) +
  geom_col(fill = ACCENT, alpha = 0.85, width = 0.6) +
  geom_text(aes(label = paste0("n=", n)), hjust = -0.2,
            color = "#a0b8cc", size = 3) +
  coord_flip() +
  labs(
    title    = "MAE by Position (Batters Weekly GBM)",
    subtitle = "Which positions are hardest to predict?",
    x = NULL, y = "Mean Absolute Error"
  ) +
  navy

# ── assemble ──────────────────────────────────────────────────────────────────
final <- (p1 / p2 / p3 / p4 / p5) +
  plot_annotation(
    title   = "Fantasy Baseball Model Diagnostics",
    caption = paste0("Val set: last 14 days of 2026 season  |  Train: 3 seasons  |  ",
                     format(Sys.Date(), "%B %d, %Y")),
    theme = theme(
      plot.background = element_rect(fill = "#011828", color = NA),
      plot.title   = element_text(color = "white", size = 14, face = "bold",
                                  hjust = 0.5, margin = margin(b = 6)),
      plot.caption = element_text(color = "#4a6a80", size = 7, hjust = 0.5)
    )
  )

ggsave(OUT_PATH, plot = final, width = 12, height = 28, dpi = 150, bg = "#011828")
message("Saved: ", OUT_PATH)
