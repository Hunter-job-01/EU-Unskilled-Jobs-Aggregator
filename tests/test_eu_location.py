from src.eu_location import (EU27, classify_location, normalize_country,
                             QUARANTINE_STATUSES)


def test_allowlist_is_exactly_eu27():
    assert len(EU27) == 27
    assert "GB" not in EU27 and "CH" not in EU27 and "NO" not in EU27


def test_normalize_codes_and_aliases():
    assert normalize_country("DE") == "DE"
    assert normalize_country("deu") == "DE"
    assert normalize_country("SWE") == "SE"
    assert normalize_country("Deutschland") == "DE"
    assert normalize_country("Německo") == "DE"
    assert normalize_country("Sverige") == "SE"
    assert normalize_country("Polska") == "PL"
    assert normalize_country("UK") == "GB"
    assert normalize_country("Switzerland") == "CH"
    assert normalize_country("Atlantis") is None
    assert normalize_country("") is None


def test_structured_country_outranks_text():
    v = classify_location(structured_country="DEU", location_text="Berlin")
    assert v.status == "eu" and v.country == "DE"


def test_non_eu_rejected_even_on_european_board():
    assert classify_location(structured_country="GB").status == "non_eu"
    assert classify_location(location_text="Zurich, Switzerland").status == "non_eu"
    assert classify_location(location_text="Oslo, Norway").status == "non_eu"
    assert classify_location(location_text="Cairo, Egypt").status == "non_eu"


def test_contradictory_evidence_quarantined():
    v = classify_location(structured_country="DE", location_text="Warsaw, Poland")
    assert v.status == "contradictory" and v.quarantined


def test_multiple_distinct_countries_quarantined():
    v = classify_location(location_text="Munich, Germany / Zurich, Switzerland")
    assert v.status == "contradictory"


def test_search_country_is_a_lead_not_proof():
    v = classify_location(location_text="", search_country="PL")
    assert v.status == "unknown" and v.quarantined
    assert any("lead" in e for e in v.evidence)


def test_remote_needs_explicit_eu_country():
    assert classify_location(location_text="Remote", remote=True).status == "remote_unverified"
    assert classify_location(location_text="Remote - Europe", remote=True).status == "remote_unverified"
    v = classify_location(location_text="Remote, Germany", remote=True)
    assert v.status == "eu" and v.country == "DE"


def test_vague_labels_never_admit():
    for label in ("Europe", "EMEA", "Worldwide", "European Union"):
        assert classify_location(location_text=label).status in QUARANTINE_STATUSES


def test_city_country_text_resolves():
    v = classify_location(location_text="Brno, Czech Republic")
    assert v.status == "eu" and v.country == "CZ"
    v = classify_location(location_text="Riga, Latvija")
    assert v.status == "eu" and v.country == "LV"
