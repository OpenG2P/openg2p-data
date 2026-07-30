# openg2p-data

Sample and reference data for the OpenG2P platform, plus the generators that
produce it. Registry and Master Data db-seed images clone this repo at build
time, so what is committed here is what a seeded install contains.

## Layout

| Directory | What it holds |
|---|---|
| [`packs/`](packs) | **Country packs** — administrative hierarchy, P-codes and boundary geometry, one directory per country. The only supported way geography reaches the platform. Start with its [README](packs/README.md). |
| [`scripts/packs/`](scripts/packs) | Pack tooling: build a real pack from OCHA COD-AB, redraw the fictitious one, and validate either. |
| [`demography/`](demography) | Shared synthetic people: `individuals.csv`, `households.csv`, ~500 portrait images. Registry-agnostic — person `i0001` is the same individual in every registry, so cross-registry scenarios line up. |
| [`household-info/`](household-info) | The same population as consolidated wide CSVs (housing, assets, livelihoods, scores). |
| [`national-social-registry/`](national-social-registry) | `distributions.json` — attribute marginals the bulk sample generator draws from, extracted from a real populated registry. |
| [`users/`](users) | Sample portal users. |
| [`geo/`](geo) | **Legacy.** The pre-pack flat `geo.csv`. Superseded — see its [README](geo/README.md). |
| [`scripts/`](scripts) | Generators for everything above. |

## Which country does an install get?

Kamuntu — a **fictitious** country ([`packs/XKM`](packs/XKM)) — by default.

That default is deliberate. A demo install attaches invented poverty, enrolment
and payment figures to whatever geography it holds, and doing that to a real
country's name and outline is not something to do by accident. Real country packs
are first-class and one Helm value away (`geoSeed.countryPack` in the
`openg2p-master-data` chart), and they carry the licence obligations their
`manifest.json` records.

## Generators

Nothing here is generated at install time — the outputs are committed, so seed
data is reviewable in a diff and reproducible. Regenerate deliberately:

```bash
cd scripts/packs
python3 generate_synthetic_pack.py --preset kamuntu --out ../../packs/XKM --version 2026-07-30
python3 validate_pack.py ../../packs/ETH ../../packs/XKM     # always
```

The pack tools are pure Python — no shapely, no GEOS. Everything else needs
`pip install -r scripts/requirements.txt`.

`scripts/generate_nsr_bulk_sample.py` is the exception: it writes millions of
rows straight into a registry database rather than into this repo, reading its
geography from MDS and its attribute marginals from
`national-social-registry/distributions.json`. It is invoked by the NSR chart's
analytics jobs, not run by hand.
