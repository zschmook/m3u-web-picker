from __future__ import annotations

from collections.abc import Callable, Iterable
from xml.etree import ElementTree


def base_xmltv_id(value: str) -> str:
    """Return the channel-level XMLTV id for a feed-qualified id.

    iptv-org playlists may identify a specific feed as ``Channel.us@SD`` while
    public XMLTV sources identify the same schedule as ``Channel.us``. Keep the
    exact id everywhere else, but allow the public-guide matcher to use the
    channel-level alias when the source guide does not publish feed ids.
    """
    clean = str(value or "").strip()
    return clean.split("@", 1)[0] if clean else ""


def expand_xmltv_ids(values: Iterable[str]) -> set[str]:
    expanded: set[str] = set()
    for raw in values:
        clean = str(raw or "").strip()
        if not clean:
            continue
        expanded.add(clean)
        base = base_xmltv_id(clean)
        if base:
            expanded.add(base)
    return expanded


def expand_country_prefixed_names(
    values: Iterable[str],
    country_codes: Iterable[str],
    normalize: Callable[[str], str],
) -> set[str]:
    """Add aliases for IPTV-EPG display names such as ``US - NBC News NOW``."""
    expanded = {str(value or "").strip() for value in values if str(value or "").strip()}
    codes = {str(code or "").strip().upper() for code in country_codes if str(code or "").strip()}
    for value in list(expanded):
        for code in codes:
            alias = normalize(f"{code} - {value}")
            if alias:
                expanded.add(alias)
    return expanded


def _targets_for_source_id(source_id: str, allowed_ids: set[str]) -> list[str]:
    source = str(source_id or "").strip()
    if not source:
        return []

    # A feed-qualified source is already specific. Prefer its exact selected
    # feed, falling back only to the selected channel-level id.
    if "@" in source:
        if source in allowed_ids:
            return [source]
        base = base_xmltv_id(source)
        return [base] if base in allowed_ids else []

    # A channel-level public guide can safely supply every selected feed for
    # that same channel. This is what lets ``Channel.us`` guide data populate
    # ``Channel.us@SD`` / ``Channel.us@HD`` playlist entries.
    return sorted(
        target
        for target in allowed_ids
        if base_xmltv_id(target) == source
    )


def _rewrite_fragments(
    fragments: Iterable[bytes],
    *,
    attribute: str,
    allowed_ids: set[str],
) -> tuple[list[bytes], set[str]]:
    rewritten: list[bytes] = []
    found: set[str] = set()
    for fragment in fragments:
        try:
            element = ElementTree.fromstring(fragment)
        except (ElementTree.ParseError, ValueError):
            continue
        source_id = str(element.attrib.get(attribute, "") or "").strip()
        targets = _targets_for_source_id(source_id, allowed_ids)
        for target in targets:
            clone = ElementTree.fromstring(fragment)
            clone.attrib[attribute] = target
            rewritten.append(ElementTree.tostring(clone, encoding="utf-8"))
            found.add(target)
    return rewritten, found


def rewrite_filtered_xmltv_result(
    result: tuple[dict[str, str], list[bytes], list[bytes], set[str], set[str]],
    allowed_channel_ids: Iterable[str],
) -> tuple[dict[str, str], list[bytes], list[bytes], set[str], set[str]]:
    """Rewrite public-guide aliases back to the exact ids served in the M3U."""
    attrs, channel_fragments, programme_fragments, _channels, _programmes = result
    allowed = {
        str(value or "").strip()
        for value in allowed_channel_ids
        if str(value or "").strip()
    }
    channels, found_channels = _rewrite_fragments(
        channel_fragments,
        attribute="id",
        allowed_ids=allowed,
    )
    programmes, found_programmes = _rewrite_fragments(
        programme_fragments,
        attribute="channel",
        allowed_ids=allowed,
    )
    return attrs, channels, programmes, found_channels, found_programmes


def install(core_module) -> None:
    """Install public-EPG compatibility aliases into the running application."""
    if getattr(core_module, "_public_epg_compat_installed", False):
        return

    import sports
    from sports import guide as sports_guide

    original_matchers = core_module._public_epg_relevant_matchers
    original_filtered_provider_xmltv = sports_guide._filtered_provider_xmltv

    def compatible_matchers() -> tuple[set[str], set[str]]:
        wanted_ids, wanted_names = original_matchers()
        return (
            expand_xmltv_ids(wanted_ids),
            expand_country_prefixed_names(
                wanted_names,
                core_module.public_epg_enabled_codes,
                sports._normalize,
            ),
        )

    def compatible_filtered_provider_xmltv(
        base_epg_path,
        allowed_channel_ids,
        *,
        cancel_check=None,
    ):
        exact_allowed = {
            str(value or "").strip()
            for value in allowed_channel_ids
            if str(value or "").strip()
        }
        result = original_filtered_provider_xmltv(
            base_epg_path,
            expand_xmltv_ids(exact_allowed),
            cancel_check=cancel_check,
        )
        return rewrite_filtered_xmltv_result(result, exact_allowed)

    core_module._public_epg_relevant_matchers = compatible_matchers
    sports_guide._filtered_provider_xmltv = compatible_filtered_provider_xmltv
    core_module._public_epg_compat_installed = True
