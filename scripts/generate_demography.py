"""Generate demography/individuals.csv + households.csv from geo/*.csv."""

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from _csv_utils import write_csv

REPO_ROOT = Path(__file__).resolve().parent.parent
GEO_DIR = REPO_ROOT / "geo"
OUT_DIR = REPO_ROOT / "demography"

NUM_INDIVIDUALS = 500
NUM_HOUSEHOLDS = 100

SEED = 42
random.seed(SEED)
Faker.seed(SEED)
fake = Faker("en_US")

INDIVIDUAL_UUID_PREFIX = "20000000-0000-4000-8000-"
HOUSEHOLD_UUID_PREFIX = "10000000-0000-4000-8000-"

MARITAL_STATUSES = ["SINGLE", "MARRIED", "WIDOWED", "DIVORCED"]
EDUCATION_LEVELS = [
    "ILLITERATE",
    "CAN_READ_AND_WRITE",
    "BASIC",
    "INTERMEDIARY",
    "HIGHER_EDUCATION",
]


def ind_uuid(seq: int) -> str:
    return f"{INDIVIDUAL_UUID_PREFIX}{seq:012d}"


def hh_uuid(seq: int) -> str:
    return f"{HOUSEHOLD_UUID_PREFIX}{seq:012d}"


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {k: (None if v == "" else v) for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def load_geo() -> dict:
    levels = _read_csv(GEO_DIR / "levels.csv")
    level_by_mnemonic = {lv["level_mnemonic"]: lv for lv in levels}

    country = _read_csv(GEO_DIR / "level-0-country.csv")[0]
    regions = _read_csv(GEO_DIR / "level-1-regions.csv")
    districts = _read_csv(GEO_DIR / "level-2-districts.csv")
    wards = _read_csv(GEO_DIR / "level-3-wards.csv")
    villages = _read_csv(GEO_DIR / "level-4-villages.csv")

    by_id = {}
    for entry in [country, *regions, *districts, *wards, *villages]:
        by_id[entry["level_value_id"]] = entry

    return {
        "level_by_mnemonic": level_by_mnemonic,
        "by_id": by_id,
        "villages": villages,
    }


def build_hierarchy(geo: dict, village_id: str) -> dict:
    """Walk parent chain from village to country, return ordered hierarchy."""
    by_id = geo["by_id"]
    levels = geo["level_by_mnemonic"]
    level_id_to_mnemonic = {lv["level_id"]: lv["level_mnemonic"] for lv in levels.values()}

    chain = []
    cur_id = village_id
    while cur_id is not None:
        node = by_id[cur_id]
        chain.append(node)
        cur_id = node["parent_level_value_id"]
    chain.reverse()

    hierarchy = []
    for node in chain:
        mnem = level_id_to_mnemonic[node["level_id"]]
        hierarchy.append(
            {
                "level": mnem,
                "level_value_id": node["level_value_id"],
                "level_value_mnemonic": node["level_value_mnemonic"],
            }
        )
    return {"hierarchy": hierarchy, "lowest_level_value_id": village_id}


def geo_ids_from_village(geo: dict, village_id: str) -> dict:
    """Return dict with country/region/district/ward/village IDs."""
    by_id = geo["by_id"]
    levels = geo["level_by_mnemonic"]
    level_id_to_mnemonic = {lv["level_id"]: lv["level_mnemonic"] for lv in levels.values()}

    result = {"geo_village_id": village_id}
    cur_id = village_id
    while cur_id is not None:
        node = by_id[cur_id]
        mnem = level_id_to_mnemonic[node["level_id"]]
        result[f"geo_{mnem}_id"] = node["level_value_id"]
        cur_id = node["parent_level_value_id"]
    return result


def random_dob(min_age: int, max_age: int) -> date:
    today = date.today()
    days_min = min_age * 365
    days_max = max_age * 365
    days = random.randint(days_min, days_max)
    return today - timedelta(days=days)


def age_from_dob(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def random_phone(seq: int) -> str:
    return f"+1{seq:010d}"


def mask_id(foundational_id: str) -> str:
    return "XXXXXX" + foundational_id[-4:]


def gen_individual(seq: int, geo: dict, village_id: str | None = None) -> dict:
    if village_id is None:
        village_id = random.choice(geo["villages"])["level_value_id"]

    gender = random.choice(["MALE", "FEMALE"])
    first_name = fake.first_name_male() if gender == "MALE" else fake.first_name_female()
    last_name = fake.last_name()
    middle_name = fake.first_name() if random.random() < 0.3 else None

    age_buckets = [
        (0, 4, 0.05),
        (5, 17, 0.15),
        (18, 24, 0.10),
        (25, 45, 0.40),
        (46, 60, 0.20),
        (61, 85, 0.10),
    ]
    r = random.random()
    cum = 0.0
    age_min, age_max = 25, 45
    for lo, hi, w in age_buckets:
        cum += w
        if r <= cum:
            age_min, age_max = lo, hi
            break
    dob = random_dob(age_min, age_max)
    age = age_from_dob(dob)

    if age < 18:
        marital = "SINGLE"
    elif age >= 60 and random.random() < 0.3:
        marital = "WIDOWED"
    else:
        marital = random.choices(MARITAL_STATUSES, weights=[0.2, 0.6, 0.1, 0.1])[0]

    foundational_id = f"{random.randint(1000000000, 9999999999)}"

    full_name_parts = [first_name]
    if middle_name:
        full_name_parts.append(middle_name)
    full_name_parts.append(last_name)
    full_name = " ".join(full_name_parts)

    village_node = geo["by_id"][village_id]
    base_lat = 10.0 + (hash(village_id) % 1000) / 100.0
    base_lon = 65.0 + (hash(village_id) % 700) / 100.0
    lat = round(base_lat + random.uniform(-0.05, 0.05), 4)
    lon = round(base_lon + random.uniform(-0.05, 0.05), 4)
    altitude = random.randint(50, 300)

    record = {
        "internal_record_id": ind_uuid(seq),
        "functional_record_id": f"IND-{seq:04d}",
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "full_name": full_name,
        "given_name": full_name,
        "gender": gender,
        "birth_date": dob.isoformat(),
        "estimated_age": age,
        "marital_status": marital,
        "phone_numbers": [
            {"type": "mobile", "number": random_phone(seq), "is_primary": True}
        ],
        "emails": fake.email() if age >= 18 and random.random() < 0.6 else None,
        "foundational_id": foundational_id,
        "foundational_id_masked": mask_id(foundational_id),
        "education_level": random.choice(EDUCATION_LEVELS) if age >= 6 else None,
        "language_code": "en",
        "image_file": f"images/IND-{seq:04d}.jpg",
    }
    record.update(geo_ids_from_village(geo, village_id))
    record["geo_hierarchy_json"] = build_hierarchy(geo, village_id)
    record.update(
        {
            "latitude": str(lat),
            "longitude": str(lon),
            "altitude": str(altitude),
            "plus_code": f"{int(lat * 10) % 100:02d}AB+{int(lon * 10) % 100:02d}",
            "address_line_1": fake.street_address(),
            "address_line_2": f"Sector {(seq % 9) + 1}",
            "postal_code": f"{seq % 1000000:06d}",
            "country_code": "KM",
        }
    )
    return record


def gen_household(seq: int, members: list[dict], geo: dict) -> dict:
    head = members[0]
    village_id = head["geo_village_id"]
    member_ids = [m["internal_record_id"] for m in members]

    n_female = sum(1 for m in members if m["gender"] == "FEMALE")
    n_male = sum(1 for m in members if m["gender"] == "MALE")
    ages = [m["estimated_age"] for m in members]
    adults = sum(1 for a in ages if a >= 18)
    children_u5 = sum(1 for a in ages if a < 5)
    school_age = sum(1 for a in ages if 5 <= a < 18)
    elderly = sum(1 for a in ages if a >= 60)

    headship = "MALE_HEADED" if head["gender"] == "MALE" else "FEMALE_HEADED"

    record = {
        "internal_record_id": hh_uuid(seq),
        "functional_record_id": f"HH-{seq:04d}",
        "head_individual_id": head["internal_record_id"],
        "head_name": head["full_name"],
        "headship_type": headship,
        "member_ids": member_ids,
        "size_total": len(members),
        "size_adults": adults,
        "size_children_u5": children_u5,
        "size_school_age": school_age,
        "size_elderly": elderly,
        "number_of_female_members": n_female,
        "number_of_male_members": n_male,
    }
    record.update(geo_ids_from_village(geo, village_id))
    record["geo_hierarchy_json"] = build_hierarchy(geo, village_id)
    record.update(
        {
            "latitude": head["latitude"],
            "longitude": head["longitude"],
            "altitude": head["altitude"],
            "plus_code": head["plus_code"],
            "address_line_1": head["address_line_1"],
            "address_line_2": head["address_line_2"],
            "postal_code": head["postal_code"],
            "country_code": head["country_code"],
        }
    )
    return record


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    geo = load_geo()

    individuals: list[dict] = []
    next_ind_seq = 1

    households: list[dict] = []

    target_in_households = 300
    assigned_to_households = 0

    for hh_seq in range(1, NUM_HOUSEHOLDS + 1):
        village_id = random.choice(geo["villages"])["level_value_id"]
        size = random.randint(3, 7)
        if assigned_to_households + size > target_in_households:
            size = max(3, target_in_households - assigned_to_households)
            if size < 3:
                break

        head = gen_individual(next_ind_seq, geo, village_id)
        if head["estimated_age"] < 25:
            head = gen_individual(next_ind_seq, geo, village_id)
        individuals.append(head)
        next_ind_seq += 1
        hh_members = [head]

        spouse_gender_pref = "FEMALE" if head["gender"] == "MALE" else "MALE"
        spouse = gen_individual(next_ind_seq, geo, village_id)
        spouse["gender"] = spouse_gender_pref
        spouse["last_name"] = head["last_name"]
        spouse["full_name"] = " ".join(
            [spouse["first_name"]]
            + ([spouse["middle_name"]] if spouse["middle_name"] else [])
            + [spouse["last_name"]]
        )
        spouse["given_name"] = spouse["full_name"]
        spouse["marital_status"] = "MARRIED"
        head["marital_status"] = "MARRIED"
        individuals.append(spouse)
        next_ind_seq += 1
        hh_members.append(spouse)

        for _ in range(size - 2):
            child = gen_individual(next_ind_seq, geo, village_id)
            child["last_name"] = head["last_name"]
            child["full_name"] = " ".join(
                [child["first_name"]]
                + ([child["middle_name"]] if child["middle_name"] else [])
                + [child["last_name"]]
            )
            child["given_name"] = child["full_name"]
            individuals.append(child)
            next_ind_seq += 1
            hh_members.append(child)

        households.append(gen_household(hh_seq, hh_members, geo))
        assigned_to_households += len(hh_members)

    while next_ind_seq <= NUM_INDIVIDUALS:
        individuals.append(gen_individual(next_ind_seq, geo))
        next_ind_seq += 1

    individuals = individuals[:NUM_INDIVIDUALS]

    individual_columns = [
        "internal_record_id", "functional_record_id",
        "first_name", "middle_name", "last_name", "full_name", "given_name",
        "gender", "birth_date", "estimated_age", "marital_status",
        "phone_numbers", "emails",
        "foundational_id", "foundational_id_masked",
        "education_level", "language_code", "image_file",
        "geo_village_id", "geo_ward_id", "geo_district_id",
        "geo_region_id", "geo_country_id", "geo_hierarchy_json",
        "latitude", "longitude", "altitude", "plus_code",
        "address_line_1", "address_line_2", "postal_code", "country_code",
    ]
    household_columns = [
        "internal_record_id", "functional_record_id",
        "head_individual_id", "head_name", "headship_type", "member_ids",
        "size_total", "size_adults", "size_children_u5",
        "size_school_age", "size_elderly",
        "number_of_female_members", "number_of_male_members",
        "geo_village_id", "geo_ward_id", "geo_district_id",
        "geo_region_id", "geo_country_id", "geo_hierarchy_json",
        "latitude", "longitude", "altitude", "plus_code",
        "address_line_1", "address_line_2", "postal_code", "country_code",
    ]

    write_csv(OUT_DIR / "individuals.csv", individual_columns, individuals)
    write_csv(OUT_DIR / "households.csv", household_columns, households)
    print(f"Wrote individuals.csv: {len(individuals)} records")
    print(f"Wrote households.csv: {len(households)} records")
    print(f"  Individuals in households: {assigned_to_households}")
    print(f"  Unattached individuals:    {len(individuals) - assigned_to_households}")


if __name__ == "__main__":
    main()
