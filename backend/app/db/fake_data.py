"""Fake (but realistic-looking) leads for demos and the SQL phase.

`python -m app.cli fake-data --leads N` calls `insert_fake_leads()`. The leads
look like they already went through cleaning and review: normalized phones,
a mix of statuses and scores, `first_seen_at` spread over the last 90 days,
and website signals for leads that have a website. All use source `fake`.
They're dentists, plumbers and gyms in four Texas cities, named to fit the trade
("Hyde Park Smiles", "Novak Plumbing"), with `.example` websites and 555-01xx
phones, so none of them can point at a real business.

Rows are inserted in batches, so tens of thousands of leads take seconds.
Pass `seed` to get the same leads every time (and only into an empty table:
the same seed makes the same `source_key`s, which must be unique).
"""

import re
from datetime import UTC
from typing import Any

from faker import Faker
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.db.models import Lead, LeadStatus

BATCH_SIZE = 1000

# city -> (lat, lng, phone area code)
CITIES = {
    "Austin": (30.27, -97.74, "512"),
    "Dallas": (32.78, -96.80, "214"),
    "Houston": (29.76, -95.37, "713"),
    "San Antonio": (29.42, -98.49, "210"),
}
# city -> its neighbourhoods (a lead's `area`, and part of some business names). Austin's
# leave out the ones the demo's own leads are named after (`demo/austin-dentists.csv`), so
# the demo's `check` never takes a fake lead for a duplicate.
AREAS = {
    "Austin": ["Travis Heights", "Bouldin", "Tarrytown", "Clarksville", "Rosedale", "Windsor Park"],
    "Dallas": ["Uptown", "Deep Ellum", "Oak Lawn", "Lakewood", "Bishop Arts", "Preston Hollow"],
    "Houston": ["Montrose", "The Heights", "Midtown", "River Oaks", "Rice Village", "EaDo"],
    "San Antonio": [
        "Alamo Heights",
        "Southtown",
        "Stone Oak",
        "Tobin Hill",
        "Monte Vista",
        "King William",
    ],
}
# How often each trade shows up (weights): mostly dentists, like the demo's own campaign.
CATEGORY_WEIGHTS = {"dentist": 6, "plumber": 2, "gym": 2}
CATEGORIES = list(CATEGORY_WEIGHTS)
# Business names that fit the trade: {surname}, {surname2} and {area} are filled in.
NAME_PATTERNS = {
    "dentist": [
        "{surname} Family Dental",
        "{area} Dental Care",
        "{surname} Dental Arts",
        "{area} Smiles",
        "{surname} & {surname2} Dentistry",
        "{area} Family Dentistry",
    ],
    "plumber": [
        "{surname} Plumbing",
        "{area} Plumbing & Drain",
        "{surname} & Sons Plumbing",
        "{surname} Pipe Works",
        "{area} Water Heater Repair",
    ],
    "gym": [
        "{area} Fitness",
        "{surname} Strength & Conditioning",
        "{area} Boxing Club",
        "{surname} Yoga Studio",
        "{area} CrossFit",
    ],
}
# How often each status shows up (weights), roughly what a real queue looks like.
STATUS_WEIGHTS = {
    LeadStatus.NEW: 40,
    LeadStatus.IN_REVIEW: 20,
    LeadStatus.APPROVED: 20,
    LeadStatus.REJECTED: 15,
    LeadStatus.ESCALATED: 5,
}


def fake_lead(fake: Faker) -> dict[str, Any]:
    rnd = fake.random
    city = rnd.choice(list(CITIES))
    lat, lng, area_code = CITIES[city]
    area = rnd.choice(AREAS[city])
    category = rnd.choices(CATEGORIES, weights=list(CATEGORY_WEIGHTS.values()))[0]
    name = rnd.choice(NAME_PATTERNS[category]).format(
        surname=fake.last_name(), surname2=fake.last_name(), area=area
    )
    status = rnd.choices(list(STATUS_WEIGHTS), weights=list(STATUS_WEIGHTS.values()))[0]
    ref = fake.uuid4()

    has_website = rnd.random() < 0.7
    # .example is reserved (RFC 2606): a fake website never points at a real business.
    domain = f"{re.sub(r'[^a-z0-9]+', '', name.lower())}.example" if has_website else None
    enrichment = None
    if has_website:
        https, mobile = rnd.random() < 0.8, rnd.random() < 0.7
        enrichment = {
            "signals": {
                "https": https,
                "mobile_viewport": mobile,
                "https_and_mobile": https and mobile,
                "copyright_year": rnd.randint(2012, 2026),
            }
        }

    return {
        "source": "fake",
        "source_ref": ref,
        "source_key": f"fake:{ref}",
        "raw": {"generator": "faker"},
        "name": name,
        # 555-0100 to 555-0199 are set aside for fiction: never a real business's number.
        # The demo's own leads use 555-0101 to 555-0112, so these stay above them.
        "phone": f"+1{area_code}5550{rnd.randint(150, 199)}" if rnd.random() < 0.85 else None,
        "email": f"info@{domain}" if domain and rnd.random() < 0.6 else None,
        "website": f"https://{domain}" if domain else None,
        "website_domain": domain,
        "address": fake.street_address(),
        "area": area,
        "city": city,
        "country": "US",
        "lat": float(fake.coordinate(center=lat, radius=0.1)),
        "lng": float(fake.coordinate(center=lng, radius=0.1)),
        "category": category,
        "services": [],
        "rating": round(rnd.uniform(2.5, 5.0), 1) if rnd.random() < 0.8 else None,
        "review_count": rnd.randint(0, 500),
        "business_status": "operational" if rnd.random() < 0.95 else "closed_permanently",
        "enrichment": enrichment,
        "enriched_at": fake.date_time_between(start_date="-30d", tzinfo=UTC) if domain else None,
        "score": rnd.randint(0, 100) if status != LeadStatus.NEW else None,
        "status": status,
        "first_seen_at": fake.date_time_between(start_date="-90d", tzinfo=UTC),
    }


def insert_fake_leads(session: Session, count: int, seed: int | None = None) -> int:
    fake = Faker("en_US")
    if seed is not None:
        fake.seed_instance(seed)

    inserted = 0
    while inserted < count:
        batch = [fake_lead(fake) for _ in range(min(BATCH_SIZE, count - inserted))]
        session.execute(insert(Lead), batch)
        inserted += len(batch)
    session.commit()
    return inserted
