import requests
import pandas as pd

COUNTRY = "MRT"
START_YEAR = 1990
END_YEAR = 2024
BASE_URL = "https://www.imf.org/external/datamapper/api/v1"

def extract(indicators_map):
    """Fetches raw data from IMF API indicator by indicator."""
    raw = {}
    periods = ",".join(str(y) for y in range(START_YEAR, END_YEAR + 1))
    
    for code in indicators_map.keys():
        url = f"{BASE_URL}/{code}/{COUNTRY}"
        try:
            response = requests.get(url, params={"periods": periods}, timeout=20)
            response.raise_for_status()
            
            data = response.json()
            # Navigate the nested JSON response
            year_data = data.get("values", {}).get(code, {}).get(COUNTRY, {})
            if year_data:
                raw[code] = year_data
        except Exception as e:
            print(f"      ⚠️ IMF API Error for {code}: {e}")
            
    return raw

def transform_long(raw_data, indicators_config):
    """Simplifies raw IMF data into a clean list of rows."""
    rows = []
    for code, year_data in raw_data.items():
        meta = indicators_config.get(code)
        if not meta: continue
        
        for year, val in year_data.items():
            if int(year) < START_YEAR or int(year) > END_YEAR:
                continue
            
            rows.append({
                "year": int(year),
                "indicator_code": code,
                "indicator_label": meta["label"], 
                "value": round(float(val), 2),
                "unit": meta["unit"]              
            })
    return rows

def run(push_fn, datasets_config):
    """Main entry point called by run_all.py"""
    results = []
    for ds_name, config in datasets_config.items():
        print(f"  ▶ Processing: {ds_name}")
        
        raw = extract(config["indicators"])
        if not raw:
            continue
            
        rows = transform_long(raw, config["indicators"])
        
        row_count = push_fn(config["ods_dataset_id"], ds_name, rows)
        results.append({"dataset": ds_name, "status": "success", "rows": row_count})
    
    return results