"""Fake (but realistic-looking) leads for demos and the SQL phase.

`python -m app.cli fake-data --leads N` calls `insert_fake_leads()`. The leads
look like they already went through cleaning and review: normalized phones,
a mix of statuses and scores, `first_seen_at` spread over the last 90 days,
and website signals for leads that have a website. All use source `fake`.

Rows are inserted in batches, so tens of thousands of leads take seconds.
Pass `seed` to get the same leads every time (and only into an empty table:
the same seed makes the same `source_key`s, which must be unique).
"""

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
AREAS = ["Downtown", "North", "South", "East", "West", "Midtown"]
CATEGORIES = ["dentist", "restaurant", "gym", "real estate agent", "plumber", "lawyer"]
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
    name = fake.company()
    status = rnd.choices(list(STATUS_WEIGHTS), weights=list(STATUS_WEIGHTS.values()))[0]
    ref = fake.uuid4()

    has_website = rnd.random() < 0.7
    domain = f"{fake.domain_word()}-{ref[:6]}.com" if has_website else None
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
        "phone": f"+1{area_code}{fake.numerify('#######')}" if rnd.random() < 0.85 else None,
        "email": f"info@{domain}" if domain and rnd.random() < 0.6 else None,
        "website": f"https://{domain}" if domain else None,
        "website_domain": domain,
        "address": fake.street_address(),
        "area": rnd.choice(AREAS),
        "city": city,
        "country": "US",
        "lat": float(fake.coordinate(center=lat, radius=0.1)),
        "lng": float(fake.coordinate(center=lng, radius=0.1)),
        "category": rnd.choice(CATEGORIES),
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
