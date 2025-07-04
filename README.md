# Discord Bot

There are many discord bots but this one is mine. I love it dearly.

I built this so I could send a message from my phone to my machine and have it run scripts even if I'm not at the helm. But there are a few fun surprises for the server as well.

## basic functionality
- runs r scripts
- sends plots/images/GIFs/memes
- sends embed messages
- reacts to messages
- responds with text & audio

## bot commands

### basic interactions
- **`mr bot`**,**`jarvis`** → mr bot will respond if mentioned
- **`gm`** → start your day with mr bot
- **`ping`** → play a game with mr bot
- **`r2`**  → chill w r2 

### surf & weather

#### surf
- **`surf`** → returns a 1-week surf forecast plot for jax beach, FL 
- **`wavemap`** → returns wave height forecast map GIF
- **`windmap`** → returns wind speed & direction forecast map GIF
- **`buoy waves`** → returns plot of wave height from noaa ndbc buoy #41117 off the coast of st. augustine
- **`tide plot`** → returns forecast plot for tides at mayport bar pilot dock

#### weather
- **`jaxradar`** → returns a radar GIF from NWS KJAX  
- **`jaxsat`** → returns a satellite GIF of jacksonville, FL 
- **`flradar`** → returns a radar GIF of the entire state
- **`usradar`** → returns a radar GIF of continental US

#### seasonal events & emergency advisories
- **`hurricane forecast`** → returns noaa 7 day tropical weather outlook

### sports

#### nfl
- **`wen jags`** → returns next jacksonville jaguars game 

#### nhl
- **`hockey today`** → returns nhl games today
- **`hockey tomorrow`** → returns nhl games tomorrow
- **`wen cats`** → returns next florida panthers game

#### nba
- **`hoops today?`** → returns nba games taking place today
- **`hoops tomorrow?`** → returns nba games taking place tomorrow 

## economics & finance
- **`fed rate`** → returns plot of federal funds target rate
- **`yield curve`** → returns latest yield curve plot  
- **`yield spread`** → returns full yield spread history
- **`yield spread short`** → returns recent yield spread history

## aerospace
- **`next launch`** → returns next launch from kennedy space center in cape canaveral, FL

## visuals
![bot screenshot](assets/screenshot.png)
![surf screenshot](outputs/weather/surf_fcst.png)
![wave map](outputs/weather/wave_animation.gif)
![jax radar](outputs/weather/nwsJaxRadar.gif)
![fl radar](outputs/weather/flRadar.gif)
![us radar](outputs/weather/usRadar.gif)
![hurricane forecast](outputs/weather/two7d.png)
![nba today](assets/nbaToday.png)
![fed rate](outputs/markets/dfedtaru.png)
![yield curve](outputs/markets/yield_curve.png)
![yield spread](outputs/markets/yield_spread.png)
![yield spread short](outputs/markets/yield_spread_2mo.png)

## up next

### sports
- all leages
    - scores/schedules/standings 
    - team stats
    - player stats
    - compare teams
    - compare players
    - visualizations
- nfl 
    - run radars & receiving leaders (already written, just need to migrate to this ecosystem)
    - fantasy football integration with sleeper!

- nba
    - shot plots?
- mlb
    - scores & schedules
- pga
    - scores
    - tournaments
- ufc, f1?

### economic data
- analyze datasets & return forecasts

### financial data
- pull stock returns
- individual stock forecasts
- compare tickers
- stat arb & spread pairs
- oracle