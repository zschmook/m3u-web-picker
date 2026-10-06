# Minor professional hockey artwork

The bundled PNGs cover the active 2026–27 AHL, ECHL, SPHL, and FPHL teams and league marks. Team names and official source URLs are recorded in `sources.json`, verified October 5, 2026.

Sources: [AHL directory](https://theahl.com/team-map-directory), [ECHL teams](https://echl.com/teams), [SPHL](https://www.thesphl.com/), and [FPHL directory](https://www.federalhockey.com/directory). San Jose's mark comes from its [official team site](https://www.sjbarracuda.com/).

Artwork is normalized to PNG within a 256-pixel box, keeping mark proportions and source transparency. AHL directory banners are cropped around their centered team marks. SVG source marks are rasterized during asset preparation; no new rendering dependency is needed by the app. The event-logo compositor reads these assets directly without requesting the app's own HTTP server.

Mobile Mysticks begin SPHL play in 2027–28 and are excluded from the active 2026–27 catalog. Current FPHL names include Baton Rouge Kingfish, Twin City Thunderbirds, and Oceanside Shock; prior feed names remain team aliases.
