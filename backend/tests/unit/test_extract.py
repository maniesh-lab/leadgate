"""Tests for cleaning + extraction pieces: `clean_lead`, the traceability checks, the baseline.

The whole `extract` node is covered by tests/unit/test_graph.py; these check the
rules one by one.
"""

from app.agents.categories import (
    CATEGORIES,
    NEIGHBOURS,
    SECTORS,
    campaign_category,
    category_matches,
    niche_category,
    prompt_categories,
)
from app.agents.cleaning.lead import (
    campaign_geo,
    clean_lead,
    find_value,
    mentions,
    source_spelling,
    source_text,
    values_in,
)
from app.agents.llm import scripted_llm
from app.agents.nodes.extract import (
    LLMFields,
    baseline_fields,
    build_prompt,
    extract_lead,
    founding_year_in,
    trace_validators,
)
from app.db.models import Campaign
from app.sources.osm import NICHE_ALIASES, NICHE_TAGS

RAW = {
    "source": "osm",
    "name": "  Smile   Austin Dental ",
    "phone": "(512) 555-0142",
    "email": "Smile@Example.test",
    "website": "https://www.smileaustin.test",
    "address": None,
    "category": "dentist",
    "rating": None,
    "review_count": None,
    "business_status": None,
    "data": {"tags": {"addr:city": "Austin", "addr:street": "South Congress Avenue"}},
}
ENRICHMENT = {
    "emails": ["info@smileaustin.test", "smile@example.test"],
    "phones": ["512.555.0142", "+1 512 555 0199", "78745-1234"],
    "text": "Teeth whitening and dental implants. Established in 1998.",
}
GEO = {"location": "Austin, TX", "country_code": "US", "place": "Austin, Texas, United States"}
YEAR = 2026


def cleaned() -> dict:
    return clean_lead(RAW, ENRICHMENT, "US")


def problems(**fields) -> list[str]:
    checks = trace_validators(cleaned(), source_text(RAW, GEO), ENRICHMENT["text"], YEAR)
    answer = LLMFields(**({"category": "dentist"} | fields))
    return [problem for check in checks if (problem := check(answer))]


def test_clean_lead_tidies_and_normalizes() -> None:
    result = cleaned()
    assert result["name"] == "Smile Austin Dental"
    assert result["phone"] == "+15125550142"  # the source's phone comes first
    assert result["phones"] == ["+15125550142", "+15125550199"]  # the ZIP+4 is dropped
    # Emails: the source's first, lowercased, no repeats.
    assert result["emails"] == ["smile@example.test", "info@smileaustin.test"]
    assert result["category_guess"] == "dentist"


def test_clean_lead_takes_the_website_phone_when_the_source_has_none() -> None:
    result = clean_lead(RAW | {"phone": None}, ENRICHMENT, "US")
    assert result["phone"] == "+15125550142"


def test_a_traceable_answer_passes() -> None:
    assert (
        problems(
            email="info@smileaustin.test",
            city="Austin",
            country="United States",
            services=["teeth whitening", "Dental Implants"],
            established_year=1998,
        )
        == []
    )


def test_an_email_that_is_not_a_candidate_is_refused() -> None:
    assert "not one of the CANDIDATE EMAILS" in problems(email="owner@gmail.test")[0]


def test_source_spelling_completes_a_word_one_letter_short() -> None:
    text = "Kailash Marg, Kathmandu, Bagmati Province 44600, Nepal | United States"
    assert source_spelling(text, "Nepa") == "Nepal"
    assert source_spelling(text, " Nepal") == "Nepal"
    assert source_spelling(text, "NEPAL") == "Nepal"
    assert source_spelling(text, "nepa") == "Nepal"
    assert source_spelling(text, "United State") == "United States"
    assert source_spelling(text, "Kathmandu") == "Kathmandu"


def test_source_spelling_refuses_what_the_source_does_not_have() -> None:
    text = "Kathmandu, Nepal"
    assert source_spelling(text, "India") is None
    assert source_spelling(text, "Nep") is None  # too short to complete
    assert source_spelling(text, "Kathman") is None  # more than one letter short
    assert source_spelling(text, "   ") is None


def test_a_country_one_letter_short_is_stored_as_the_source_spells_it() -> None:
    raw = RAW | {"address": "Kailash Marg, Kathmandu, Nepal", "data": {}}
    geo = {"location": "Kathmandu, Nepal", "country_code": None, "place": None}
    reply = LLMFields(city="Kathmandu", country="Nepa", category="dentist").model_dump_json()
    llm, _ = scripted_llm([reply])

    result = extract_lead(llm, niche="dentist", raw=raw, enrichment=ENRICHMENT, geo=geo, year=YEAR)

    assert result["extraction_ok"] is True
    assert result["extracted"]["country"] == "Nepal"


def test_a_bad_country_is_left_blank_without_failing_the_lead() -> None:
    raw = RAW | {"address": "Kailash Marg, Kathmandu, Nepal", "data": {}}
    geo = {"location": "Kathmandu, Nepal", "country_code": None, "place": None}
    reply = LLMFields(city="Kathmandu", country="N", category="dentist").model_dump_json()
    llm, _ = scripted_llm([reply])

    result = extract_lead(llm, niche="dentist", raw=raw, enrichment=ENRICHMENT, geo=geo, year=YEAR)

    assert result["extraction_ok"] is True
    assert result["extracted"]["country"] is None
    assert result["extracted"]["city"] == "Kathmandu"


def test_a_made_up_place_is_refused() -> None:
    found = problems(city="Dallas", area="South Congress")
    assert len(found) == 1
    assert "city 'Dallas'" in found[0] and "area" not in found[0]  # the street names it


def test_a_category_outside_the_list_is_refused() -> None:
    assert "not in the list" in problems(category="orthodontist")[0]


def test_invented_services_and_too_many_services_are_refused() -> None:
    assert "'teeth cleaning'" in problems(services=["teeth cleaning"])[0]
    assert "at most 10" in problems(services=["teeth whitening"] * 11)[0]


def test_a_year_must_be_on_the_site_and_plausible() -> None:
    assert "1999" in problems(established_year=1999)[0]
    assert problems(established_year=2999) != []


def test_baseline_picks_only_what_is_plainly_there() -> None:
    fields = baseline_fields(cleaned(), RAW)
    assert fields == LLMFields(email="smile@example.test", city="Austin", category="dentist")
    assert problems(**fields.model_dump()) == []


def test_baseline_without_a_category_hint_is_other() -> None:
    raw = RAW | {"category": "pottery"}
    assert baseline_fields(clean_lead(raw, ENRICHMENT, "US"), raw).category == "other"


def test_prompt_lists_the_candidates_and_the_categories() -> None:
    prompt = build_prompt("dentist", RAW, GEO, cleaned(), ENRICHMENT["text"])
    assert "CANDIDATE EMAILS: smile@example.test, info@smileaustin.test" in prompt
    assert "dentist, doctor, clinic" in prompt
    assert "Teeth whitening" in prompt


def test_prompt_keeps_sales_wording_out_of_services() -> None:
    prompt = build_prompt("dentist", RAW, GEO, cleaned(), ENRICHMENT["text"])
    assert "never payment, financing, insurance" in prompt


def test_prompt_drops_cloudflares_hidden_email() -> None:
    text = "Write to us: [email\xa0protected] or call. [Email Protected]"
    prompt = build_prompt("dentist", RAW, GEO, cleaned(), text)
    assert "protected" not in prompt.casefold()
    assert "Write to us:" in prompt


def test_prompt_without_website_text() -> None:
    assert "(no website text)" in build_prompt("dentist", RAW, GEO, cleaned(), "")


def test_mentions_matches_whole_words_only() -> None:
    assert mentions("Austin, Travis County", "travis  county")
    assert not mentions("Austinville", "Austin")
    assert not mentions("anything", "   ")


def test_values_in_and_find_value_look_inside_nested_data() -> None:
    data = {"a": [1, {"b": "two"}], "flag": True, "none": None}
    assert values_in(data) == ["1", "two"]
    assert find_value(RAW["data"], ("addr:city",)) == "Austin"
    assert find_value("not a dict", ("city",)) is None


def test_campaign_geo_reads_the_osm_geocode_or_an_explicit_country() -> None:
    osm = Campaign(location="Austin, TX", source_options={"osm_area": {"country_code": "us"}})
    csv = Campaign(location="Kathmandu", source_options={"country": "np"})
    bare = Campaign(location="Somewhere", source_options={})
    assert campaign_geo(osm)["country_code"] == "US"
    assert campaign_geo(csv)["country_code"] == "NP"
    assert campaign_geo(bare) == {"location": "Somewhere", "country_code": None, "place": None}


def test_niche_categories_and_aliases() -> None:
    assert niche_category("Real_Estate") == "real estate agent"
    assert niche_category("vet") == "veterinarian"
    assert niche_category(None) is None
    assert campaign_category("web design") == "other"


def test_plural_niches_and_new_aliases_find_their_category() -> None:
    assert niche_category("Dermatologists") == "dermatologist"
    assert niche_category("skin_care_clinic") == "dermatologist"
    assert niche_category("barbers") == "hair salon"
    assert niche_category("medical_clinic") == "clinic"


def test_every_alias_points_at_a_real_category() -> None:
    assert set(NICHE_ALIASES.values()) <= set(NICHE_TAGS)
    assert not set(NICHE_ALIASES) & set(NICHE_TAGS)


def test_specialists_and_general_doctors_match_but_specialists_differ() -> None:
    assert category_matches("dermatologist", "dermatologist")
    assert category_matches("dermatologist", "doctor")
    assert category_matches("dermatologist", "clinic")
    assert category_matches("doctor", "dermatologist")
    assert category_matches("clinic", "doctor")
    assert not category_matches("dermatologist", "cardiologist")
    assert not category_matches("dermatologist", "beauty salon")
    assert not category_matches("dentist", "doctor")
    assert category_matches("other", "beauty salon")


def test_close_neighbours_match_but_other_businesses_dont() -> None:
    assert category_matches("hotel", "guest house")
    assert category_matches("guest house", "hotel")
    assert category_matches("restaurant", "cafe")
    assert category_matches("hair salon", "beauty salon")
    assert category_matches("beauty salon", "spa")
    assert not category_matches("hair salon", "spa")  # neighbours of a neighbour don't count
    assert not category_matches("restaurant", "bar")
    assert not category_matches("dermatologist", "beauty salon")  # still a reviewer's call


def test_every_category_is_in_exactly_one_sector() -> None:
    words = [word for sector in SECTORS for word in sector]
    assert sorted(words) == sorted(NICHE_TAGS)
    assert set().union(*NEIGHBOURS) <= set(NICHE_TAGS)


def test_the_prompt_offers_the_campaigns_sector_and_the_sources_hint() -> None:
    dentist = prompt_categories("dentist", None)
    assert {"dentist", "doctor", "cardiologist", "pharmacy", "other"} <= set(dentist)
    assert "plumber" not in dentist and "beauty salon" not in dentist
    assert "beauty salon" in prompt_categories("dermatologist", "beauty salon")
    assert prompt_categories("other", None) == CATEGORIES  # a niche off the list: all of them
    prompt = build_prompt("dentist", RAW, GEO, cleaned(), ENRICHMENT["text"])
    assert "plumber" not in prompt


def test_a_copyright_year_is_not_a_founding_year() -> None:
    assert founding_year_in("Creating smiles since 1991. © 2024 Kunik", 1991)
    assert founding_year_in("Dr. Kunik opened his practice in 1991", 1991)
    assert not founding_year_in("Same day treatment © 2011 Tech Ridge Dental", 2011)
    assert not founding_year_in("Established in 19981", 1998)


def test_the_year_check_refuses_a_copyright_year() -> None:
    checks = trace_validators(cleaned(), "", "Open daily. © 2011 Smile Dental", YEAR)
    answer = LLMFields(category="dentist", established_year=2011)
    assert any("copyright year does not count" in (check(answer) or "") for check in checks)
