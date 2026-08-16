from __future__ import annotations

import gzip
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from xml.etree import ElementTree


LOCAL_NETWORKS = ("ABC", "CBS", "NBC")


def base_xmltv_id(value: str) -> str:
    """Return the channel-level XMLTV id for a feed-qualified id."""
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
    expanded = {
        str(value or "").strip()
        for value in values
        if str(value or "").strip()
    }
    codes = {
        str(code or "").strip().upper()
        for code in country_codes
        if str(code or "").strip()
    }
    for value in list(expanded):
        for code in codes:
            alias = normalize(f"{code} - {value}")
            if alias:
                expanded.add(alias)
    return expanded


def _network_from_names(values: Iterable[str]) -> str:
    for value in values:
        text = str(value or "")
        match = re.search(r"\b(ABC|CBS|NBC)\b", text, flags=re.I)
        if match:
            return match.group(1).upper()
    return ""


def _callsign_from_channel(channel_id: str, names: Iterable[str]) -> str:
    # Prefer an explicit parenthesized callsign from the playlist name.
    for value in names:
        match = re.search(
            r"\(([KW][A-Z]{2,3})(?:-[A-Z0-9]+)?\)",
            str(value or "").upper(),
        )
        if match:
            return match.group(1)

    # iptv-org local IDs often look like WDIODT101.us, WMAQTV51.us,
    # WJAR101.us, or WHPDT1.us. Strip the numeric service suffix first, then
    # remove DT/TV/LD/CD only when the remaining stem is too long to itself be
    # a valid legacy/current US callsign (so WFTV stays WFTV).
    local = base_xmltv_id(channel_id).split(".", 1)[0].upper()
    stem = re.split(r"\d", local, maxsplit=1)[0]
    if len(stem) > 4:
        stem = re.sub(r"(?:DT|TV|LD|CD)$", "", stem)
    return stem if re.fullmatch(r"[KW][A-Z]{2,3}", stem) else ""


def local_affiliate_alias(channel_id: str, names: Iterable[str]) -> str:
    """Map an iptv-org local feed to IPTV-EPG's network+callsign convention."""
    base = base_xmltv_id(channel_id)
    if not base.lower().endswith(".us"):
        return ""
    values = [str(value or "").strip() for value in names if str(value or "").strip()]
    network = _network_from_names(values)
    callsign = _callsign_from_channel(base, values)
    if not network or not callsign:
        return ""
    return f"{network}{callsign}.us"


def build_public_alias_maps(
    channels: Iterable[dict],
    wanted_ids: Iterable[str],
    wanted_names: Iterable[str],
    country_codes: Iterable[str],
    normalize: Callable[[str], str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build public-source aliases that resolve to exact M3U XMLTV ids.

    These maps are intentionally for the IPTV-EPG public fallback only. The
    provider/Xtream XMLTV path continues using exact IDs with no aliasing.
    """
    exact_wanted = {
        str(value or "").strip()
        for value in wanted_ids
        if str(value or "").strip()
    }
    normalized_wanted_names = {
        str(value or "").strip()
        for value in wanted_names
        if str(value or "").strip()
    }
    id_targets: dict[str, set[str]] = {}
    name_targets: dict[str, set[str]] = {}

    def add_id(alias: str, target: str) -> None:
        clean_alias = str(alias or "").strip()
        clean_target = str(target or "").strip()
        if clean_alias and clean_target:
            id_targets.setdefault(clean_alias, set()).add(clean_target)

    def add_name(alias: str, target: str) -> None:
        clean_alias = str(alias or "").strip()
        clean_target = str(target or "").strip()
        if clean_alias and clean_target:
            name_targets.setdefault(clean_alias, set()).add(clean_target)

    # Exact/base feed aliases work even if a selected ID is not represented in
    # the currently loaded channel objects for some reason.
    for target in exact_wanted:
        add_id(target, target)
        add_id(base_xmltv_id(target), target)

    country_codes = {
        str(code or "").strip().upper()
        for code in country_codes
        if str(code or "").strip()
    }

    for channel in channels:
        target = str(channel.get("tvg_id", "") or "").strip()
        if not target:
            continue
        names = [
            str(channel.get("tvg_name", "") or "").strip(),
            str(channel.get("name", "") or "").strip(),
        ]
        normalized_names = {normalize(value) for value in names if value}
        normalized_names.discard("")
        if target not in exact_wanted and not (normalized_names & normalized_wanted_names):
            continue

        add_id(target, target)
        add_id(base_xmltv_id(target), target)
        affiliate = local_affiliate_alias(target, names)
        if affiliate:
            add_id(affiliate, target)

        for normalized_name in normalized_names:
            add_name(normalized_name, target)
            for code in country_codes:
                prefixed = normalize(f"{code} - {normalized_name}")
                if prefixed:
                    add_name(prefixed, target)

    return id_targets, name_targets


def _targets_for_source_id(source_id: str, allowed_ids: set[str]) -> list[str]:
    source = str(source_id or "").strip()
    if not source:
        return []
    if "@" in source:
        if source in allowed_ids:
            return [source]
        base = base_xmltv_id(source)
        return [base] if base in allowed_ids else []
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
    """Compatibility helper retained for focused unit tests."""
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


def rewrite_public_epg_file(
    path: Path,
    *,
    id_targets: dict[str, set[str]],
    name_targets: dict[str, set[str]],
    normalize: Callable[[str], str],
    cancel_check=None,
) -> tuple[int, int]:
    """Rewrite one already-filtered public gzip guide to exact Picker IDs."""
    import sports

    source = Path(path)
    temp = source.with_name(source.name + ".compat.tmp")
    temp.unlink(missing_ok=True)
    source_targets: dict[str, set[str]] = {}
    kept_channels = 0
    kept_programmes = 0
    scanned_lines = 0

    try:
        with gzip.open(source, "rt", encoding="utf-8", errors="replace") as input_handle, gzip.open(
            temp, "wt", encoding="utf-8", compresslevel=1
        ) as output_handle:
            output_handle.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            output_handle.write('<tv source-info-name="IPTV-EPG.org filtered" source-info-url="https://iptv-epg.org">\n')
            block_lines: list[str] = []
            block_kind = ""

            for line in input_handle:
                scanned_lines += 1
                if scanned_lines % 50000 == 0 and cancel_check and cancel_check():
                    raise sports.ScanCancelled()
                stripped = line.lstrip()
                if not block_kind:
                    if stripped.startswith("<channel "):
                        block_kind = "channel"
                        block_lines = [line]
                    elif stripped.startswith("<programme "):
                        block_kind = "programme"
                        block_lines = [line]
                    else:
                        continue
                else:
                    block_lines.append(line)

                if f"</{block_kind}>" not in line:
                    continue

                block = "".join(block_lines)
                try:
                    element = ElementTree.fromstring(block)
                except ElementTree.ParseError:
                    block_kind = ""
                    block_lines = []
                    continue

                if block_kind == "channel":
                    source_id = str(element.attrib.get("id", "") or "").strip()
                    targets = set(id_targets.get(source_id, set()))
                    if not targets:
                        for child in element:
                            if child.tag.rsplit("}", 1)[-1] != "display-name" or not child.text:
                                continue
                            targets.update(name_targets.get(normalize(child.text.strip()), set()))
                    if not targets:
                        # Preserve name-only sports candidates and other public
                        # rows that have no exact M3U id target. Combined XMLTV's
                        # ordinary exact-id filter will ignore them, while sports
                        # discovery can still use them.
                        targets = {source_id} if source_id else set()
                    if source_id and targets:
                        source_targets[source_id] = set(targets)
                    for target in sorted(targets):
                        clone = ElementTree.fromstring(block)
                        clone.attrib["id"] = target
                        output_handle.write(ElementTree.tostring(clone, encoding="unicode"))
                        output_handle.write("\n")
                        kept_channels += 1
                else:
                    source_id = str(element.attrib.get("channel", "") or "").strip()
                    targets = source_targets.get(source_id) or id_targets.get(source_id) or ({source_id} if source_id else set())
                    for target in sorted(targets):
                        clone = ElementTree.fromstring(block)
                        clone.attrib["channel"] = target
                        output_handle.write(ElementTree.tostring(clone, encoding="unicode"))
                        output_handle.write("\n")
                        kept_programmes += 1

                block_kind = ""
                block_lines = []

            output_handle.write("</tv>\n")

        temp.replace(source)
        return kept_channels, kept_programmes
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def install(core_module) -> None:
    """Install compatibility only around the IPTV-EPG public fallback path.

    Do not patch ``sports.guide._filtered_provider_xmltv`` here. That function
    is shared by the primary/provider XMLTV path (including Xtream), which must
    remain exact-ID behavior.
    """
    if getattr(core_module, "_public_epg_compat_installed", False):
        return

    import sports

    original_matchers = core_module._public_epg_relevant_matchers
    original_filter = core_module._filter_public_epg_cache

    def current_maps():
        wanted_ids, wanted_names = original_matchers()
        return build_public_alias_maps(
            core_module.channels,
            wanted_ids,
            wanted_names,
            core_module.public_epg_enabled_codes,
            sports._normalize,
        )

    def compatible_matchers() -> tuple[set[str], set[str]]:
        wanted_ids, wanted_names = original_matchers()
        id_targets, name_targets = build_public_alias_maps(
            core_module.channels,
            wanted_ids,
            wanted_names,
            core_module.public_epg_enabled_codes,
            sports._normalize,
        )
        return (
            set(wanted_ids) | set(id_targets),
            set(wanted_names) | set(name_targets),
        )

    def compatible_public_filter(country_code: str, *, cancel_check=None):
        ok, message = original_filter(country_code, cancel_check=cancel_check)
        if not ok:
            return ok, message

        code = str(country_code or "").strip().upper()
        destination = core_module.public_epg_filtered_path(code)
        id_targets, name_targets = current_maps()
        try:
            kept_channels, kept_programmes = rewrite_public_epg_file(
                destination,
                id_targets=id_targets,
                name_targets=name_targets,
                normalize=sports._normalize,
                cancel_check=cancel_check,
            )
            state = core_module.public_epg_state.setdefault(code, {})
            state["filtered_channels"] = kept_channels
            state["filtered_programmes"] = kept_programmes
            state["filtered_bytes"] = destination.stat().st_size
            state.pop("filter_error", None)
            core_module.save_config()
            return True, (
                f"{message} Public aliases reconciled to "
                f"{kept_channels:,} channels / {kept_programmes:,} programmes."
            )
        except sports.ScanCancelled:
            raise
        except Exception as exc:
            state = core_module.public_epg_state.setdefault(code, {})
            state["filter_error"] = str(exc)
            core_module.save_config()
            return False, str(exc)

    core_module._public_epg_relevant_matchers = compatible_matchers
    core_module._filter_public_epg_cache = compatible_public_filter
    core_module._public_epg_compat_installed = True
