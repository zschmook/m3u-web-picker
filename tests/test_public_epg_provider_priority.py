from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

from sports.guide import build_combined_xmltv


def _guide(path: Path, title: str) -> None:
    path.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<tv>
  <channel id=\"provider.exact.id\"><display-name>Provider Channel</display-name></channel>
  <programme start=\"20260816170000 +0000\" stop=\"20260816180000 +0000\" channel=\"provider.exact.id\"><title>{title}</title></programme>
</tv>
""".format(title=title),
        encoding="utf-8",
    )


def test_primary_provider_xmltv_wins_over_public_fallback_for_same_airtime():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        provider = root / "xtream-provider.xml"
        public = root / "public-us.xml"
        _guide(provider, "Provider schedule")
        _guide(public, "Public fallback schedule")

        combined = build_combined_xmltv(
            provider,
            b'<?xml version="1.0" encoding="utf-8"?><tv></tv>',
            {"provider.exact.id"},
            fallback_epg_paths=[public],
        )

        xml = ElementTree.fromstring(combined)
        titles = [
            child.text
            for programme in xml
            if programme.tag.rsplit("}", 1)[-1] == "programme"
            for child in programme
            if child.tag.rsplit("}", 1)[-1] == "title"
        ]
        assert "Provider schedule" in titles
        assert "Public fallback schedule" not in titles
