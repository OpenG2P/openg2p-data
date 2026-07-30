# geo/ — superseded by [`../packs`](../packs)

`geo.csv` is the platform's original geography: a flat CSV of
`country,region,district,ward,village` name columns, loaded by
`registry-platform/docker/db-seed/load_geo_data.py`, which mints UUIDs for each
unit as it goes.

It still works and is still wired up (the Farmer Registry's `loadGeoData` step
reads it), so it has been left in place. New work should use a **country pack**
instead.

## Why packs replaced it

| | `geo.csv` | a pack |
|---|---|---|
| Identifiers | UUIDs minted at load time | the unit's P-code, stable across reloads |
| Re-running | new UUIDs, so rows duplicate | upserts in place |
| Geometry | none — nothing to draw a map with | simplified boundary GeoJSON per level |
| Provenance | none | source, licence and version in `manifest.json` |
| Countries | one, hardcoded in the file | one directory per country, selected by Helm value |

The UUID minting is the important one. Because ids were generated at load time
rather than derived from the data, a reload produced a *different* set of ids for
the same places, so registry rows built against one load pointed at nothing after
the next. Packs use the P-code as the identifier, which makes reloading a refresh
rather than a migration.

## Kamuntu came from here

`geo.csv` describes **Kamuntu** — the platform's fictitious demo country — as a
uniform 4×4×4×4 tree of 1 / 4 / 16 / 64 / 254 units with no codes and no
geometry.

Kamuntu now ships as a proper pack at [`../packs/XKM`](../packs/XKM), and it
keeps this file's identity: the same five level names, and the same place names,
lifted into
[`../scripts/packs/kamuntu_names.json`](../scripts/packs/kamuntu_names.json) so
they survive this file being retired. What changed is that the pack has P-codes,
real boundary geometry, and a lumpy hierarchy instead of a perfectly uniform one
— a uniform tree makes every "largest and smallest unit" chart come out
suspiciously flat.

Retiring `geo.csv` means moving the Farmer Registry's geo step onto MDS pack
seeding, the way NSR already has (`dbSeed.loadGeoData: false`). Until then, do
not treat this file as a second source of truth: if both run against the same
environment, the pack's P-coded units and this file's UUID units both land in
`g2p_geo_level_values` and nothing reconciles them.
