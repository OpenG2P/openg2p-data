"""Generate users/users.csv from a subset of household heads."""

import csv
import random
from pathlib import Path

from _csv_utils import write_csv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demography"
OUT_DIR = REPO_ROOT / "users"

NUM_USERS = 20
SEED = 7
random.seed(SEED)

DP_ROLES_POOL = [
    "DP_social_protection",
    "DP_health",
    "DP_education",
    "DP_disaster_response",
    "DP_food_security",
]

ROLE_POOL = ["registry_user", "registry_admin", "registry_approver"]


def username_from(first: str, last: str, idx: int) -> str:
    base = (first[:1] + last).lower()
    base = "".join(c for c in base if c.isalnum()) or f"user{idx}"
    return base


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {k: (None if v == "" else v) for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    households = _read_csv(DEMO_DIR / "households.csv")
    individuals_by_id = {
        i["internal_record_id"]: i for i in _read_csv(DEMO_DIR / "individuals.csv")
    }

    heads = [individuals_by_id[h["head_individual_id"]] for h in households]
    heads = heads[:NUM_USERS]

    users = []
    used_usernames: set[str] = set()
    for idx, ind in enumerate(heads, start=1):
        uname = username_from(ind["first_name"], ind["last_name"], idx)
        suffix = 0
        while uname in used_usernames:
            suffix += 1
            uname = f"{uname}{suffix}"
        used_usernames.add(uname)

        roles = ["registry_user"]
        if idx <= 3:
            roles.append("registry_admin")
        elif idx <= 6:
            roles.append("registry_approver")

        dp_roles = random.sample(DP_ROLES_POOL, k=random.randint(1, 3))

        users.append(
            {
                "username": uname,
                "email": ind["emails"] or f"{uname}@example.org",
                "first_name": ind["first_name"],
                "last_name": ind["last_name"],
                "individual_id": ind["internal_record_id"],
                "foundational_id": ind["foundational_id"],
                "roles": roles,
                "dp_roles": dp_roles,
            }
        )

    columns = [
        "username", "email", "first_name", "last_name",
        "individual_id", "foundational_id", "roles", "dp_roles",
    ]
    write_csv(OUT_DIR / "users.csv", columns, users)
    print(f"Wrote users.csv: {len(users)} users")


if __name__ == "__main__":
    main()
