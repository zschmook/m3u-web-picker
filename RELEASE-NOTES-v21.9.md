# Sports v21.9 release notes

v21.9 keeps all v21.8 event-lifecycle, cancellation, filtered-guide, and authoritative XMLTV programme fixes. It separates saved manual/static channels from generated sports channels so overlapping sources can coexist intentionally.

## Manual/static and generated sports namespaces

- Manual selections are no longer identified only by stream URL.
- Saved manual rows use metadata-aware `manual:*` keys derived from URL, provider XMLTV ID, provider channel number, name, and group.
- Existing URL-only selection keys migrate automatically when the provider playlist is loaded.
- A manual channel and generated sports feed may use the same stream URL and provider source ID without either row being suppressed.
- Manual rows retain provider metadata and saved order.
- Generated rows retain unique `m3u-picker-sports-*` XMLTV IDs and assigned sports channel numbers.
- Distinct manual provider rows sharing one URL remain separately selectable and persist across restart.
- Custom-group keys are migrated to the same manual namespace.

## EPG Manager polish

- The guide-table column is now labeled `Status`.
- The add-source row is blank while idle and shows only validation/submission feedback.
- Built-in guide rows show an updated timestamp with file size as secondary text.
- External rows show real states such as Updated, Never updated, or Refresh failed.
- The privacy notice remains in the section helper text rather than masquerading as row status.

## Validation

- Unit suite: 66 tests run, 60 passed, 6 Flask/Docker-image-dependent tests skipped in the lightweight environment.
- New regression coverage verifies manual/generated coexistence on the same URL, separate manual identities for shared URLs, and migration of v21.8 URL-only selections.
- Python compilation, JavaScript syntax, shell syntax, ZIP integrity, version marker, and release archive cleanliness were checked.
- Docker itself was unavailable in the build environment; the running container remains the final integration test.
