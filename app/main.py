"""Flask web UI + weekly scheduled scrape for UniFi firmware releases."""

import json
import logging
import os
import sqlite3
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, render_template

import scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("web")

app = Flask(__name__)

PORT = int(os.environ.get("PORT", "8080"))
# default: every Sunday 03:30
SCRAPE_CRON = os.environ.get("SCRAPE_CRON", "30 3 * * sun")

_scrape_lock = threading.Lock()


def run_scrape():
    if not _scrape_lock.acquire(blocking=False):
        log.info("scrape already running, skipping")
        return
    try:
        scraper.scrape()
    except Exception:
        log.exception("scrape failed")
    finally:
        _scrape_lock.release()


def load_data():
    conn = scraper.get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM releases ORDER BY device, date_published DESC, id DESC"
    ).fetchall()
    last = conn.execute("SELECT value FROM meta WHERE key='last_scrape'").fetchone()
    conn.close()

    devices = {}
    for r in rows:
        d = devices.setdefault(
            r["device"] + "\x00" + (r["category"] or ""),
            {
                "device": r["device"],
                "category": r["category"],
                "product_lines": json.loads(r["product_lines"] or "[]"),
                "releases": [],
            },
        )
        d["releases"].append(
            {
                "version": r["version"],
                "date": r["date_published"],
                "url": r["file_url"],
                "size": r["size"],
                "notes": r["release_notes_url"],
                "name": r["name"],
            }
        )
    out = sorted(devices.values(), key=lambda d: d["device"].lower())
    return out, (last["value"] if last else None), len(rows)


@app.route("/")
def index():
    devices, last_scrape, total = load_data()
    return render_template(
        "index.html",
        devices_json=json.dumps(devices),
        last_scrape=last_scrape,
        total=total,
        days_back=scraper.DAYS_BACK,
    )


@app.route("/api/releases")
def api_releases():
    devices, last_scrape, total = load_data()
    return jsonify({"last_scrape": last_scrape, "total": total, "devices": devices})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    threading.Thread(target=run_scrape, daemon=True).start()
    return jsonify({"started": True})


@app.route("/health")
def health():
    return jsonify({"ok": True})


def start_scheduler():
    sched = BackgroundScheduler(timezone=os.environ.get("TZ", "UTC"))
    sched.add_job(run_scrape, CronTrigger.from_crontab(SCRAPE_CRON))
    sched.start()
    log.info("scheduler started, cron: %s", SCRAPE_CRON)


def initial_scrape_if_needed():
    conn = scraper.get_conn()
    count = conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    conn.close()
    if count == 0:
        log.info("empty database, running initial scrape")
        threading.Thread(target=run_scrape, daemon=True).start()


if __name__ == "__main__":
    start_scheduler()
    initial_scrape_if_needed()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
