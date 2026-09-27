# Domain lists

Code lists that vary by **domain as well as by country**. Crops are agricultural
*and* Ethiopian; a social registry install has no use for them.

Keeping them here rather than in `../codelists/` means one artifact per country
without every install loading every domain's lists — a Farmer Registry reads
`agriculture/`, NSR ignores it. Master Data loads a domain only when its chart
names it (`geoSeed.domains: [agriculture]`); registries then read the lists live
from Master Data.

The file format is identical to `../codelists/`, and `validate_pack.py` checks
both. An `attribute_id` must be unique across `codelists/` and every domain:
Master Data holds them in one table. Roles (`../../roles.json`) apply here too if
platform logic ever needs to reason about a domain value's meaning; none do today.

## agriculture/

| Lists | Used by |
|---|---|
| `CROP_COMMODITY`, `LIVESTOCK_TYPE`, `LIVESTOCK_BREED`, `MEANS_OF_ACQUISITION`, `SOURCE_OF_INCOME`, `WATER_SOURCE` | Farmer Registry |
| `CROP_COMMODITY`, `CROP_SEASON`, `SOIL_FERTILITY`, `WATER_SOURCE`, and the crop-season lists: `CROPPING_SYSTEM`, `LAND_PREPARATION_METHOD`, `IRRIGATION_SOURCE`, `IRRIGATION_METHOD`, `SEED_TYPE`, `SEED_SOURCE`, `SEED_VARIETY`, `SOWING_METHOD`, `FERTILIZER_TYPE`, `FARM_MACHINERY`, `CROP_GROWTH_STAGE`, `CROP_CONDITION`, `INFESTATION_TYPE`, `INFESTATION_AGENT`, `INFESTATION_SEVERITY`, `PEST_CONTROL_ACTION`, `CROP_DAMAGE_CAUSE`, `AGRO_ECOLOGICAL_ZONE` | Crop Sown Registry |

Seasons follow Ethiopia's CSA Agricultural Sample Survey: Meher (main rains),
Belg (short rains) and irrigated dry-season production. Each list's codes carry a
short list prefix (`SEASON_MEHER`, `SEV_HIGH`) so a code names its list on its own.
