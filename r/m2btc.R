# m2btc.R by JHCV

##### Required Packages #####

library(fredr)
library(dplyr)
library(ggplot2)
library(plotly)
library(scales)

#####

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
  fredr_series_observations(series) |>
    select(date, value)
}

#####

##### Pull M2 #####

m2 <- getFredData("M2REAL")

#####

##### Pull BTC & Create Monthly DF #####

btc <- getFredData("CBBTCUSD") |>
  
  drop_na()

btcm <- btc %>%
  mutate(date = floor_date(date, "month")) %>%
  group_by(date) %>%
  summarize(btc = last(value)) %>%
  ungroup()

#####

##### Combine into single DF #####

m2 <- m2 %>% 
  mutate(date = floor_date(date, "month")) %>%
  group_by(date) %>%
  summarize(m2 = last(value)) %>%
  ungroup()

m2$chg <- 0
m2$yoy <- 0

for (i in 13:nrow(m2)) {
  
  m2$chg[i] <- m2$m2[i]-m2$m2[i-12]
  m2$yoy[i] <- m2$chg[i]/m2$m2[i-12]
  
}

df <- btcm %>%
  inner_join(m2, by = "date")

#####

##### Generate Plot #####

range1 <- range(btcm$btc)
range2 <- range(m2$m2)

range1 <- range1[2]-range1[1]
range2 <- range2[2]-range2[1]

factor <- range2/range1

p <- ggplot(df, aes(x = date)) +
  geom_line(aes(y = btc), 
            color = "blue", 
            size = 1) +
  geom_line(aes(y = m2), 
            color = "red", 
            size = 1) +
  scale_y_continuous(name = "BTC Price",
    sec.axis = sec_axis(~. 
                        /factor, 
                        name = "Real M2 Money Supply")) +
  labs(title = "Bitcoin vs M2 Money Supply",
       x = "Date") +
  #scale_y_continuous(trans="log10")+
  myTheme

#####

