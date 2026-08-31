from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from sports.guide import _normalize_jellyfin_series_metadata


EASTERN = ZoneInfo("America/New_York")


def _programme(xml: str) -> ElementTree.Element:
    return ElementTree.fromstring(xml)


def _episodes(programme: ElementTree.Element) -> dict[str, str]:
    return {
        str(node.attrib.get("system", "")): str(node.text or "")
        for node in programme.findall("episode-num")
    }


def test_real_affiliate_episode_is_normalized_for_jellyfin():
    programme = _programme(
        """
        <programme channel="NBCWGAL.us" start="20260830223000 +0000">
          <category>News</category><category>Series</category>
          <episode-num>S02 E225</episode-num>
          <title>NBC Nightly News With Tom Llamas</title><new />
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "NBC Nightly News"
    episodes = _episodes(programme)
    assert episodes["SxxExx"] == "S02E225"
    assert episodes["xmltv_ns"] == "1.224."
    assert episodes["dd_progid"].startswith("m3u-picker:")
    assert programme.find("new") is not None


def test_nbc_news_now_is_excluded_from_dvr_series_normalization():
    programme = _programme(
        """
        <programme channel="NBCNewsNow.us" start="20260829020000 +0000">
          <category>News</category><category>Series</category>
          <episode-num>S02 E224</episode-num>
          <title>NBC Nightly News With Tom Llamas  ᴺᵉʷ</title><previously-shown />
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "NBC Nightly News With Tom Llamas"
    assert _episodes(programme) == {"": "S02 E224"}
    assert programme.find("new") is not None


def test_inline_new_marker_is_removed_from_every_programme_title():
    programme = _programme(
        """
        <programme channel="FOXWPMT.us" start="20260901000000 +0000">
          <title>Beat Shazam  ᴺᵉʷ</title>
          <desc>S08 E08 Battle of the Best Friends!</desc>
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "Beat Shazam"
    assert programme.find("new") is not None
    assert _episodes(programme)["SxxExx"] == "S08E08"


def test_inline_live_marker_is_removed_without_marking_programme_new():
    programme = _programme(
        """
        <programme channel="FoxSports1.us" start="20260901000000 +0000">
          <title>MLB Baseball : Miami at Washington  ᴸᶦᵛᵉ</title>
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "MLB Baseball : Miami at Washington"
    assert programme.find("new") is None
    assert _episodes(programme) == {}


def test_generated_sports_programme_never_receives_series_metadata():
    programme = _programme(
        """
        <programme channel="m3u-picker-sports-example" start="20260901000000 +0000">
          <title>Wizards Classics  ᴺᵉʷ</title>
          <desc>S01 E11 From April 18, 1986.</desc>
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "Wizards Classics"
    assert programme.find("new") is not None
    assert _episodes(programme) == {}


def test_real_episode_can_be_promoted_from_provider_description():
    programme = _programme(
        """
        <programme channel="NBCWGAL.us" start="20260830223000 +0000">
          <title>NBC Nightly News With Tom Llamas</title>
          <desc>S02 E225 Tom Llamas anchors the latest news.</desc>
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "NBC Nightly News"
    episodes = _episodes(programme)
    assert episodes["SxxExx"] == "S02E225"
    assert episodes["xmltv_ns"] == "1.224."
    assert episodes["dd_progid"].startswith("m3u-picker:")


def test_local_newscast_gets_canonical_title_and_date_episode():
    first = _programme(
        """
        <programme channel="NBCWGAL.us" start="20260829223000 +0000">
          <category>News</category><title>News 8 at 6:00</title>
        </programme>
        """
    )
    variant = _programme(
        """
        <programme channel="NBCWGAL.us" start="20260831220000 +0000">
          <title>News 8 at 6pm  ᴺᵉʷ</title>
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(first, EASTERN)
    _normalize_jellyfin_series_metadata(variant, EASTERN)

    assert first.findtext("title") == "News 8 at 6:00 PM"
    assert variant.findtext("title") == "News 8 at 6:00 PM"
    assert _episodes(first)["SxxExx"] == "S2026E241"
    assert _episodes(first)["xmltv_ns"] == "2025.240."
    assert _episodes(variant)["SxxExx"] == "S2026E243"
    assert _episodes(first)["dd_progid"] != _episodes(variant)["dd_progid"]
    assert variant.find("new") is not None


def test_existing_typed_episode_metadata_is_never_replaced():
    programme = _programme(
        """
        <programme channel="NBCWGAL.us" start="20260829223000 +0000">
          <category>News</category><title>News 8 at 6:00</title>
          <episode-num system="xmltv_ns">4.8.</episode-num>
        </programme>
        """
    )

    _normalize_jellyfin_series_metadata(programme, EASTERN)

    assert programme.findtext("title") == "News 8 at 6:00"
    assert _episodes(programme) == {"xmltv_ns": "4.8."}
