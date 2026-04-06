# ============================================================
#  core/publisher.py
#
#  Local file storage — simulates OpenDataSoft.
#
#  Instead of pushing to the ODS Management API, this module
#  writes transformed data as CSV files on disk:
#
#      data/{SOURCE_CODE}/{dataset_name}.csv
#
#  Public interface (signature unchanged):
#      push(ods_dataset_id, dataset_name, rows, source_code="UNKNOWN")
# ============================================================

import csv
import io
import os

# Root of the project: two levels up from core/publisher.py
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")


# ============================================================
#  INTERNAL HELPERS
# ============================================================

def _rows_to_csv_string(rows: list) -> str:
    """
    Converts a list of dicts to a CSV string (semicolon-separated).
    Works entirely in memory — no intermediate file.
    """
    if not rows:
        return ""

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(rows[0].keys()),
        delimiter=";",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _resolve_path(source_code: str, dataset_name: str) -> str:
    """
    Builds the full output path and creates parent directories if needed.

    Returns the absolute path to the target CSV file.
    """
    folder = os.path.join(DATA_DIR, source_code.upper())
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{dataset_name}.csv")


# ============================================================
#  PUBLIC INTERFACE
# ============================================================

def push(ods_dataset_id: str, dataset_name: str, rows: list, source_code: str = "UNKNOWN") -> int:
    """
    Persists one dataset as a local CSV file.

    Args:
        ods_dataset_id : Original ODS identifier (kept for logging / metadata
                         compatibility — not used for storage).
        dataset_name   : Used as the CSV filename.
        rows           : List of dicts produced by a connector.
        source_code    : Source folder name (e.g. 'IMF', 'WB', 'YAHOO').
                         Defaults to 'UNKNOWN' for backward compatibility.

    Returns:
        row_count (int) — number of rows written to disk.
    """
    print(f"  [LOCAL] {dataset_name} → data/{source_code.upper()}/{dataset_name}.csv")

    if not rows:
        print(f"         ⚠️  No rows to write — skipping.")
        return 0

    # ── 1. Serialise to CSV string ─────────────────────────
    csv_content = _rows_to_csv_string(rows)
    print(f"         Serialised : {len(rows)} rows ({len(csv_content.encode('utf-8')):,} bytes)")

    # ── 2. Resolve destination path ────────────────────────
    dest_path = _resolve_path(source_code, dataset_name)

    # ── 3. Write to disk (overwrite if already exists) ─────
    with open(dest_path, "w", encoding="utf-8", newline="") as f:
        f.write(csv_content)

    print(f"         Saved      : {dest_path} ✅")

    return len(rows)
