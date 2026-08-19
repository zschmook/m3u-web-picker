from pathlib import Path

import pytest

from sports import multiview


@pytest.fixture(autouse=True)
def restore_director_state():
    with multiview._LOCK:
        multiview._STATE.slots = list(multiview.DEFAULT_SLOTS)
        multiview._STATE.locked = [True, False, False, False]
        multiview._STATE.audio_slot = 0
        multiview._STATE.upset_ids = []
        multiview._STATE.revision = 1
        multiview._SESSION = None
    yield


def test_week_five_weights_keep_subscribed_game_primary():
    state = multiview.state_payload()
    assert state["slots"][0]["id"] == "ore-psu"
    assert state["slots"][0]["weight"] == 1000
    assert state["locked"][0] is True
    assert [game["weight"] for game in state["ticker"]] == sorted(
        (game["weight"] for game in state["ticker"]), reverse=True
    )


def test_unlocked_slot_accepts_ticker_game_and_old_game_returns_to_ticker():
    state = multiview.update_state({"slots": ["ore-psu", "wis-nd", "ala-uga", "usc-ill"]})
    assert state["slots"][1]["id"] == "wis-nd"
    assert "lsu-miss" in {game["id"] for game in state["ticker"]}


def test_locked_slot_rejects_replacement():
    multiview.update_state({"locked": [True, True, False, False]})
    with pytest.raises(ValueError, match="slot 1 is locked"):
        multiview.update_state({"slots": ["ore-psu", "wis-nd", "ala-uga", "usc-ill"]})


def test_upset_games_stack_at_left_of_ticker():
    state = multiview.update_state({"upset_ids": ["bay-aub", "wis-nd"]})
    assert [game["id"] for game in state["ticker"][:2]] == ["wis-nd", "bay-aub"]
    assert all(game["upset_alert"] for game in state["ticker"][:2])


def test_audio_can_follow_any_pane():
    state = multiview.update_state({"audio_slot": 3})
    assert state["audio_slot"] == 3


def test_resume_auto_unlocks_side_panes_and_restores_weighted_layout():
    multiview.update_state({"locked": [True, True, True, True]})
    state = multiview.reset_state()
    assert [game["id"] for game in state["slots"]] == multiview.DEFAULT_SLOTS
    assert state["locked"] == [True, False, False, False]


def test_ffmpeg_command_builds_one_by_three_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(multiview, "ffmpeg_executable", lambda: "ffmpeg")
    command = multiview.ffmpeg_command(
        Path(tmp_path),
        ["one.m3u8", "two.m3u8", "three.m3u8", "four.m3u8"],
        2,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "pad=1920:1080" in graph
    assert "overlay=x=1280:y=720" in graph
    map_positions = [index for index, value in enumerate(command) if value == "-map"]
    assert command[map_positions[1] + 1] == "2:a:0"


def test_playlist_exposes_stable_jellyfin_channel():
    value = multiview.playlist("http://picker.test:9998")
    assert "#EXTM3U" in value
    assert "NCAA Multiview" in value
    assert "http://picker.test:9998/sports/multiview/ncaa/stream.m3u8" in value


def test_multiview_is_injected_into_main_jellyfin_playlist():
    value = multiview.inject_channel("#EXTM3U\n#EXTINF:-1,Existing\nhttp://existing\n", "http://picker.test:9998")
    assert value.splitlines()[1].endswith(",NCAA Multiview")
    assert value.splitlines()[2] == "http://picker.test:9998/sports/multiview/ncaa/stream.m3u8"


def test_live_playlist_response_disables_conditional_caching(tmp_path):
    from flask import Flask
    from api.multiview import _media_response

    playlist = tmp_path / "stream.m3u8"
    playlist.write_text("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:2\n", encoding="utf-8")
    app = Flask(__name__)
    with app.test_request_context("/sports/multiview/ncaa/stream.m3u8"):
        response = _media_response(playlist, "stream.m3u8")
    assert response.status_code == 200
    assert "ETag" not in response.headers
    assert "Last-Modified" not in response.headers
    assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
