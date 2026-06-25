"""
AAII Investor Sentiment Survey - Weekly since July 1987
Free XLS download — requires a session visit to referral page first (Incapsula WAF).
Columns: date, bullish, neutral, bearish (decimals: 0.38 = 38%)
Requires: pip install xlrd
"""
import requests
import pandas as pd
import io
import time

AAII_URL   = "https://www.aaii.com/files/surveys/sentiment.xls"
REFERER    = "https://www.aaii.com/sentimentsurvey/sent_results"
UA         = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def fetch_aaii_sentiment() -> pd.DataFrame:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": REFERER,
                             "Accept-Language": "en-US,en;q=0.9"})
    session.get(REFERER, timeout=15)       # warm up cookies (Incapsula)
    time.sleep(1)
    r = session.get(AAII_URL, timeout=20)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), engine='xlrd', header=3)
    df = df.rename(columns={df.columns[0]: 'date',    df.columns[1]: 'bullish',
                             df.columns[2]: 'neutral', df.columns[3]: 'bearish'})
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date', 'bullish'])
    df = df[['date', 'bullish', 'neutral', 'bearish']].copy()
    df[['bullish','neutral','bearish']] = df[['bullish','neutral','bearish']].apply(
        pd.to_numeric, errors='coerce')
    return df.reset_index(drop=True)

if __name__ == "__main__":
    df = fetch_aaii_sentiment()
    print(f"Total: {len(df)} rows, {df['date'].min().date()} -> {df['date'].max().date()}")
    print(df.tail(5).to_string(index=False))
    last = df.iloc[-1]
    print(f"\nLatest ({last['date'].date()}):  "
          f"Bull={last['bullish']:.1%}  Neut={last['neutral']:.1%}  Bear={last['bearish']:.1%}")
