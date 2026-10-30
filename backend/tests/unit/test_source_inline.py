"""Tests for the `n8n` source (leads sent in the webhook body)."""

from app.sources.csv_mapped import row_hash
from app.sources.inline import InlineSource


def test_leads_become_raw_leads_like_csv_rows() -> None:
    source = InlineSource(
        [
            {"name": "Smile Austin Dental", "phone": 5125550142, "rating": 4.5, "note": "vip"},
            {"name": "  Barton Creek  ", "category": "dentist", "review_count": "1,024"},
        ]
    )

    smile, barton = list(source.collect())

    assert (smile.source, smile.phone, smile.rating) == ("n8n", "5125550142", 4.5)
    assert smile.source_ref == row_hash("Smile Austin Dental", "5125550142", None)
    assert smile.raw["note"] == "vip"  # extra keys are kept in raw
    assert (barton.name, barton.category_hint, barton.review_count) == (
        "Barton Creek",
        "dentist",
        1024,
    )
    assert source.skipped == 0


def test_bad_leads_are_skipped_and_counted() -> None:
    source = InlineSource([{"phone": "512"}, {}, {"name": "Ok", "rating": 9}, {"name": "Fine"}])

    assert [lead.name for lead in source.collect()] == ["Fine"]
    assert source.skipped == 3
