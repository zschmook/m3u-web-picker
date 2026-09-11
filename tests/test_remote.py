from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from PIL import Image

from api import guide as guide_api
from api import remote as remote_api
from media import director
from sports import remote_games


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


def channel(index: int, *, number: str | None = None) -> dict:
    return {
        "id": f"/guide/play/manual/channel-{index}",
        "number": number or str(index),
        "name": f"Channel {index}",
        "group": "Test",
        "logo": "",
        "target": f"https://provider.test/{index}.m3u8",
    }


@pytest.fixture(autouse=True)
def restore_director_state(monkeypatch):
    monkeypatch.setattr(director, "_save_selection", lambda _channel_id: None)
    with director._LOCK:
        director._CATALOG.clear()
        director._SELECTED_ID = ""
        director._LAST_PERSISTED_WALL = 0.0
        director._REVISION = 1
        director._SWITCHING = False
        director._LAST_ERROR = ""
        director._SESSION = None
        director._RETIRED_DIRECTORIES.clear()
    with guide_api._ROKU_DIRECTOR_LOCK:
        guide_api._ROKU_DIRECTOR_RECEIVERS.clear()
        guide_api._ROKU_DIRECTOR_RELAUNCHING.clear()
    yield


def test_remote_channel_has_stable_playlist_and_guide_identity():
    value = director.playlist("http://picker.test:9998")
    assert 'tvg-chno="0.2"' in value
    assert ",Remote" in value
    assert "http://picker.test:9998/director/stream.m3u8" in value
    assert director.guide_item()["play_url"] == "/guide/play/director/0.2"


def test_roku_uses_the_stable_channel_zero_two_playlist(monkeypatch, tmp_path):
    app = Flask(__name__)
    guide_api.register_guide_routes(app)
    launched = []
    sent_home = []
    playlist = tmp_path / "stream.m3u8"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(
        guide_api,
        "load_settings",
        lambda: SimpleNamespace(lan_host="10.0.0.5", external_port=9998, port=9999),
    )
    monkeypatch.setattr(guide_api, "_resolve_roku_host", lambda _data: ("10.0.0.8", ""))
    monkeypatch.setattr(remote_api, "_manual_channel_payload", lambda: ([], {}))
    monkeypatch.setattr(director, "safe_media_file", lambda filename: playlist if filename == "stream.m3u8" else None)
    monkeypatch.setattr(guide_api.roku, "launch_dev", lambda host, url: launched.append((host, url)))
    monkeypatch.setattr(guide_api.roku, "device_info", lambda _host: {"name": "Living Room Roku"})
    monkeypatch.setattr(guide_api.roku, "send_home", lambda host: sent_home.append(host))
    monkeypatch.setattr(guide_api.roku_devices, "device_key", lambda _info: "")

    client = app.test_client()
    response = client.post(
        "/api/guide/roku/start",
        json={"play_url": director.PLAY_URL, "roku_host": "10.0.0.8"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["token"] == ""
    assert payload["playlist_path"] == director.STREAM_PATH
    assert payload["media_url"] == "http://10.0.0.5:9998/director/stream.m3u8"
    assert launched == [("10.0.0.8", payload["media_url"])]
    assert guide_api._ROKU_DIRECTOR_RECEIVERS == {"10.0.0.8": payload["media_url"]}

    stopped = client.post("/api/guide/roku/stop", json={"roku_host": "10.0.0.8"})
    assert stopped.status_code == 200
    assert sent_home == ["10.0.0.8"]
    assert guide_api._ROKU_DIRECTOR_RECEIVERS == {}


def test_catalog_accepts_manual_and_generated_sports_but_not_virtual_channels():
    valid = channel(1)
    sports_channel = {
        **channel(3, number="1000"),
        "id": "/guide/play/sports/1000",
    }
    director.set_channel_catalog([
        valid,
        {**channel(2), "id": director.PLAY_URL, "number": "0.2"},
        sports_channel,
    ])
    assert list(director._CATALOG) == [valid["id"], sports_channel["id"]]
    assert director._CATALOG[sports_channel["id"]]["kind"] == "sports"
    with pytest.raises(ValueError, match="enabled channel"):
        director.select_channel(director.PLAY_URL)


def test_selected_channel_is_stream_copy_without_normalization(tmp_path, monkeypatch):
    monkeypatch.setattr(director, "ffmpeg_executable", lambda: "ffmpeg")
    command = director.stream_copy_command(
        tmp_path,
        "https://provider.test/live.m3u8",
        8,
        start_number=1200,
    )
    joined = " ".join(command)
    assert "-c copy" in joined
    assert "h264_nvenc" not in joined
    assert "libx264" not in joined
    assert "-vf" not in command
    assert "-af" not in command
    assert command[command.index("-start_number") + 1] == "1200"
    assert "segment_8_%010d.ts" in joined


def test_warmed_handoff_starts_beyond_old_sequence_window(tmp_path, monkeypatch):
    directory = tmp_path / "old"
    directory.mkdir()
    (directory / "stream.m3u8").write_text(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:100\n"
        + "#EXTINF:1.0,\nsegment.ts\n" * 12,
        encoding="utf-8",
    )
    monkeypatch.setattr(director.time, "time", lambda: 50.0)
    with director._LOCK:
        director._SESSION = director.DirectorSession(directory, FakeProcess(), 3, "old")

    assert director._next_hls_start_number() == 111 + director.HANDOFF_SEQUENCE_GAP


def test_public_manifest_maps_replacement_to_next_sequence_and_generation(tmp_path):
    old_directory = tmp_path / "old"
    new_directory = tmp_path / "new"
    old_directory.mkdir()
    new_directory.mkdir()
    old_manifest = old_directory / "stream.m3u8"
    new_manifest = new_directory / "stream.m3u8"
    old_manifest.write_text(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:100\n"
        + "#EXTINF:1.0,\nold.ts\n" * 12,
        encoding="utf-8",
    )
    new_manifest.write_text(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:150\n#EXT-X-DISCONTINUITY\n"
        + "#EXTINF:1.0,\nnew.ts\n" * 3,
        encoding="utf-8",
    )
    old = director.DirectorSession(
        old_directory,
        FakeProcess(),
        2,
        "old",
        sequence_offset=0,
        discontinuity_sequence=2,
    )
    replacement = director.DirectorSession(
        new_directory,
        FakeProcess(),
        3,
        "new",
        sequence_offset=112 - 150,
        discontinuity_sequence=3,
    )
    with director._LOCK:
        director._SESSION = replacement

    text = director.public_manifest(new_manifest)

    assert "#EXT-X-MEDIA-SEQUENCE:112" in text
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:2" in text
    assert "#EXT-X-DISCONTINUITY\n#EXTINF" in text

    # Once FFmpeg rolls the marker out of its private window, the header advances
    # so the first listed segment remains in the same discontinuity generation.
    new_manifest.write_text(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:151\n#EXTINF:1.0,\nnew.ts\n",
        encoding="utf-8",
    )
    text = director.public_manifest(new_manifest)
    assert "#EXT-X-MEDIA-SEQUENCE:113" in text
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:3" in text


def test_idle_slate_contains_the_lan_remote_qr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        director,
        "load_settings",
        lambda: SimpleNamespace(lan_host="10.0.0.18", external_port=9998),
    )
    path = tmp_path / "idle.png"

    director._write_idle_slate(path)

    with Image.open(path) as image:
        assert image.size == (1920, 1080)
        # The bright QR square occupies the center of an otherwise dark slate.
        assert image.getpixel((960, 540)) != image.getpixel((10, 10))
    assert director.remote_url() == "http://10.0.0.18:9998/remote"


def test_remote_url_prefers_secure_control_url(monkeypatch):
    monkeypatch.setattr(
        director,
        "load_settings",
        lambda: SimpleNamespace(
            lan_host="10.0.0.18",
            external_port=9998,
            remote_url="https://m3u-web-picker.tail.example/remote/",
        ),
    )

    assert director.remote_url() == "https://m3u-web-picker.tail.example/remote"


def test_handoff_keeps_old_segments_available(tmp_path, monkeypatch):
    director.set_channel_catalog([channel(1), channel(2)])
    sessions = []

    def new_session(channel_id, revision, _displaced=None):
        directory = tmp_path / f"session-{revision}"
        directory.mkdir()
        (directory / "stream.m3u8").write_text(
            f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{revision * 100}\n"
            f"#EXTINF:1.0,\nsegment_{revision}_1.ts\n",
            encoding="utf-8",
        )
        (directory / f"segment_{revision}_1.ts").write_bytes(b"segment")
        session = director.DirectorSession(directory, FakeProcess(), revision, channel_id)
        sessions.append(session)
        return session

    stopped = []
    monkeypatch.setattr(director, "_new_session", new_session)
    monkeypatch.setattr(director, "terminate", lambda process: stopped.append(process))

    director.select_channel(channel(1)["id"])
    director.select_channel(channel(2)["id"])

    assert director.state_payload()["selected_id"] == channel(2)["id"]
    assert stopped == [sessions[0].process]
    assert sessions[1].sequence_offset == -99
    assert sessions[1].discontinuity_sequence == sessions[0].discontinuity_sequence + 1
    assert "#EXT-X-MEDIA-SEQUENCE:201" in director.public_manifest(
        sessions[1].directory / "stream.m3u8"
    )
    assert director.safe_media_file("segment_3_1.ts") == sessions[1].directory / "segment_3_1.ts"
    assert director.safe_media_file("segment_3_1.ts") is not None
    assert director.safe_media_file("segment_3_1.ts").read_bytes() == b"segment"
    assert director.safe_media_file("segment_2_1.ts") == sessions[0].directory / "segment_2_1.ts"


def test_switching_serves_stopped_old_playlist_without_spawning_again(tmp_path, monkeypatch):
    directory = tmp_path / "holding"
    directory.mkdir()
    manifest = directory / "stream.m3u8"
    manifest.write_text(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:100\n#EXTINF:1.0,\nsegment_2_1.ts\n",
        encoding="utf-8",
    )
    (directory / "segment_2_1.ts").write_bytes(b"old")
    process = FakeProcess()
    process.returncode = 0
    with director._LOCK:
        director._SESSION = director.DirectorSession(directory, process, 2, "old")
        director._REVISION = 3
        director._SWITCHING = True
    monkeypatch.setattr(
        director,
        "_activate",
        lambda *_args: pytest.fail("a holding playlist must not spawn another encoder"),
    )

    assert director.safe_media_file("stream.m3u8") == manifest
    assert director.safe_media_file("segment_2_1.ts").read_bytes() == b"old"


def test_queued_activation_reuses_live_winner_for_revision(tmp_path, monkeypatch):
    directory = tmp_path / "winner"
    directory.mkdir()
    winner = director.DirectorSession(directory, FakeProcess(), 4, "winner")
    with director._LOCK:
        director._SESSION = winner
        director._REVISION = 4
    monkeypatch.setattr(
        director,
        "_new_session",
        lambda *_args: pytest.fail("the winning session must be reused"),
    )

    assert director._activate("stale-request", 4) is winner


def test_selection_is_remembered_and_stop_clears_it(tmp_path, monkeypatch):
    director.set_channel_catalog([channel(1)])
    saved = []

    def new_session(channel_id, revision, _displaced=None):
        directory = tmp_path / f"memory-{revision}"
        directory.mkdir()
        (directory / "stream.m3u8").write_text(
            f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{revision * 100}\n"
            f"#EXTINF:1.0,\nsegment_{revision}_1.ts\n",
            encoding="utf-8",
        )
        return director.DirectorSession(directory, FakeProcess(), revision, channel_id)

    monkeypatch.setattr(director, "_new_session", new_session)
    monkeypatch.setattr(director, "_save_selection", lambda value: saved.append(value))
    monkeypatch.setattr(director, "terminate", lambda _process: None)

    director.select_channel(channel(1)["id"])
    director.stop()

    assert saved == [channel(1)["id"], ""]
    assert director.state_payload()["selected_id"] == ""
    assert director.state_payload()["idle_timeout_seconds"] == 300
    assert director.state_payload()["remote_url"].endswith("/remote")


def test_unwatched_source_expires_back_to_qr_default(tmp_path, monkeypatch):
    process = FakeProcess()
    directory = tmp_path / "expired"
    directory.mkdir()
    saved = []
    monkeypatch.setattr(director, "_save_selection", lambda value: saved.append(value))
    with director._LOCK:
        director._SELECTED_ID = channel(1)["id"]
        director._SESSION = director.DirectorSession(
            directory,
            process,
            revision=4,
            channel_id=channel(1)["id"],
            last_access_monotonic=100.0,
        )

    expired = director._expire_idle_session(401.0)

    assert expired is not None
    assert expired.process is process
    assert director.state_payload()["selected_id"] == ""
    assert saved == [""]


def test_stale_remembered_source_expires_before_stream_restart(monkeypatch):
    saved = []
    monkeypatch.setattr(director, "_save_selection", lambda value: saved.append(value))
    monkeypatch.setattr(director.time, "time", lambda: 1000.0)
    with director._LOCK:
        director._SELECTED_ID = channel(1)["id"]
        director._SESSION = None
        director._LAST_PERSISTED_WALL = 699.0

    expired = director._expire_idle_session()

    assert expired is None
    assert director.state_payload()["selected_id"] == ""
    assert saved == [""]


def test_virtual_channel_injection_places_zero_two_after_header():
    value = "#EXTM3U\n#EXTINF:-1,Existing\nhttp://existing\n"
    value = director.inject_channel(value, "http://picker.test:9998")
    lines = value.splitlines()
    assert 'tvg-chno="0.2"' in lines[1]
    assert lines[3] == "#EXTINF:-1,Existing"


def test_remote_payload_keeps_tv_programmes_and_adds_one_sports_target(monkeypatch):
    items = [
        {
            "number": 8,
            "name": "ESPN",
            "group": "Sports",
            "logo": "espn.png",
            "generated": False,
            "play_url": "/guide/play/manual/espn",
        },
        {
            "number": "0.2",
            "name": "Remote",
            "generated": True,
            "play_url": director.PLAY_URL,
        },
        {
            "number": 1000,
            "name": "Generated game",
            "generated": True,
            "play_url": "/guide/play/sports/1000",
        },
    ]
    enriched = [{**items[0], "now": {"title": "College Football"}, "next": None}]
    generated_rows = [
        {
            "event_key": "mlb:live-game",
            "event_title": "Visitors at Home",
            "display_name": "Visitors at Home",
            "league_id": "mlb",
            "assigned_number": 1000,
            "event_start": "2000-09-06T12:00:00-04:00",
            "event_end": "2998-09-06T15:00:00-04:00",
            "group_title": "Sports Today",
            "subtitle": "Home feed",
            "tvg_logo": "game.png",
            "url": "https://provider.test/game.m3u8",
            "is_replay": False,
        },
        {
            "event_key": "mlb:upcoming-game",
            "event_title": "Later at Tonight",
            "display_name": "Later at Tonight",
            "league_id": "mlb",
            "assigned_number": 1010,
            "event_start": "2999-09-06T18:00:00-04:00",
            "event_end": "2999-09-06T21:00:00-04:00",
            "group_title": "Sports Today",
            "subtitle": "Event feed",
            "tvg_logo": "later.png",
            "url": "https://provider.test/later.m3u8",
            "is_replay": False,
        },
    ]
    monkeypatch.setattr(remote_api.core, "curated_channels_for_guide", lambda: items)
    monkeypatch.setattr(remote_api.sports, "get_settings", lambda _path: {"timezone": "America/New_York"})
    monkeypatch.setattr(remote_api.sports, "generated_rows", lambda _path: generated_rows)
    monkeypatch.setattr(remote_api, "enrich_guide_channels", lambda values, *_args, **_kwargs: (enriched, {"available": True}))
    monkeypatch.setattr(remote_api.guide_api, "_resolve_guide_play_target", lambda play_url: "https://provider.test/espn")

    channels, games, epg = remote_api._remote_payload(refresh_sports=False)

    assert epg["available"] is True
    assert [item["name"] for item in channels] == ["ESPN"]
    assert channels[0]["now"]["title"] == "College Football"
    assert games[0]["title"] == "Visitors at Home"
    assert games[0]["phase"] == "live"
    assert games[0]["selectable"] is True
    assert games[1]["phase"] == "upcoming"
    assert games[1]["selectable"] is False
    assert list(director._CATALOG) == ["/guide/play/manual/espn", "/guide/play/sports/1000"]
    assert director._CATALOG["/guide/play/sports/1000"]["target"] == "https://provider.test/game.m3u8"


def test_sports_cards_dedupe_feeds_and_disable_upcoming_games():
    rows = [
        {
            "event_key": "ncaaf:game-one",
            "event_title": "Away at Home",
            "league_id": "ncaaf-fbs",
            "assigned_number": number,
            "event_start": "2026-09-06T14:00:00-04:00",
            "event_end": "2026-09-06T18:00:00-04:00",
            "subtitle": feed,
            "url": f"https://provider.test/{number}.m3u8",
        }
        for number, feed in ((1011, "Alternate feed"), (1010, "Preferred feed"))
    ]

    cards = remote_games.game_cards(
        rows,
        timezone_name="America/New_York",
        now=remote_games._datetime("2026-09-06T12:00:00-04:00"),
        refresh=False,
    )

    assert len(cards) == 1
    assert cards[0]["number"] == "1010"
    assert cards[0]["phase"] == "upcoming"
    assert cards[0]["selectable"] is False


def test_ap_top_25_is_exposed_as_a_sports_card_boost():
    row = {
        "event_key": "ncaaf:ranked",
        "event_title": "Ball State at Ohio State",
    }
    event = {
        "competitions": [{
            "status": {"type": {"state": "in", "shortDetail": "3rd · 4:12"}},
            "competitors": [
                {"homeAway": "away", "score": "10", "team": {"displayName": "Ball State", "abbreviation": "BALL"}, "curatedRank": {"current": 99}},
                {"homeAway": "home", "score": "17", "team": {"displayName": "Ohio State", "abbreviation": "OSU"}, "curatedRank": {"current": 1}},
            ],
            "situation": {"lastPlay": {"text": "Touchdown Ohio State"}},
            "broadcasts": [{"names": ["FOX"]}],
        }],
    }

    snapshot = remote_games._football_snapshot(row, event, "ncaaf")

    assert snapshot["top_25"] is True
    assert snapshot["home"]["ap_rank"] == 1
    assert snapshot["away"]["ap_rank"] is None
    assert snapshot["status"] == "3rd · 4:12"
    assert snapshot["last_play"] == "Touchdown Ohio State"
    assert snapshot["network"] == "FOX"


def test_final_scores_survive_for_one_day_without_a_playable_stream(tmp_path):
    db_path = tmp_path / "scores.db"
    finished = {
        "id": "/guide/play/sports/2010",
        "event_key": "nfl:final-game",
        "sport": "nfl",
        "sport_label": "NFL",
        "title": "Eagles at Ravens",
        "phase": "final",
        "selectable": False,
        "away": {"name": "Eagles", "score": "27"},
        "home": {"name": "Ravens", "score": "24"},
    }
    first_seen = remote_games._datetime("2026-09-06T18:00:00-04:00")

    current = remote_games.retain_recent_finals(db_path, [finished], now=first_seen)
    retained = remote_games.retain_recent_finals(
        db_path,
        [],
        now=first_seen + timedelta(hours=23),
    )
    expired = remote_games.retain_recent_finals(
        db_path,
        [],
        now=first_seen + timedelta(days=1, minutes=1),
    )

    assert current == [finished]
    assert len(retained) == 1
    assert retained[0]["id"] == "history:nfl:final-game"
    assert retained[0]["phase"] == "final"
    assert retained[0]["selectable"] is False
    assert retained[0]["history"] is True
    assert expired == []


def test_upcoming_game_within_three_hours_replaces_that_teams_last_score(tmp_path):
    db_path = tmp_path / "scores.db"
    now = remote_games._datetime("2026-09-06T12:00:00-04:00")
    final = {
        "id": "/guide/play/sports/2010",
        "event_key": "nfl:last-eagles-game",
        "sport": "nfl",
        "phase": "final",
        "selectable": False,
        "away": {"name": "Philadelphia Eagles", "abbr": "PHI", "score": "27"},
        "home": {"name": "Baltimore Ravens", "abbr": "BAL", "score": "24"},
    }
    upcoming = {
        "id": "/guide/play/sports/2020",
        "event_key": "nfl:next-eagles-game",
        "sport": "nfl",
        "phase": "upcoming",
        "selectable": False,
        "start": (now + timedelta(hours=2, minutes=59)).isoformat(),
        "away": {"name": "Philadelphia Eagles", "abbr": "PHI", "score": "-"},
        "home": {"name": "Dallas Cowboys", "abbr": "DAL", "score": "-"},
    }

    remote_games.retain_recent_finals(db_path, [final], now=now)
    visible = remote_games.retain_recent_finals(
        db_path,
        [upcoming],
        now=now + timedelta(minutes=1),
    )
    visible_again = remote_games.retain_recent_finals(
        db_path,
        [],
        now=now + timedelta(minutes=2),
    )

    assert visible == [upcoming]
    assert len(visible_again) == 1
    assert visible_again[0]["event_key"] == "nfl:last-eagles-game"


def test_remote_page_assets_exist():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "remote.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "remote.js").read_text(encoding="utf-8")
    assert 'id="remoteCurrent"' in template
    assert 'id="remoteChannels"' in template
    assert 'data-remote-mode="tv"' in template
    assert 'data-remote-mode="sports"' in template
    assert 'id="remoteSportsFilters"' in template
    assert 'id="remoteAlertsButton"' in template
    assert 'rel="manifest"' in template
    assert "js/remote.js" in template
    assert "css/remote.css" in template
    assert "fetch(`/api/remote?mode=" in script
    assert 'data-channel-id="${id}"' in script
    assert 'isUpcoming ? "is-upcoming"' in script
    assert 'isLive ? "is-live"' in script
    assert "remote-live-badge" in script
    assert "Boolean(left.top_25)" in script
    assert 'navigator.serviceWorker.register("/remote-sw.js"' in script
    assert '"/api/remote/alerts/test"' in script


def test_test_alert_targets_an_enabled_movie_channel(monkeypatch):
    app = Flask(__name__)
    sent = {}
    monkeypatch.setattr(
        remote_api,
        "_remote_payload",
        lambda **_kwargs: ([{
            "id": "/guide/play/manual/amc-west",
            "name": "US: AMC (WEST)",
        }], [], {}),
    )
    monkeypatch.setattr(
        remote_api.push_alerts,
        "send",
        lambda db_path, endpoint, payload: sent.update(
            db_path=db_path,
            endpoint=endpoint,
            payload=payload,
        ),
    )
    remote_api.register_remote_routes(app)

    response = app.test_client().post(
        "/api/remote/alerts/test",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/test"},
    )

    assert response.status_code == 200
    assert sent["payload"]["title"] == "Jurassic Park is on"
    assert sent["payload"]["channel_id"] == "/guide/play/manual/amc-west"
    assert response.get_json()["channel"]["name"] == "US: AMC (WEST)"


def test_browser_player_follows_stable_remote_playlist(monkeypatch):
    app = Flask(__name__)
    seen = {}
    monkeypatch.setattr(director, "safe_media_file", lambda _filename: Path("stream.m3u8"))
    monkeypatch.setattr(
        remote_api.guide_api,
        "_resolve_guide_play_target",
        lambda _play_url: "http://127.0.0.1:9998/director/stream.m3u8",
    )

    def response_for(target, **kwargs):
        seen.update(target=target, kwargs=kwargs)
        return "ok"

    monkeypatch.setattr(remote_api.browser, "response_for", response_for)
    remote_api.register_remote_routes(app)

    response = app.test_client().get(director.PLAY_URL)

    assert response.status_code == 200
    assert seen["target"] == "http://127.0.0.1:9998/director/stream.m3u8"
    assert seen["kwargs"]["stream_copy"] is True
    assert "stop_when" not in seen["kwargs"]
