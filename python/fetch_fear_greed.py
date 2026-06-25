"""
Crypto Fear & Greed Index - alternative.me
Free, no API key. limit=0 returns all ~3000 daily entries since Feb 2018.
Date format: 'world' gives DD-MM-YYYY strings.
Value: 0-100 (0=Extreme Fear, 100=Extreme Greed)
"""
import requests
import pandas as pd

def fetch_fear_greed(limit: int = 30) -> pd.DataFrame:
    url = f"https://api.alternative.me/fng/?limit={limit}&format=json&date_format=world"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()['data']
    df = pd.DataFrame(data)[['timestamp', 'value', 'value_classification']]
    df['date'] = pd.to_datetime(df['timestamp'], format='%d-%m-%Y')
    df['value'] = df['value'].astype(int)
    df = df.drop(columns='timestamp').sort_values('date').reset_index(drop=True)
    return df

def fetch_fear_greed_all() -> pd.DataFrame:
    return fetch_fear_greed(limit=0)

if __name__ == "__main__":
    df = fetch_fear_greed_all()
    print(f"Total: {len(df)} rows, {df['date'].min().date()} -> {df['date'].max().date()}")
    print(df.tail(5).to_string(index=False))
