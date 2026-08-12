# UniFi Firmware Tracker

Weekly scraper + web UI for UniFi firmware releases from the past year.

Instead of driving a browser against https://ui.com/download/releases/firmware
(a client-rendered React page), it hits the page's JSON backend directly:
`https://download.svc.ui.com/v1/downloads?page=N` — the same paginated endpoint
the page's "Load More" button calls.

## Run

Runs as the `unifi-firmware` service in the docker monorepo's
`docker-compose.local.yml` (`dca up -d --build unifi-firmware`).

- Web UI: `http://<host>:8890` or https://unififirmware.lmerza.com
- State: SQLite at `volumes/unifi-firmware/firmware.db`

Standalone (outside the monorepo):

```bash
docker build -t unifi-firmware .
docker run -d -p 8890:8080 -v "$PWD/data:/data" unifi-firmware
```

## How it works

- One container: Flask web UI + APScheduler.
- Scrape runs on startup when the DB is empty, then every Sunday 03:30
  (`SCRAPE_CRON`, crontab syntax).
- Keeps releases published in the last `DAYS_BACK` days (default 365) in
  SQLite at `DB_PATH` (default `/data/firmware.db`).
- Web UI groups releases by device (name minus version), shows the latest
  version per device, and a dropdown to pick any version and get its
  download link / release notes. Filter by device, category
  (Firmware default / Software / All), or text search.
- `POST /api/refresh` triggers an immediate re-scrape; `GET /api/releases`
  returns everything as JSON.

## Upstream API quirk

`download.svc.ui.com` sits behind CloudFront (`max-age=60`) and sometimes
returns the same cached body for different `?page=` values. The scraper
detects duplicate pages by ID set, retries after the cache TTL expires,
merges results into the DB (never wipes it), and only prunes rows older
than the cutoff — so a flaky scrape can't destroy good data.
