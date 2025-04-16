library(fredr)
library(dplyr)
library(ggplot2)
library(scales)

##### Plot Appearance Theme #####

myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = "#02233F"),
                 panel.background = element_rect(fill = "#02233F"),
                 panel.grid = element_line(color = "#274066"),
                 axis.ticks = element_line(color = "#274066"),
                 axis.text = element_text(color = "white"),
                 axis.title = element_text(color = "white"),
                 plot.title = element_text(color = "white",
                                           hjust = .5),
                 plot.subtitle = element_text(color = "white",
                                              hjust = .5))

#####

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))

#####

##### Federal Reserve API Authentication #####

fredr_set_key("d47e2b30bf4826314df23a57408a56a6")

#####

##### Functions #####

getFredData <- function(series = "UNRATE") {
  
  data <- fredr_series_observations(series) |>
    
    select(date,
           value)
  
  return(data)
  
}

#####

# ---- Short-term treasuries ----
stir <- data.frame(
  mte = c(1, 3, 6),
  name = c("DGS1MO", "DGS3MO", "DGS6MO")
)

stdf <- data.frame()

for (i in 1:nrow(stir)) {
  data <- getFredData(stir$name[i]) |>
    mutate(value = value / 100)
  data$mte <- stir$mte[i]
  stdf <- rbind(stdf, data)
}

# ---- Long-term treasuries ----
ltir <- data.frame(yte = c(1, 2, 3, 5, 7, 10, 20, 30))
ltir$name <- paste0("DGS", ltir$yte)

ltdf <- data.frame()

for (i in 1:nrow(ltir)) {
  hd <- getFredData(ltir$name[i]) |>
    mutate(value = value / 100)
  hd$yte <- ltir$yte[i]
  ltdf <- rbind(ltdf, hd)
}

# ---- Combine both ----
stl <- stdf |>
  group_by(mte) |>
  summarize(last = value[which.max(date)], asOf = max(date)) |>
  select(mte, last, asOf)

ltl <- ltdf |>
  group_by(yte) |>
  summarize(last = value[which.max(date)], asOf = max(date)) |>
  mutate(mte = yte * 12) |>
  select(mte, last, asOf)

ycdf <- rbind(stl, ltl)
ycdf$yte <- ycdf$mte / 12

# ---- Plot yield curve ----
ycp <- ggplot(ycdf, aes(x = yte, y = last)) +
  geom_line(color = "white") +
  labs(
    x = "Time to Maturity",
    y = "Interest Rate",
    title = paste("Market Yield on U.S. Treasury Securities at Constant Maturity:", max(ycdf$asOf))
  ) +
  scale_x_log10(
    breaks = ycdf$yte,
    labels = c("1 Month", "3 Months", "6 Months", "1 Year", "2 Years", "3 Years", "5 Years", "7 Years", "10 Years", "20 Years", "30 Years")
  ) +
  scale_y_continuous(labels = percent) +
  myTheme +
  theme(
    axis.text.x = element_text(angle = -45, size = 8),
    plot.title = element_text(size = 7)
  )

# ---- Save the plot ----
ggsave("/Users/jamescissel/discordBot/outputs/yield_curve.png", ycp, width = 8, height = 4.5, dpi = 300)
