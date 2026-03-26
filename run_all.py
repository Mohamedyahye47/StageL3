#!/usr/bin/env python3
# ============================================================
#  run_all.py
# ============================================================

import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from core import db
from core import publisher
from connectors import worldbank_connector as wb
from connectors import imf_connector      as imf
from connectors import yahoo_connector    as yahoo

CONNECTORS = {
    "worldbank": ("WB", wb),
    "imf":       ("IMF", imf),
    "yahoo":     ("YAHOO", yahoo),
}

def make_push_fn(source_code, dry_run=False):
    def push_fn(ods_dataset_id, dataset_name, rows):
        if dry_run:
            print(f"    [DRY-RUN] Would push {len(rows)} rows to {ods_dataset_id}")
            return len(rows)
        try:
            row_count = publisher.push(ods_dataset_id, dataset_name, rows)
            db.log_push(dataset_name, source_code, ods_dataset_id, row_count, "success")
            return row_count
        except Exception as e:
            db.log_push(dataset_name, source_code, ods_dataset_id, 0, "error", str(e))
            raise e
    return push_fn

def main():
    parser = argparse.ArgumentParser(description="Richat DataBridge — pipeline runner")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--connector", choices=list(CONNECTORS.keys()))
    args = parser.parse_args()

    started_at = datetime.now()
    print(f"\n{'='*55}\n  Richat DataBridge — Pipeline\n  Started    : {started_at.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*55}")

    # STEP 1: Metadata Seeding
    print("\n[STEP 1] Initialise database & metadata")
    db.init_db()
    db.seed_metadata()

    # STEP 2: ETL
    print("\n[STEP 2] ETL & Push")
    all_results = []
    active = {k: v for k, v in CONNECTORS.items() if args.connector is None or k == args.connector}

    for name, (source_code, module) in active.items():
        push_fn = make_push_fn(source_code, dry_run=args.dry_run)
        datasets_config = db.get_etl_config(source_code)
        
        if not datasets_config:
            print(f"  ❌ No config found for {source_code}")
            continue

        try:
            results = module.run(push_fn, datasets_config)
            all_results.extend(results)
        except Exception as e:
            print(f"  ❌ {name} failed: {e}")
            all_results.append({"dataset": name, "status": "error", "rows": 0, "error": str(e)})

    # Final Summary logic would follow...
    print(f"\nPipeline finished in {datetime.now() - started_at}")

if __name__ == "__main__":
    main()