# mchiCorrelation.R by JHCV
# Static full-period correlation: MCHI vs AMZN, MCHI vs MELI
# Daily log returns, full history (2011-03-31 to present)

##### Required Packages #####

library(tidyverse)
library(dplyr)
library(ggplot2)
library(scales)
library(patchwork)
library(corrplot)

#####

##### Plot Appearance Theme (navy) #####

BG    <- "#02233F"
GRID  <- "#274066"
WHITE <- "white"
CYAN  <- "#00bfff"
GREEN <- "#00e676"
RED   <- "#ff1744"
GOLD  <- "#fbbf24"

myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = BG),
                 panel.background = element_rect(fill = BG),
                 panel.grid = element_line(color = GRID),
                 axis.ticks = element_line(color = GRID),
                 axis.text = element_text(color = WHITE),
                 axis.title = element_text(color = WHITE),
                 plot.title = element_text(color = WHITE, hjust = .5),
                 plot.subtitle = element_text(color = WHITE, hjust = .5),
                 plot.caption = element_text(color = WHITE),
                 strip.background = element_rect(fill = BG),
                 strip.text = element_text(color = WHITE))

#####

##### Load Data #####

args      <- commandArgs(trailingOnly = TRUE)
lookback  <- if (length(args) >= 1) args[1] else "max"  # "1y", "5y", "max"

csv_path <- path.expand("~/discordBot/outputs/research/mchi_amzn_meli_prices.csv")
if (!file.exists(csv_path)) stop("Price CSV not found: ", csv_path)

px <- read_csv(csv_path, show_col_types = FALSE) %>%
  rename(date = Date) %>%
  mutate(date = as.Date(date)) %>%
  arrange(date)

if (lookback != "max") {
  yrs      <- as.numeric(sub("y$", "", lookback))
  cutoff   <- max(px$date) - lubridate::years(yrs)
  px       <- px %>% filter(date >= cutoff)
}

lookback_label <- switch(lookback, `1y` = "1 Year", `5y` = "5 Years", `3y` = "3 Years", "Full History")
out_suffix     <- switch(lookback, `1y` = "_1y", `5y` = "_5y", `3y` = "_3y", "")

rets <- px %>%
  mutate(
    MCHI_ret = c(NA, diff(log(MCHI))),
    AMZN_ret = c(NA, diff(log(AMZN))),
    MELI_ret = c(NA, diff(log(MELI)))
  ) %>%
  drop_na()

n_obs      <- nrow(rets)
date_start <- min(rets$date)
date_end   <- max(rets$date)

# ── Full-period static correlations ─────────────────────────────────────────
cor_mchi_amzn <- cor(rets$MCHI_ret, rets$AMZN_ret)
cor_mchi_meli <- cor(rets$MCHI_ret, rets$MELI_ret)
cor_amzn_meli <- cor(rets$AMZN_ret, rets$MELI_ret)

cat(sprintf("MCHI vs AMZN corr (daily log returns, %s to %s, n=%d): %.4f\n",
            date_start, date_end, n_obs, cor_mchi_amzn))
cat(sprintf("MCHI vs MELI corr (daily log returns, %s to %s, n=%d): %.4f\n",
            date_start, date_end, n_obs, cor_mchi_meli))
cat(sprintf("AMZN vs MELI corr (daily log returns, %s to %s, n=%d): %.4f\n",
            date_start, date_end, n_obs, cor_amzn_meli))

#####

##### Correlation Matrix Heatmap #####

cor_mat <- cor(rets %>% select(MCHI_ret, AMZN_ret, MELI_ret))
rownames(cor_mat) <- c("MCHI", "AMZN", "MELI")
colnames(cor_mat) <- c("MCHI", "AMZN", "MELI")

cor_df <- as.data.frame(cor_mat) %>%
  rownames_to_column("var1") %>%
  pivot_longer(-var1, names_to = "var2", values_to = "corr") %>%
  mutate(var1 = factor(var1, levels = c("MCHI", "AMZN", "MELI")),
         var2 = factor(var2, levels = c("MELI", "AMZN", "MCHI")))

p_heatmap <- ggplot(cor_df, aes(x = var1, y = var2, fill = corr)) +
  geom_tile(color = BG, linewidth = 2) +
  geom_text(aes(label = sprintf("%.3f", corr)), color = "white", size = 5, fontface = "bold") +
  scale_fill_gradient2(low = RED, mid = "#0a3d62", high = GREEN,
                        midpoint = 0, limits = c(-1, 1)) +
  labs(title = "Correlation Matrix - Daily Log Returns",
       subtitle = paste0(format(date_start, "%b %Y"), " - ", format(date_end, "%b %Y"),
                         " (n=", n_obs, ")"),
       x = NULL, y = NULL) +
  myTheme +
  theme(axis.text = element_text(color = WHITE, size = 11, face = "bold"))

#####

##### Scatter Plots #####

p_mchi_amzn <- ggplot(rets, aes(x = MCHI_ret, y = AMZN_ret)) +
  geom_point(color = CYAN, alpha = 0.35, size = 1.2) +
  geom_smooth(method = "lm", color = GOLD, se = TRUE, fill = GRID, linewidth = 1) +
  scale_x_continuous(labels = scales::percent) +
  scale_y_continuous(labels = scales::percent) +
  labs(title = "MCHI vs AMZN",
       subtitle = paste0("r = ", round(cor_mchi_amzn, 3)),
       x = "MCHI Daily Return", y = "AMZN Daily Return") +
  myTheme

p_mchi_meli <- ggplot(rets, aes(x = MCHI_ret, y = MELI_ret)) +
  geom_point(color = "#c084fc", alpha = 0.35, size = 1.2) +
  geom_smooth(method = "lm", color = GOLD, se = TRUE, fill = GRID, linewidth = 1) +
  scale_x_continuous(labels = scales::percent) +
  scale_y_continuous(labels = scales::percent) +
  labs(title = "MCHI vs MELI",
       subtitle = paste0("r = ", round(cor_mchi_meli, 3)),
       x = "MCHI Daily Return", y = "MELI Daily Return") +
  myTheme

#####

##### Normalized Price Levels (context) #####

px_norm <- px %>%
  mutate(MCHI_n = MCHI / MCHI[1] * 100,
         AMZN_n = AMZN / AMZN[1] * 100,
         MELI_n = MELI / MELI[1] * 100) %>%
  select(date, MCHI_n, AMZN_n, MELI_n) %>%
  pivot_longer(-date, names_to = "ticker", values_to = "level") %>%
  mutate(ticker = recode(ticker, MCHI_n = "MCHI", AMZN_n = "AMZN", MELI_n = "MELI"))

p_levels <- ggplot(px_norm, aes(x = date, y = level, color = ticker)) +
  geom_line(linewidth = 0.7) +
  scale_color_manual(values = c(MCHI = CYAN, AMZN = GOLD, MELI = "#c084fc")) +
  scale_y_log10(labels = scales::comma) +
  labs(title = "Normalized Price Levels (log scale, indexed to 100 at start)",
       x = NULL, y = "Indexed Level (log)", color = NULL) +
  myTheme +
  theme(legend.position = "top",
        legend.background = element_rect(fill = BG),
        legend.text = element_text(color = WHITE),
        legend.key = element_rect(fill = BG))

#####

##### Assemble & Save #####

top_row    <- p_heatmap
mid_row    <- p_mchi_amzn | p_mchi_meli
bottom_row <- p_levels

combined <- (top_row / mid_row / bottom_row) +
  plot_layout(heights = c(1.1, 1, 1)) +
  plot_annotation(
    title = paste0("China (MCHI) vs AMZN / MELI - Correlation Analysis (", lookback_label, ")"),
    subtitle = paste0("Daily log returns: ",
                      format(date_start, "%b %d, %Y"), " - ", format(date_end, "%b %d, %Y")),
    caption = "Source: Yahoo Finance via yfinance | JHCV",
    theme = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title    = element_text(color = WHITE, hjust = 0.5, face = "bold", size = 16),
      plot.subtitle = element_text(color = CYAN, hjust = 0.5, size = 11),
      plot.caption  = element_text(color = WHITE, hjust = 1, size = 8)
    )
  ) &
  theme(plot.background = element_rect(fill = BG, color = NA))

out_path <- path.expand(paste0("~/discordBot/outputs/research/mchi_correlation", out_suffix, ".png"))
ggsave(out_path, combined, width = 9, height = 12, dpi = 300, bg = BG)

cat(sprintf("OUTPUT_PATH:%s\n", out_path))

#####
