import importlib.util
import json
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "live_dns_hotfix.py"
SPEC = importlib.util.spec_from_file_location("live_dns_hotfix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
hotfix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hotfix)


def _database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE devices (
                id INTEGER PRIMARY KEY,
                policy TEXT NOT NULL DEFAULT '{}',
                policy_version INTEGER NOT NULL DEFAULT 1,
                policy_checksum TEXT NOT NULL DEFAULT '',
                policy_revision INTEGER NOT NULL DEFAULT 0,
                policy_updated_at INTEGER
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta (key, value) VALUES ('policy_revision', '0');
            """
        )
        connection.execute(
            "INSERT INTO devices (id, policy, policy_checksum) VALUES (?, ?, ?)",
            (
                1,
                json.dumps({
                    "options": ["youtube", "tiktok"],
                    "custom_hosts": ["youtube.com", "example.org"],
                    "custom_apps": [
                        "com.google.android.youtube",
                        "com.example.keep",
                    ],
                    "app_rules": {
                        "com.google.android.youtube": {"mode": "both"},
                        "com.example.keep": {
                            "mode": "network",
                            "hosts": ["googlevideo.com", "api.example.org"],
                        },
                    },
                }),
                "old-checksum",
            ),
        )
        connection.execute(
            "INSERT INTO devices (id, policy, policy_checksum) VALUES (?, ?, ?)",
            (
                2,
                json.dumps({
                    "options": ["youtube"],
                    "custom_hosts": [],
                    "custom_apps": ["app.revanced.android.youtube"],
                    "app_rules": [
                        {
                            "package": "app.revanced.android.youtube",
                            "mode": "accessibility",
                        },
                        {
                            "package": "com.example.other",
                            "mode": "network",
                            "hosts": ["youtube-nocookie.com", "other.example"],
                        },
                    ],
                }),
                "old-checksum",
            ),
        )


def test_remove_youtube_policy_supports_real_devices_schema(tmp_path, monkeypatch):
    database = tmp_path / "filterphone.db"
    backups = tmp_path / "backups"
    _database(database)
    monkeypatch.setattr(hotfix, "DB_PATH", database)
    monkeypatch.setattr(hotfix, "BACKUP_DIR", backups)

    changed_ids, changed_count = hotfix._remove_youtube_policy_everywhere()

    assert changed_ids == [1, 2]
    assert changed_count == 2
    assert list(backups.glob("filterphone.db-before-youtube-dns-hotfix-*"))

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM devices ORDER BY id").fetchall()
        assert rows[0]["policy_version"] == 2
        assert rows[1]["policy_version"] == 2
        assert rows[0]["policy_checksum"] == rows[1]["policy_checksum"] == ""
        assert rows[0]["policy_revision"] == 1
        assert rows[1]["policy_revision"] == 2
        assert connection.execute(
            "SELECT value FROM meta WHERE key = 'policy_revision'"
        ).fetchone()[0] == "2"

    first = json.loads(rows[0]["policy"])
    assert first["options"] == ["tiktok"]
    assert first["custom_hosts"] == ["example.org"]
    assert first["custom_apps"] == ["com.example.keep"]
    assert "com.google.android.youtube" not in first["app_rules"]
    assert first["app_rules"]["com.example.keep"]["hosts"] == ["api.example.org"]

    second = json.loads(rows[1]["policy"])
    assert second["options"] == []
    assert second["custom_apps"] == []
    assert second["app_rules"] == [
        {
            "package": "com.example.other",
            "mode": "network",
            "hosts": ["other.example"],
        }
    ]

    assert hotfix._remove_youtube_policy_everywhere() == ([], 0)



def test_disable_youtube_safe_search_preserves_other_engines(monkeypatch):
    state = {
        "enabled": True,
        "bing": True,
        "duckduckgo": True,
        "ecosia": True,
        "google": True,
        "pixabay": True,
        "yandex": True,
        "youtube": True,
    }
    calls = []

    def fake_request(base_url, user, password, method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return dict(state)
        assert method == "PUT" and path == "/safesearch/settings"
        state.update(body)
        return {}

    monkeypatch.setattr(hotfix, "_request", fake_request)
    result = hotfix._disable_youtube_safe_search("http://adguard", "admin", "secret")

    assert result["youtube"] is False
    assert all(result[name] is True for name in (
        "enabled", "bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex"
    ))
    assert calls[0][:2] == ("GET", "/safesearch/status")
    assert calls[1][0:2] == ("PUT", "/safesearch/settings")
    assert calls[1][2]["youtube"] is False


def test_youtube_dns_check_covers_video_and_thumbnail_families():
    assert hotfix.YOUTUBE_DNS_CHECK_HOSTS["ytimg.com"] == "i.ytimg.com"
    assert hotfix.YOUTUBE_DNS_CHECK_HOSTS["ggpht.com"] == "yt3.ggpht.com"
    assert "youtubeembeddedplayer.googleapis.com" in hotfix.YOUTUBE_ALLOW_HOSTS
    assert "yt3.googleusercontent.com" in hotfix.YOUTUBE_ALLOW_HOSTS


def test_update_user_rules_removes_youtube_blocks_and_preserves_other_rules():
    current = [
        "||youtube.com^",
        "||i.ytimg.com^",
        "||ggpht.com^",
        "||googlevideo.com^",
        "||instagram.com^",
    ]
    merged, removed, _, _, added_youtube = hotfix.update_user_rules(current)

    assert "||youtube.com^" in removed
    assert "||i.ytimg.com^" in removed
    assert "||ggpht.com^" in removed
    assert "||googlevideo.com^" in removed
    assert "||instagram.com^" in merged
    assert all(rule.startswith("@@||") and "$important" in rule for rule in added_youtube)
