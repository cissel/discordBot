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
- **`!surf`** → returns a 1-week surf forecast plot for jax beach, FL  
- **`!jaxradar`** → returns a radar GIF from NWS KJAX  
- **`!flradar`** → returns a radar GIF of the entire state

### sports

#### nhl
- **`cats!`** → returns shot plot from game 7 of the 2024 stanley cup finals - vamos gatos

#### nba
- **`hoops today?`** → returns nba games taking place today
- **`hoops tomorrow?`** → returns nba games taking place tomorrow 

## visuals
![bot screenshot](assets/screenshot.png)
![surf screenshot](outputs/surf_fcst.png)
![jax radar](outputs/nwsJaxRadar.gif)
![fl radar](outputs/flRadar.gif)
![cats win](outputs/sports/nhl/catsWin.png)
![nba today](assets/nbaToday.png)

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
- nhl
    - scores & schedules
- nba
    - live scores
    - shot plots?
- mlb
    - scores & schedules
- pga
    - scores
    - tournaments
- ufc, f1?

### economic data
- pull datasets from FRED API
- analyze datasets & return forecasts

### financial data
- pull stock returns
- individual stock forecasts
- compare tickers
- stat arb & spread pairs
- oracle