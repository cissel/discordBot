"""
FRED Jobless Claims - No API key required
ICSA = Initial Claims (weekly, since 1967)
CCSA = Continuing Claims (weekly, since 1967)
"""
import requests
import pandas as pd
import io

def fetch_fred_series(series_id: str) -> pd.DataFrame:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=['observation_date'])
    df = df.rename(columns={'observation_date': 'date', series_id: 'value'})
    df = df[df['value'] != '.']  # drop missing (rare)
    df['value'] = pd.to_numeric(df['value'])
    return df

if __name__ == "__main__":
    icsa = fetch_fred_series("ICSA")
    ccsa = fetch_fred_series("CCSA")
    print(f"ICSA: {len(icsa)} rows, {icsa['date'].min().date()} -> {icsa['date'].max().date()}")
    print(icsa.tail(3).to_string(index=False))
    print(f"\nCCSA: {len(ccsa)} rows, {ccsa['date'].min().date()} -> {ccsa['date'].max().date()}")
    print(ccsa.tail(3).to_string(index=False))
