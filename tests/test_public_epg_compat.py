from types import SimpleNamespace
from xml.etree import ElementTree

import public_epg_compat
from sports import guide as sports_guide


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").split())


def test_feed_qualified_ids_include_channel_level_alias():
    assert public_epg_compat.expand_xmltv_ids(
        {"NBCNewsNOW.us@SD", "Dateline247.us@HD"}
    ) == {
        "NBCNewsNOW.us@SD",
        "NBCNewsNOW.us",
        "Dateline247.us@HD",
        "Dateline247.us",
    }


def test_country_prefixed_public_epg_name_alias_is_added():
    aliases = public_epg_compat.expand_country_prefixed_names(
        {"nbc news now"},
        {"US"},
        _normalize,
    )
    assert "nbc news now" in aliases
    assert "us nbc news now" in aliases


def test_local_affiliate_alias_uses_network_and_callsign():
    channels = [
        {
            "tvg_id": "WDIODT101.us@HD",
            "tvg_name": "ABC 10 Duluth MN (WDIO) (1080p)",
            "name": "ABC 10 Duluth MN (WDIO) (1080p)",
        },
        {
            "tvg_id": "WMAQTV51.us@HD",
            "tvg_name": "NBC 5 Chicago Live News (1080p)",
            "name": "NBC 5 Chicago Live News (1080p)",
        },
        {
            "tvg_id": "KUTV21.us@HD",
            "tvg_name": "CBS 2 Salt Lake City UT (KUTV) (1080p)",
            "name": "CBS 2 Salt Lake City UT (KUTV) (1080p)",
        },
    ]
    id_targets, _name_targets = public_epg_compat.build_public_alias_maps(
        channels,
        {item["tvg_id"] for item in channels},
        set(),
        {"US"},
        _normalize,
    )

    assert id_targets["ABCWDIO.us"] == {"WDIODT101.us@HD"}
    assert id_targets["NBCWMAQ.us"] == {"WMAQTV51.us@HD"}
    assert id_targets["CBSKUTV.us"] == {"KUTV21.us@HD"}


def test_local_affiliate_alias_does_not_cross_network_subchannels():
    alias = public_epg_compat.local_affiliate_alias(
        "KSBW81.us@HD",
        ["NBC 8 Salinas CA (KSBW) (720p)"],
    )
    assert alias == "NBCKSBW.us"
    assert alias != "ABCKSBWDT2.us"


def test_base_public_guide_is_rewritten_to_exact_selected_feed_id():
    channel = b'<channel id="NBCNewsNOW.us"><display-name>US - NBC News NOW</display-name></channel>'
    programme = b'<programme channel="NBCNewsNOW.us" start="20260816170000 +0000" stop="20260816180000 +0000"><title>Dateline</title></programme>'

    result = public_epg_compat.rewrite_filtered_xmltv_result(
        ({"source-info-name": "test"}, [channel], [programme], {"NBCNewsNOW.us"}, {"NBCNewsNOW.us"}),
        {"NBCNewsNOW.us@SD"},
    )

    _attrs, channels, programmes, found_channels, found_programmes = result
    channel_element = ElementTree.fromstring(channels[0])
    programme_element = ElementTree.fromstring(programmes[0])
    assert channel_element.attrib["id"] == "NBCNewsNOW.us@SD"
    assert programme_element.attrib["channel"] == "NBCNewsNOW.us@SD"
    assert found_channels == {"NBCNewsNOW.us@SD"}
    assert found_programmes == {"NBCNewsNOW.us@SD"}


def test_base_public_guide_can_supply_multiple_selected_feeds():
    channel = b'<channel id="Example.us"><display-name>US - Example</display-name></channel>'
    programme = b'<programme channel="Example.us" start="20260816170000 +0000" stop="20260816180000 +0000"><title>Example</title></programme>'

    result = public_epg_compat.rewrite_filtered_xmltv_result(
        ({}, [channel], [programme], {"Example.us"}, {"Example.us"}),
        {"Example.us@SD", "Example.us@HD"},
    )

    _attrs, channels, programmes, found_channels, found_programmes = result
    assert {ElementTree.fromstring(item).attrib["id"] for item in channels} == {
        "Example.us@SD",
        "Example.us@HD",
    }
    assert {ElementTree.fromstring(item).attrib["channel"] for item in programmes} == {
        "Example.us@SD",
        "Example.us@HD",
    }
    assert found_channels == {"Example.us@SD", "Example.us@HD"}
    assert found_programmes == {"Example.us@SD", "Example.us@HD"}


def test_install_does_not_patch_shared_provider_or_xtream_xmltv_filter():
    original = sports_guide._filtered_provider_xmltv
    fake_core = SimpleNamespace(
        _public_epg_relevant_matchers=lambda: (set(), set()),
        _filter_public_epg_cache=lambda country_code, cancel_check=None: (True, "ok"),
    )

    public_epg_compat.install(fake_core)

    assert sports_guide._filtered_provider_xmltv is original
    assert fake_core._public_epg_compat_installed is True
