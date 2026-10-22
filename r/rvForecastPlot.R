#!/usr/bin/env Rscript
# rvForecastPlot.R - realised vol forecast model vs production GEX lookup (navy theme)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
p <- read_csv(file.path(base, "rv_forecast_predictions.csv"), show_col_types = FALSE)
res <- read_csv(file.path(base, "rv_forecast_results.csv"), show_col_types = FALSE)
pooled <- read_csv(file.path(base, "rv_forecast_pooled.csv"), show_col_types = FALSE)
best <- pooled$best_model[1]; verdict <- pooled$verdict[1]
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt))

best_lab <- paste0(best, " (best model)")
a <- p |> arrange(date) |>
  transmute(date, actual = actual_rv_fwd, `baseline lookup` = rv_baseline, best = .data[[paste0("rv_", best)]]) |>
  rename(!!best_lab := best) |>
  pivot_longer(-date, names_to = "series", values_to = "rv")
a$series <- factor(a$series, levels = c("actual", "baseline lookup", best_lab))
p1 <- ggplot(a, aes(date, rv, colour = series)) + geom_line(linewidth = 0.6, alpha = 0.95) +
  scale_colour_manual(values = setNames(c("#E0A02E", "#2E7BE0", "#2E9E4E"), c("actual", "baseline lookup", best_lab))) +
  labs(title = "Forward 21-day realised vol: actual vs baseline lookup vs model (walk-forward, yearly refits)",
       subtitle = sprintf("pooled OOS RMSE of log RV: baseline %.3f, %s %.3f  |  verdict: %s",
                          pooled$rmse[pooled$model == "baseline"], best, pooled$rmse[pooled$model == best], verdict),
       x = NULL, y = "annualised vol points", colour = NULL) + thm

b <- res |> filter(year != "pooled", model %in% c("baseline", best)) |>
  mutate(model = ifelse(model == "baseline", "baseline lookup", best_lab), year = as.integer(year))
p2 <- ggplot(b, aes(factor(year), rmse, fill = model)) + geom_col(position = position_dodge(width = 0.7), width = 0.65) +
  scale_fill_manual(values = setNames(c("#2E7BE0", "#2E9E4E"), c("baseline lookup", best_lab))) +
  labs(title = "Per-year OOS RMSE of log RV_fwd", subtitle = "model wins 5 of 9 years; 2018 and 2022 decide the pooled result",
       x = NULL, y = "RMSE", fill = NULL) + thm + theme(legend.position = "bottom")

elig_best <- p[[paste0("elig_", best)]]
cc <- p |> arrange(date) |>
  mutate(base_g = ifelse(elig_baseline, pnl, 0), best_g = ifelse(elig_best, pnl, 0)) |>
  transmute(date, `baseline-gated` = cumsum(base_g) / 21, best = cumsum(best_g) / 21) |>
  rename(!!paste0(best, "-gated") := best) |>
  pivot_longer(-date, names_to = "series", values_to = "cum")
p3 <- ggplot(cc, aes(date, cum, colour = series)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = setNames(c("#2E7BE0", "#2E9E4E"), c("baseline-gated", paste0(best, "-gated")))) +
  labs(title = "Short var swap, gated on pred. RV/VIX < train median",
       subtitle = "cumulative vol points (1 vega per month equivalent)",
       x = NULL, y = "vol points", colour = NULL) + thm

pl <- p1 / (p2 | p3) + plot_layout(heights = c(1.1, 1)) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "rv_forecast.png"), pl, width = 13, height = 10, dpi = 150, bg = bg)
cat("wrote", file.path(base, "rv_forecast.png"), "\n")
