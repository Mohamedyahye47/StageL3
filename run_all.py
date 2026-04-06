#!/usr/bin/env python3
# ============================================================
#  run_all.py
#  Richat DataBridge — Pipeline runner
#
#  Usage:
#      python run_all.py                  # run all connectors
#      python run_all.py --connector imf  # run one connector
#      python run_all.py --dry-run        # simulate, no file I/O
# ============================================================

import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from core import db
from core import publisher
from connectors import worldbank_connector as wb
from connectors import imf_connector       as imf
from connectors import yahoo_connector     as yahoo

# Map connector name → (source_code, module)
CONNECTORS = {
    "worldbank": ("WB",    wb),
    "imf":       ("IMF",   imf),
    "yahoo":     ("YAHOO", yahoo),
}


# ============================================================
#  PUSH FACTORY
# ============================================================

def make_push_fn(source_code: str, dry_run: bool = False):
    """
    Returns a push function bound to a specific source_code.

    The returned function:
      - In dry-run mode: prints what would happen, does nothing.
      - Otherwise: delegates to publisher.push() and logs the result.
    """
    def push_fn(ods_dataset_id: str, dataset_name: str, rows: list) -> int:
        if dry_run:
            print(f"    [DRY-RUN] Would write {len(rows)} rows "
                  f"→ data/{source_code}/{dataset_name}.csv")
            return len(rows)

        try:
            row_count = publisher.push(
                ods_dataset_id,
                dataset_name,
                rows,
                source_code=source_code,   # ← tells publisher which subfolder
            )
            db.log_push(dataset_name, source_code, ods_dataset_id, row_count, "success")
            return row_count

        except Exception as exc:
            db.log_push(dataset_name, source_code, ods_dataset_id, 0, "error", str(exc))
            raise exc

    return push_fn


# ============================================================
#  MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Richat DataBridge — pipeline runner")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Simulate the pipeline without writing any files")
    parser.add_argument("--connector", choices=list(CONNECTORS.keys()),
                        help="Run a single connector instead of all")
    args = parser.parse_args()

    started_at = datetime.now()
    print(
        f"\n{'='*55}\n"
        f"  Richat DataBridge — Pipeline\n"
        f"  Mode       : {'DRY-RUN' if args.dry_run else 'LIVE'}\n"
        f"  Started    : {started_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'='*55}"
    )

    # ── STEP 1: Initialise DB & seed metadata ──────────────
    print("\n[STEP 1] Initialise database & metadata")
    db.init_db()
    db.seed_metadata()

    # ── STEP 2: ETL per connector ──────────────────────────
    print("\n[STEP 2] ETL & Push\n")

    active = {
        k: v for k, v in CONNECTORS.items()
        if args.connector is None or k == args.connector
    }

    all_results = []

    for name, (source_code, module) in active.items():
        print(f"  ┌─ [{source_code}] {name}")

        datasets_config = db.get_etl_config(source_code)
        if not datasets_config:
            print(f"  │  ❌ No active datasets found for {source_code}\n  └─")
            continue

        push_fn = make_push_fn(source_code, dry_run=args.dry_run)

        try:
            results = module.run(push_fn, datasets_config)
            all_results.extend(results)
            ok = sum(1 for r in results if r.get("status") == "success")
            print(f"  └─ ✅ {ok}/{len(results)} datasets pushed successfully\n")

        except Exception as exc:
            print(f"  └─ ❌ {name} failed: {exc}\n")
            all_results.append({
                "dataset": name,
                "status":  "error",
                "rows":    0,
                "error":   str(exc),
            })

    # ── STEP 3: Summary ────────────────────────────────────
    elapsed = datetime.now() - started_at
    success  = [r for r in all_results if r.get("status") == "success"]
    errors   = [r for r in all_results if r.get("status") != "success"]
    total_rows = sum(r.get("rows", 0) for r in success)

    print(f"{'='*55}")
    print(f"  SUMMARY")
    print(f"  Datasets processed : {len(all_results)}")
    print(f"  Success            : {len(success)}")
    print(f"  Errors             : {len(errors)}")
    print(f"  Total rows written : {total_rows:,}")
    print(f"  Elapsed            : {elapsed}")
    print(f"{'='*55}\n")

    if errors:
        print("  Failed datasets:")
        for r in errors:
            print(f"    - {r['dataset']}: {r.get('error', 'unknown error')}")
        print()


if __name__ == "__main__":
    main()
