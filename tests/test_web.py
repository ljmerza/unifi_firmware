import json
from datetime import date, timedelta

import main
import pytest
import scraper


@pytest.fixture
def client():
    return main.app.test_client()


def seed(rows):
    conn = scraper.get_conn()
    with conn:
        conn.executemany(
            "INSERT INTO releases (id, name, device, version, category, product_lines, "
            "date_published, file_url, size, release_notes_url) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    conn.close()


def test_health(client):
    assert client.get("/health").json == {"ok": True}


def test_api_releases_groups_by_device(client):
    today = date.today()
    seed(
        [
            (
                1,
                "UDM Pro 2.0",
                "UDM Pro",
                "2.0",
                "Firmware",
                '["unifi"]',
                today.isoformat(),
                "https://dl/2",
                None,
                None,
            ),
            (
                2,
                "UDM Pro 1.0",
                "UDM Pro",
                "1.0",
                "Firmware",
                '["unifi"]',
                (today - timedelta(days=30)).isoformat(),
                "https://dl/1",
                None,
                None,
            ),
            (
                3,
                "Other App 9.9",
                "Other App",
                "9.9",
                "Software",
                '["unifi"]',
                today.isoformat(),
                "https://dl/3",
                None,
                None,
            ),
        ]
    )
    data = client.get("/api/releases").json
    assert data["total"] == 3
    assert len(data["devices"]) == 2
    udm = next(d for d in data["devices"] if d["device"] == "UDM Pro")
    assert [r["version"] for r in udm["releases"]] == ["2.0", "1.0"], "newest first"


def test_index_renders_devices(client):
    seed(
        [
            (
                1,
                "UDM Pro 2.0",
                "UDM Pro",
                "2.0",
                "Firmware",
                '["unifi"]',
                date.today().isoformat(),
                "https://dl/2",
                None,
                None,
            ),
        ]
    )
    resp = client.get("/")
    assert resp.status_code == 200
    devices = json.loads(resp.text.split("const DEVICES = ", 1)[1].split(";\n", 1)[0])
    assert devices[0]["device"] == "UDM Pro"
