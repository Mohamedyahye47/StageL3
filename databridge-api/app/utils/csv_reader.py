# ============================================================
#  databridge-api/app/utils/csv_reader.py
#
#  Reads the CSV files produced by core/publisher.py and
#  converts them to a list of dicts for the API to return.
#
#  CSV format written by publisher:
#    - Semicolon-separated (;)
#    - UTF-8 encoded
#    - First row = header
# ============================================================

import csv
import os
from typing import List, Dict, Any

from app.database import DATA_DIR


def get_csv_path(source_code: str, dataset_name: str) -> str:
    """Returns the absolute path to a dataset's CSV file."""
    return os.path.join(DATA_DIR, source_code.upper(), f"{dataset_name}.csv")


def csv_exists(source_code: str, dataset_name: str) -> bool:
    """Returns True if the CSV file exists on disk."""
    return os.path.isfile(get_csv_path(source_code, dataset_name))


def read_csv(
    source_code: str,
    dataset_name: str,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Reads a CSV file and returns a slice of rows as a list of dicts.

    Args:
        source_code  : Source subfolder (e.g. 'IMF', 'WB', 'YAHOO').
        dataset_name : Dataset name, used as the filename stem.
        limit        : Maximum number of rows to return.
        offset       : Number of rows to skip from the beginning.

    Returns:
        List of dicts — one per data row (header is not included).

    Raises:
        FileNotFoundError if the CSV file does not exist.
    """
    path = get_csv_path(source_code, dataset_name)

    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"CSV not found: {path}\n"
            f"Run `python run_all.py` first to generate data files."
        )

    rows: List[Dict[str, Any]] = []

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for i, row in enumerate(reader):
            if i < offset:
                continue
            if len(rows) >= limit:
                break
            # Cast numeric-looking strings to float where possible
            rows.append(_cast_numerics(dict(row)))

    return rows


def count_csv_rows(source_code: str, dataset_name: str) -> int:
    """Returns the total number of data rows (excluding header)."""
    path = get_csv_path(source_code, dataset_name)
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8", newline="") as f:
        # subtract 1 for the header line
        return max(0, sum(1 for _ in f) - 1)


# ── Internal helper ────────────────────────────────────────

def _cast_numerics(row: Dict[str, str]) -> Dict[str, Any]:
    """
    Tries to coerce each string value to int or float.
    Leaves it as a string if coercion fails.
    """
    result: Dict[str, Any] = {}
    for key, val in row.items():
        if val == "" or val is None:
            result[key] = None
            continue
        try:
            as_int = int(val)
            result[key] = as_int
        except ValueError:
            try:
                result[key] = float(val)
            except ValueError:
                result[key] = val
    return result
