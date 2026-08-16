from xml.etree import ElementTree

import public_epg_compat


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
    normalize = lambda value: " ".join(value.lower().replace("-", " ").split())
    aliases = public_epg_compat.expand_country_prefixed_names(
        {"nbc news now"},
        {"US"},
        normalize,
    )
    assert "nbc news now" in aliases
    assert "us nbc news now" in aliases


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
