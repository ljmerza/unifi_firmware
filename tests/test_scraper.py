from datetime import date, timedelta

import pytest
import scraper


@pytest.mark.parametrize(
    ("name", "version", "expected"),
    [
        ("UniFi OS - Dream Machine Pro Max 5.0.12", "5.0.12", "UniFi OS - Dream Machine Pro Max"),
        ("UniFi OS Server 5.1.21 for Linux (x64)", "5.1.21", "UniFi OS Server for Linux (x64)"),
        (
            "UniFi Network Application 10.5.67 for UniFi OS Native",
            "10.5.67",
            "UniFi Network Application for UniFi OS Native",
        ),
        ("Some Release", "", "Some Release"),
    ],
)
def test_device_name(name, version, expected):
    assert scraper.device_name(name, version) == expected


def test_parse_date():
    assert scraper.parse_date("2026-07-28") == date(2026, 7, 28)
    assert scraper.parse_date("2026-07-28T00:00:00Z") == date(2026, 7, 28)
    assert scraper.parse_date("garbage") is None
    assert scraper.parse_date(None) is None


def make_item(id_, name, version, pub, enabled=True):
    return {
        "id": id_,
        "name": f"{name} {version}",
        "version": version,
        "date_published": pub,
        "enabled": enabled,
        "category": {"name": "Firmware", "slug": "firmware"},
        "product_lines": ["unifi"],
        "file_url": f"https://dl.example/{id_}",
        "size": 1000,
        "release_notes_url": None,
    }


def db_rows():
    conn = scraper.get_conn()
    rows = conn.execute("SELECT id, device, version, date_published FROM releases ORDER BY id").fetchall()
    conn.close()
    return rows


def test_scrape_stores_recent_and_skips_old(monkeypatch):
    today = date.today()
    recent = (today - timedelta(days=10)).isoformat()
    ancient = (today - timedelta(days=scraper.DAYS_BACK + 10)).isoformat()
    page = {
        "downloads": [
            make_item(1, "Dream Machine", "1.0.0", recent),
            make_item(2, "Dream Machine", "0.9.0", ancient),
            make_item(3, "Dream Router", "2.0.0", recent, enabled=False),
        ],
        "pagination": {"totalPages": 1, "page": 1},
    }
    monkeypatch.setattr(scraper, "fetch_page", lambda p: page)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)

    result = scraper.scrape()

    assert result["stored"] == 1
    rows = db_rows()
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] == "Dream Machine"


def test_scrape_retries_cdn_duplicate_pages(monkeypatch):
    today = date.today()
    recent = (today - timedelta(days=5)).isoformat()
    page1 = {
        "downloads": [make_item(1, "Dream Machine", "1.0.0", recent)],
        "pagination": {"totalPages": 2, "page": 1},
    }
    page2 = {
        "downloads": [make_item(2, "Dream Router", "2.0.0", recent)],
        "pagination": {"totalPages": 2, "page": 2},
    }
    calls = {"n": 0}

    def fake_fetch(p):
        if p == 1:
            return page1
        calls["n"] += 1
        # first request for page 2 returns page 1's body (CDN cache bug)
        return page1 if calls["n"] == 1 else page2

    sleeps = []
    monkeypatch.setattr(scraper, "fetch_page", fake_fetch)
    monkeypatch.setattr(scraper.time, "sleep", sleeps.append)

    result = scraper.scrape()

    assert result["stored"] == 2
    assert {r[0] for r in db_rows()} == {1, 2}
    assert any(s >= 60 for s in sleeps)


def test_scrape_merges_and_prunes(monkeypatch):
    today = date.today()
    recent = (today - timedelta(days=1)).isoformat()
    stale = (today - timedelta(days=scraper.DAYS_BACK + 1)).isoformat()

    conn = scraper.get_conn()
    with conn:
        conn.execute(
            "INSERT INTO releases (id, name, device, version, category, product_lines, "
            "date_published, file_url, size, release_notes_url) "
            "VALUES (50, 'Old 0.1', 'Old', '0.1', 'Firmware', '[]', ?, NULL, NULL, NULL)",
            (stale,),
        )
        conn.execute(
            "INSERT INTO releases (id, name, device, version, category, product_lines, "
            "date_published, file_url, size, release_notes_url) "
            "VALUES (60, 'Kept 0.2', 'Kept', '0.2', 'Firmware', '[]', ?, NULL, NULL, NULL)",
            ((today - timedelta(days=30)).isoformat(),),
        )
    conn.close()

    page = {
        "downloads": [make_item(70, "Dream Machine", "3.0.0", recent)],
        "pagination": {"totalPages": 1, "page": 1},
    }
    monkeypatch.setattr(scraper, "fetch_page", lambda p: page)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)

    scraper.scrape()

    ids = {r[0] for r in db_rows()}
    assert ids == {60, 70}, "stale row pruned, existing in-window row kept, new row merged"
