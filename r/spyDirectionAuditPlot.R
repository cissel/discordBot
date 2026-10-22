#!/usr/bin/env Rscript
# Navy-theme chart for the SPY next-day direction audit.
# Reads the CSVs written by python/research/structural/spy_direction_audit.py and writes
# outputs/research/structural/direction_audit.png with three panels:
#   1. reliability diagram, full_clean vs small model, 45-degree line
#   2. leave-one-group-out delta pooled log-loss (vs full_clean) with +/- 1 se bars
#   3. per-year OOS accuracy: full (registered), full_clean, small, always-UP
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr); library(patchwork)})

out <- "/home/jhcv/discordBot/outputs/research/structural"
rel <- read_csv(file.path(out, "direction_audit_reliability.csv"), show_col_types = FALSE)
abl <- read_csv(file.path(out, "direction_audit_ablation.csv"), show_col_types = FALSE)
yr  <- read_csv(file.path(out, "direction_audit_yearly.csv"), show_col_types = FALSE)

navy <- "#02233F"; grid <- "#274066"
cols <- c("#2E7BE0", "#E0A02E", "#2E9E4E", "#7B4CE0", "#E0392E")
theme_navy <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = grid),
        panel.grid.minor = element_line(colour = grid, linewidth = 0.25),
        text = element_text(colour = "white"),
        axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"),
        plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white"),
        plot.caption = element_text(colour = "white"),
        strip.text = element_text(colour = "white", face = "bold"),
        legend.text = element_text(colour = "white"),
        legend.title = element_text(colour = "white"),
        legend.position = "bottom",
        legend.background = element_rect(fill = navy, colour = navy),
        strip.background = element_rect(fill = navy, colour = navy))

# ---- panel 1: reliability, pooled 2019-2026
r1 <- rel %>% filter(period == "2019-2026", model %in% c("full_clean", "small")) %>%
  mutate(model = factor(model, levels = c("full_clean", "small"),
                        labels = c("full_clean (72 features)", "small (forward-selected)")))
base_rate <- mean(rel$obs_freq[rel$period == "2019-2026" & rel$model == "full_clean"])
p1 <- ggplot(r1, aes(x = mean_pred, y = obs_freq, colour = model)) +
  geom_abline(slope = 1, intercept = 0, colour = "white", linetype = "dashed", linewidth = 0.5) +
  geom_hline(yintercept = base_rate, colour = "#E0392E", linetype = "dotted", linewidth = 0.6) +
  geom_errorbar(aes(ymin = obs_freq - se_obs, ymax = obs_freq + se_obs), width = 0.004, alpha = 0.7) +
  geom_line(linewidth = 0.8) + geom_point(size = 2.4) +
  scale_colour_manual(values = cols[1:2], name = NULL) +
  coord_cartesian(xlim = c(0.3, 0.9), ylim = c(0.3, 0.9)) +
  labs(title = "1. Reliability, walk-forward OOS 2019-2026",
       subtitle = "10 equal-count bins, +/- 1 se\nDashed: perfect calibration. Dotted red: base rate of UP days",
       x = "Mean predicted P(UP)", y = "Observed frequency of UP") +
  theme_navy

# ---- panel 2: leave-one-group-out
a2 <- abl %>% mutate(group = factor(group, levels = group[order(delta_logloss)]),
                     fill = ifelse(load_bearing, cols[3], ifelse(delta_logloss < 0, cols[5], cols[2])))
p2 <- ggplot(a2, aes(x = group, y = delta_logloss, fill = fill)) +
  geom_col(width = 0.7) +
  geom_errorbar(aes(ymin = delta_logloss - delta_logloss_se, ymax = delta_logloss + delta_logloss_se),
                width = 0.25, colour = "white", linewidth = 0.6) +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.4) +
  geom_text(aes(label = paste0(years_removal_hurt, "/8"), y = pmax(delta_logloss + delta_logloss_se, 0) + 0.0008),
            colour = "white", size = 3) +
  scale_fill_identity() +
  coord_flip() +
  labs(title = "2. Leave-one-group-out: change in pooled OOS log-loss",
       subtitle = "Positive = removing the group hurt; label = years of 8 in which it hurt\nGreen: load-bearing (6+ of 8). Red: removal improved log-loss",
       x = NULL, y = "delta log-loss vs full_clean (+/- 1 se across years)") +
  theme_navy

# ---- panel 3: per-year accuracy
y3 <- yr %>% filter(model %in% c("full", "full_clean", "small", "always_up")) %>%
  mutate(model = factor(model, levels = c("full", "full_clean", "small", "always_up"),
                        labels = c("full (registered, 73)", "full_clean (72)", "small", "always-UP")))
p3 <- ggplot(y3, aes(x = factor(year), y = acc, colour = model, group = model)) +
  geom_hline(yintercept = 0.5, colour = "white", linetype = "dotted", linewidth = 0.4) +
  geom_line(linewidth = 0.8) + geom_point(size = 2.4) +
  scale_colour_manual(values = c(cols[4], cols[1], cols[2], cols[5]), name = NULL) +
  labs(title = "3. Per-year OOS directional accuracy",
       subtitle = "Expanding-window walk-forward, test year on the x axis. always-UP = that year's share of UP days",
       x = "Test year", y = "Accuracy (p >= 0.5 calls UP)") +
  theme_navy

p <- (p1 | p2) / p3 +
  plot_annotation(
    title = "SPY next-day direction model audit: calibration, group ablation, accuracy vs always-UP",
    caption = "L2 logistic, StandardScaler, C by inner 5-fold CV. Train before Jan 1 of test year. full_clean drops vix_move_ratio (broken MOVE source). Research only.",
    theme = theme_navy)
ggsave(file.path(out, "direction_audit.png"), p, bg = navy, width = 15, height = 10.5, dpi = 150)
cat("saved", file.path(out, "direction_audit.png"), "\n")
