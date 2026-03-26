# ============================================================
#  core/publisher.py
#
#  Pushes transformed data to OpenDataSoft via the
#  Management API v2.
#
#  Flow per dataset:
#    1. Serialise rows → in-memory CSV  (no file written)
#    2. Delete previous CSV resource(s) on the ODS dataset
#    3. Upload the new CSV as a resource
#    4. Publish the dataset
#
#  Requires in .env:
#    ODS_DOMAIN   = richat.opendatasoft.com
#    ODS_API_KEY  = <your management API key>
# ============================================================

import io
import csv
import os
import requests
from dotenv import load_dotenv

load_dotenv()

ODS_DOMAIN  = os.getenv("ODS_DOMAIN", "richat.opendatasoft.com")
ODS_API_KEY = os.getenv("ODS_API_KEY", "")
BASE_URL    = f"https://{ODS_DOMAIN}/api/management/v2"


# ============================================================
#  INTERNAL HELPERS
# ============================================================

def _headers() -> dict:
    if not ODS_API_KEY:
        raise EnvironmentError(
            "ODS_API_KEY is not set. "
            "Add it to your .env file and restart."
        )
    return {"Authorization": f"Apikey {ODS_API_KEY}"}


def _rows_to_csv_bytes(rows: list) -> bytes:
    """
    Converts a list of dicts to a UTF-8 encoded CSV byte string.
    Never touches the filesystem — lives entirely in memory.
    """
    if not rows:
        return b""

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames = list(rows[0].keys()),
        delimiter  = ";",          # ODS default CSV separator
        lineterminator = "\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _get_csv_resource_ids(ods_dataset_id: str) -> list:
    """Returns the resource_id of every existing CSV resource on the dataset."""
    r = requests.get(
        f"{BASE_URL}/datasets/{ods_dataset_id}/resources/",
        headers = _headers(),
        timeout = 30
    )
    r.raise_for_status()
    return [
        res["resource_id"]
        for res in r.json().get("results", [])
        if res.get("type") == "csvfile"
    ]


def _delete_resource(ods_dataset_id: str, resource_id: str):
    """Deletes a single resource from an ODS dataset."""
    requests.delete(
        f"{BASE_URL}/datasets/{ods_dataset_id}/resources/{resource_id}/",
        headers = _headers(),
        timeout = 30
    )
    # Ignore 404 — already gone is fine


def _upload_csv(ods_dataset_id: str, filename: str, csv_bytes: bytes):
    """Uploads an in-memory CSV as a new csvfile resource."""
    r = requests.post(
        f"{BASE_URL}/datasets/{ods_dataset_id}/resources/",
        headers = _headers(),
        files   = {
            "file": (filename, io.BytesIO(csv_bytes), "text/csv")
        },
        data    = {
            "type"  : "csvfile",
            "title" : filename.replace(".csv", ""),
        },
        timeout = 60
    )
    r.raise_for_status()
    return r.json()


def _publish(ods_dataset_id: str):
    """Triggers a publish on the ODS dataset."""
    r = requests.post(
        f"{BASE_URL}/datasets/{ods_dataset_id}/publish/",
        headers = _headers(),
        timeout = 30
    )
    r.raise_for_status()


# ============================================================
#  PUBLIC INTERFACE
# ============================================================

def push(ods_dataset_id: str, dataset_name: str, rows: list) -> int:
    """
    Full push cycle for one dataset:
      rows → in-memory CSV → ODS Management API → publish

    Args:
        ods_dataset_id : ODS platform dataset identifier
                         (e.g. 'mauritania-human-development-indicators')
        dataset_name   : used as the uploaded filename
        rows           : list of dicts produced by the connector

    Returns:
        row_count (int) — number of rows successfully pushed

    Raises:
        requests.HTTPError  on any API failure
        EnvironmentError    if ODS_API_KEY is missing
    """

    print(f"  [PUSH] {dataset_name} → {ods_dataset_id}")

    # ── 1. Serialise to CSV in memory ─────────────────────
    csv_bytes = _rows_to_csv_bytes(rows)
    print(f"         Serialised : {len(rows)} rows ({len(csv_bytes):,} bytes)")

    # ── 2. Remove stale CSV resources ─────────────────────
    old_ids = _get_csv_resource_ids(ods_dataset_id)
    for rid in old_ids:
        _delete_resource(ods_dataset_id, rid)
    if old_ids:
        print(f"         Removed    : {len(old_ids)} old resource(s)")

    # ── 3. Upload new CSV ─────────────────────────────────
    filename = f"{dataset_name}.csv"
    _upload_csv(ods_dataset_id, filename, csv_bytes)
    print(f"         Uploaded   : {filename}")

    # ── 4. Publish ────────────────────────────────────────
    _publish(ods_dataset_id)
    print(f"         Published  ✅")

    return len(rows)
