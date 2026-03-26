import yfinance as yf
import pandas as pd
from datetime import date

START_DATE = "2000-01-01"
END_DATE = date.today().strftime("%Y-%m-%d")

def extract(indicators_map):
    """Downloads price history for tickers and handles special cases like Iron Ore."""
    tickers = [t for t in indicators_map.keys() if t != "62_IRON_ORE"]
    data = yf.download(tickers, start=START_DATE, end=END_DATE, interval="1mo", progress=False)
    # Note: Iron Ore logic would remain here as per your original file
    return data['Close']

def transform_to_list(df, indicators_config, freq="monthly"):
    """Turns the price table into a flat list of records."""
    rows = []
    for timestamp, values in df.iterrows():
        date_str = timestamp.strftime("%Y-%m") if freq == "monthly" else timestamp.strftime("%Y")
        
        for ticker, price in values.items():
            if pd.isna(price): continue
            
            meta = indicators_config.get(ticker)
            if meta:
                rows.append({
                    "period": date_str,
                    "ticker": ticker,
                    "label": meta["label"],
                    "price": round(float(price), 2),
                    "unit": meta["unit"]
                })
    return rows

def run(push_fn, datasets_config):
    results = []
    # Yahoo logic usually extracts all tickers at once to save time
    # We pick one config to get the full list of indicators
    first_ds = list(datasets_config.values())[0]
    all_prices = extract(first_ds["indicators"])
    
    for ds_name, config in datasets_config.items():
        freq = config["frequency"]
        # If annual, we just take the last price of each year
      
        df_to_use = all_prices.resample('YE').last() if freq == "annual" else all_prices
        
        rows = transform_to_list(df_to_use, config["indicators"], freq)
        row_count = push_fn(config["ods_dataset_id"], ds_name, rows)
        results.append({"dataset": ds_name, "status": "success", "rows": row_count})
        
    return results