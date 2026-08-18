from sports import channel_three_alerts


def test_route_channel_three_rewrites_only_channel_three():
    text = (
        '#EXTM3U\n'
        '#EXTINF:-1 tvg-chno="1",One\n'
        'http://provider.test/one\n'
        '#EXTINF:-1 tvg-chno="3",Three\n'
        'http://provider.test/three\n'
        '#EXTINF:-1 tvg-chno="30",Thirty\n'
        'http://provider.test/thirty\n'
    )

    routed = channel_three_alerts.route_channel_three(
        text,
        "http://picker.test:9998",
    )

    assert (
        "http://picker.test:9998/sports/mlb-score-alerts/3/stream.m3u8"
        in routed
    )
    assert "http://provider.test/one" in routed
    assert "http://provider.test/thirty" in routed
    assert "http://provider.test/three" not in routed


def test_channel_three_state_is_idle_before_tune():
    payload = channel_three_alerts.state_payload("db.sqlite")

    assert payload["channel_number"] == 3
    assert payload["mode"] == "all-mlb-scores"
    assert payload["active"] is False
