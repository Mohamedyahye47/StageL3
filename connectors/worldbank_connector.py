import world_bank_data as wb
import pandas as pd

COUNTRY = "MR" # ISO2 for Mauritania
START_YEAR = 1990
END_YEAR = 2024

def extract(indicators_map):
    """Uses the world_bank_data library to get series one by one."""
    raw = {}
    for code in indicators_map.keys():
        try:
            series = wb.get_series(code, country=COUNTRY, simplify_index=True, date=f"{START_YEAR}:{END_YEAR}")
            raw[code] = series
        except Exception as e:
            print(f"      ⚠️ Failed to fetch {code}: {e}")
    return raw

def transform(raw_data, indicators_config):
    """Converts World Bank series dictionary into a simple list of dictionaries."""
    rows = []
    for code, series in raw_data.items():
        meta = indicators_config.get(code)
        if not meta: continue
        
        for idx, val in series.items():
            # Skip empty values
            if pd.isna(val): continue
            
            # The index might be a tuple (Country, Year) or just a Year
            year = str(idx[-1]) if isinstance(idx, tuple) else str(idx)
            
            rows.append({
                "year": int(year),
                "indicator_code": code,
                "indicator_label": meta["label"],
                "value": round(float(val), 2),
                "unit": meta["unit"]
            })
    return rows

def run(push_fn, datasets_config):
    results = []
    for ds_name, config in datasets_config.items():
        print(f"  ▶ Processing: {ds_name}")
        
        df = extract(config["indicators"])
        if df is None: continue
            
        rows = transform(df, config["indicators"])
        
        row_count = push_fn(config["ods_dataset_id"], ds_name, rows)
        results.append({"dataset": ds_name, "status": "success", "rows": row_count})
    return results