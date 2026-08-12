"""Scrape https://download.svc.ui.com/v1/downloads (the JSON backend behind
https://ui.com/download/releases/firmware) into SQLite, keeping the past year."""

import json
import logging
import os
import re
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta

import requests

API_URL = "https://download.svc.ui.com/v1/downloads"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; unifi-firmware-tracker)"}
DB_PATH = os.environ.get("DB_PATH", "/data/firmware.db")
DAYS_BACK = int(os.environ.get("DAYS_BACK", "365"))

log = logging.getLogger("scraper")

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    device TEXT NOT NULL,
    version TEXT,
    category TEXT,
    product_lines TEXT,
    date_published TEXT,
    file_url TEXT,
    size INTEGER,
    release_notes_url TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def device_name(name: str, version: str) -> str:
    """'UniFi OS - Dream Machine Pro Max 5.0.12' -> 'UniFi OS - Dream Machine Pro Max'."""
    dev = name
    if version:
        dev = dev.replace(version, " ")
    dev = re.sub(r"\s+", " ", dev).strip()
    # leftover 'v' when the name had 'Firmware v2.3.1' but version is '2.3.1'
    dev = re.sub(r"[-,]\s*$|\s+v$", "", dev).strip()
    # 'UniFi firmware 6.6.22 for U7-Pro' -> the device is what follows 'for'
    dev = re.sub(r"^UniFi firmware for\s+", "", dev, flags=re.IGNORECASE)
    return dev or name


def parse_date(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def fetch_page(page: int) -> dict:
    r = requests.get(API_URL, params={"page": page}, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def scrape() -> dict:
    """The API sits behind CloudFront (max-age=60) and intermittently returns the
    same cached body for different ?page= values, so duplicate pages are retried
    after the cache TTL, and results are merged into the DB rather than replacing it."""
    cutoff = date.today() - timedelta(days=DAYS_BACK)
    rows_by_id, seen_pages = {}, {}
    page, total_pages, old_pages_in_a_row = 1, 1, 0
    while page <= total_pages:
        data = fetch_page(page)
        downloads = data.get("downloads", [])
        total_pages = data.get("pagination", {}).get("totalPages", page)
        if not downloads:
            break
        ids = frozenset(d["id"] for d in downloads)
        if ids in seen_pages and seen_pages[ids] != page:
            for attempt in range(1, 5):
                wait = 65 * attempt
                log.warning(
                    "page %s duplicates page %s (CDN cache), retrying in %ss", page, seen_pages[ids], wait
                )
                time.sleep(wait)
                data = fetch_page(page)
                downloads = data.get("downloads", [])
                ids = frozenset(d["id"] for d in downloads)
                if ids not in seen_pages or seen_pages[ids] == page:
                    break
            else:
                log.error("page %s still duplicated after retries, stopping early", page)
                break
        seen_pages[ids] = page
        page_dates = []
        for d in downloads:
            pub = parse_date(d.get("date_published"))
            if pub:
                page_dates.append(pub)
            if not pub or pub < cutoff or not d.get("enabled", True):
                continue
            version = d.get("version") or ""
            rows_by_id[d["id"]] = (
                d["id"],
                d.get("name") or "",
                device_name(d.get("name") or "", version),
                version,
                (d.get("category") or {}).get("name") or "Other",
                json.dumps(d.get("product_lines") or []),
                pub.isoformat(),
                d.get("file_url") or d.get("file_path"),
                d.get("size"),
                d.get("release_notes_url"),
            )
        log.info(
            "page %s/%s: %s downloads, %s kept total", page, total_pages, len(downloads), len(rows_by_id)
        )
        # API is sorted newest-first; stop after two consecutive pages fully older than the cutoff
        if page_dates and max(page_dates) < cutoff:
            old_pages_in_a_row += 1
            if old_pages_in_a_row >= 2:
                break
        else:
            old_pages_in_a_row = 0
        page += 1
        time.sleep(1)

    rows = list(rows_by_id.values())
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM releases WHERE date_published < ?", (cutoff.isoformat(),))
        conn.executemany(
            "INSERT OR REPLACE INTO releases "
            "(id, name, device, version, category, product_lines, "
            "date_published, file_url, size, release_notes_url) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_scrape', ?)",
            (datetime.now(UTC).isoformat(timespec="seconds"),),
        )
    conn.close()
    log.info("scrape done: %s releases stored (cutoff %s)", len(rows), cutoff)
    return {"stored": len(rows), "cutoff": cutoff.isoformat()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(scrape())
