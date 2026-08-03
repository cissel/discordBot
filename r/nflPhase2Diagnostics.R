#!/usr/bin/env Rscript
# nflPhase2Diagnostics.R
# Navy-theme diagnostic plot for the NFL Phase 2 projection model.
# 5 panels: actual-vs-predicted (faceted by position), residual distribution,
# feature importance (top features per position), Spearman comparison across
# positions/models, and MAE by position.
suppressMessages({
  library(tidyverse)
  library(ggplot2)
  library(patchwork)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
OUT_PATH <- file.path(OUT_DIR, "phase2_model_diagnostics.png")

navy <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#1a3a5c", linewidth = 0.4),
  panel.grid.minor  = element_line(color = "#122840", linewidth = 0.2),
  axis.text         = element_text(color = "#a0b8cc", size = 8),
  axis.title        = element_text(color = "#cde0f0", size = 9),
  plot.title        = element_text(color = "white",   size = 13, face = "bold", hjust = 0),
  plot.subtitle     = element_text(color = "#7fa8c4", size = 9,  hjust = 0),
  plot.caption      = element_text(color = "#4a6a80", size = 7),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "#cde0f0", size = 9, face = "bold"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "#a0b8cc", size = 8),
  legend.title      = element_text(color = "#cde0f0", size = 8),
  legend.key        = element_rect(fill = "#02233F"),
  plot.margin       = margin(10, 14, 10, 12)
)
POS_COLORS <- c(QB = "#4fc3f7", RB = "#ef5350", WR = "#69f0ae", TE = "#ffa726")

positions <- c("qb", "rb", "wr", "te")
eval_all <- bind_rows(lapply(positions, function(p) {
  f <- file.path(OUT_DIR, paste0("eval_", p, ".csv"))
  if (!file.exists(f)) return(NULL)
  df <- read_csv(f, show_col_types = FALSE)
  df$position <- toupper(p)
  df
}))

imp_all <- bind_rows(lapply(positions, function(p) {
  f <- file.path(OUT_DIR, paste0("importance_", p, ".csv"))
  if (!file.exists(f)) return(NULL)
  read_csv(f, show_col_types = FALSE)
}))

exp_log <- read_csv(path.expand("~/discordBot/models/meta/nfl_experiment_log.csv"), show_col_types = FALSE)

# Panel 1: actual vs predicted, faceted by position
p1 <- ggplot(eval_all, aes(x = predicted, y = room40_pts, color = position)) +
  geom_point(alpha = 0.6, size = 1.6) +
  geom_abline(slope = 1, intercept = 0, linetype = "dashed", color = "#4a6a80") +
  geom_smooth(method = "loess", se = FALSE, linewidth = 0.6, color = "white", alpha = 0.5) +
  facet_wrap(~position, scales = "free") +
  scale_color_manual(values = POS_COLORS) +
  labs(title = "2024 Holdout: Actual vs Predicted Season Fantasy Points",
       subtitle = "Room 40 scoring | dashed = perfect prediction | white = LOESS fit",
       x = "Predicted room40_pts", y = "Actual room40_pts", color = "Position") +
  navy

# Panel 2: residual distribution
p2 <- ggplot(eval_all, aes(x = residual, fill = position)) +
  geom_density(alpha = 0.5) +
  geom_vline(xintercept = 0, linetype = "dashed", color = "#4a6a80") +
  scale_fill_manual(values = POS_COLORS) +
  labs(title = "Residual Distribution by Position",
       subtitle = "Centered near 0 = unbiased predictions",
       x = "Residual (actual - predicted)", y = "Density", fill = "Position") +
  navy

# Panel 3: top-5 feature importance per position
imp_top <- imp_all %>% group_by(position) %>% slice_max(importance, n = 5) %>% ungroup()
p3 <- ggplot(imp_top, aes(x = reorder(feature, importance), y = importance, fill = position)) +
  geom_col() +
  coord_flip() +
  facet_wrap(~position, scales = "free_y", ncol = 2) +
  scale_fill_manual(values = POS_COLORS) +
  labs(title = "Top 5 Feature Importances (GBM) by Position",
       subtitle = "Last-season usage (target share, points, snap share) dominates - matches MLB pipeline finding",
       x = NULL, y = "GBM importance", fill = "Position") +
  navy +
  theme(strip.text = element_text(size = 8))

# Panel 4: Spearman by position/model
p4 <- ggplot(exp_log, aes(x = position, y = val_spearman, fill = model_type)) +
  geom_col(position = "dodge") +
  scale_fill_manual(values = c(ridge = "#4fc3f7", gbm = "#ffa726", lgbm = "#69f0ae")) +
  labs(title = "Validation Spearman by Position and Model",
       subtitle = "2024 holdout season | primary metric: ranking accuracy, not exact points",
       x = NULL, y = "Spearman correlation", fill = "Model") +
  navy

# Panel 5: MAE by position (best model)
best_mae <- exp_log %>% group_by(position) %>% slice_max(val_spearman, n = 1) %>% ungroup()
p5 <- ggplot(best_mae, aes(x = position, y = val_mae, fill = position)) +
  geom_col() +
  scale_fill_manual(values = POS_COLORS) +
  labs(title = "MAE by Position (Best Model)",
       subtitle = "Average error in projected season fantasy points",
       x = NULL, y = "MAE (room40_pts)", fill = "Position") +
  navy

layout <- p1 / (p2 | p4) / (p3 | p5)
final <- layout +
  plot_annotation(
    caption = sprintf("NFL Moneyball Phase 2 | Ridge + GBM + LightGBM, 2019-2023 train / 2024 val | %d player-seasons evaluated",
                       nrow(eval_all))
  ) &
  theme(plot.background = element_rect(fill = "#02233F", color = NA))

ggsave(OUT_PATH, final, width = 14, height = 18, dpi = 150, bg = "#02233F")
cat("Wrote:", OUT_PATH, "\n")
