"""Generate demography/individuals.csv + households.csv from geo/geo.csv.

Reads the single flat, human-readable geo CSV (one row per village, with
country/region/district/ward names denormalized) and emits the geo location as
plain human-readable name columns (country, region, district, ward, village).
No ids, no path strings, no hierarchy JSON — the registry/master-data loaders
derive whatever internal keys they need from these names at seed time.
Each individual carries a household_id (the household it belongs to, blank for
unattached individuals); households no longer carry member_ids.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from _csv_utils import write_csv

REPO_ROOT = Path(__file__).resolve().parent.parent
GEO_DIR = REPO_ROOT / "geo"
GEO_FILE = GEO_DIR / "geo.csv"
OUT_DIR = REPO_ROOT / "demography"

# Ordered geo levels, matching the columns of geo/geo.csv (root -> leaf).
GEO_LEVELS = ["country", "region", "district", "ward", "village"]


def _geo_key(village: dict) -> str:
    """Stable string key for a village (used only for deterministic lat/long)."""
    return "/".join(village[level] for level in GEO_LEVELS)


NUM_INDIVIDUALS = 500
NUM_HOUSEHOLDS = 100

SEED = 42
random.seed(SEED)
Faker.seed(SEED)
fake = Faker("en_US")

INDIVIDUAL_UUID_PREFIX = "i"
HOUSEHOLD_UUID_PREFIX = "h"

MARITAL_STATUSES = ["SINGLE", "MARRIED", "WIDOWED", "DIVORCED"]
EDUCATION_LEVELS = [
    "ILLITERATE",
    "CAN_READ_AND_WRITE",
    "BASIC",
    "INTERMEDIARY",
    "HIGHER_EDUCATION",
]


def ind_uuid(seq: int) -> str:
    return f"{INDIVIDUAL_UUID_PREFIX}{seq:04d}"


def hh_uuid(seq: int) -> str:
    return f"{HOUSEHOLD_UUID_PREFIX}{seq:03d}"


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {k: (None if v == "" else v) for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def load_geo() -> dict:
    """Read the flat geo/geo.csv into a list of village name-rows.

    Each row is one village denormalized with its parent names
    (country, region, district, ward, village). Villages are the rows
    themselves; no ids or hierarchy are derived here."""
    villages = [
        {level: row[level] for level in GEO_LEVELS}
        for row in _read_csv(GEO_FILE)
    ]
    return {"villages": villages}


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


def gen_individual(seq: int, geo: dict, village: dict | None = None) -> dict:
    if village is None:
        village = random.choice(geo["villages"])

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

    village_key = _geo_key(village)
    base_lat = 10.0 + (hash(village_key) % 1000) / 100.0
    base_lon = 65.0 + (hash(village_key) % 700) / 100.0
    lat = round(base_lat + random.uniform(-0.05, 0.05), 4)
    lon = round(base_lon + random.uniform(-0.05, 0.05), 4)
    altitude = random.randint(50, 300)

    record = {
        "internal_record_id": ind_uuid(seq),
        "functional_record_id": f"IND-{seq:04d}",
        "household_id": None,
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
    record.update({level: village[level] for level in GEO_LEVELS})
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
        "size_total": len(members),
        "size_adults": adults,
        "size_children_u5": children_u5,
        "size_school_age": school_age,
        "size_elderly": elderly,
        "number_of_female_members": n_female,
        "number_of_male_members": n_male,
    }
    record.update({level: head[level] for level in GEO_LEVELS})
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
        village = random.choice(geo["villages"])
        size = random.randint(3, 7)
        if assigned_to_households + size > target_in_households:
            size = max(3, target_in_households - assigned_to_households)
            if size < 3:
                break

        head = gen_individual(next_ind_seq, geo, village)
        if head["estimated_age"] < 25:
            head = gen_individual(next_ind_seq, geo, village)
        individuals.append(head)
        next_ind_seq += 1
        hh_members = [head]

        spouse_gender_pref = "FEMALE" if head["gender"] == "MALE" else "MALE"
        spouse = gen_individual(next_ind_seq, geo, village)
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
            child = gen_individual(next_ind_seq, geo, village)
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

        hh_id = hh_uuid(hh_seq)
        for m in hh_members:
            m["household_id"] = hh_id
        households.append(gen_household(hh_seq, hh_members, geo))
        assigned_to_households += len(hh_members)

    while next_ind_seq <= NUM_INDIVIDUALS:
        individuals.append(gen_individual(next_ind_seq, geo))
        next_ind_seq += 1

    individuals = individuals[:NUM_INDIVIDUALS]

    individual_columns = [
        "internal_record_id", "functional_record_id", "household_id",
        "first_name", "middle_name", "last_name", "full_name", "given_name",
        "gender", "birth_date", "estimated_age", "marital_status",
        "phone_numbers", "emails",
        "foundational_id", "foundational_id_masked",
        "education_level", "language_code", "image_file",
        "country", "region", "district", "ward", "village",
        "latitude", "longitude", "altitude", "plus_code",
        "address_line_1", "address_line_2", "postal_code", "country_code",
    ]
    household_columns = [
        "internal_record_id", "functional_record_id",
        "head_individual_id", "head_name", "headship_type",
        "size_total", "size_adults", "size_children_u5",
        "size_school_age", "size_elderly",
        "number_of_female_members", "number_of_male_members",
        "country", "region", "district", "ward", "village",
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
