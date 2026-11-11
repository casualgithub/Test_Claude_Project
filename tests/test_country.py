import pytest

from party_er.country import CountryDetector, CountryResult, normalize_country_code, reconcile_country


@pytest.fixture(scope="module")
def detector():
    return CountryDetector()


@pytest.mark.parametrize("address, expected", [
    ("12 Baker Street, London W1U 3BW, United Kingdom", "GB"),
    ("12 Baker St, London W1U 3BW", "GB"),                       # postcode + city
    ("350 5th Ave, New York NY 10118", "US"),                     # state + ZIP
    ("500 Market St, San Francisco, CA 94105", "US"),             # CA = California here
    ("Austin, TX", "US"),
    ("6985 Financial Drive, Mississauga, ON L5N 0A1", "CA"),      # Canadian postcode
    ("Werner-von-Siemens-Straße 1, 80333 München, Deutschland", "DE"),
    ("Berlin, DE", "DE"),
    ("TCS House, Raveline Street, Fort, Mumbai 400001, Maharashtra", "IN"),
    ("〒471-8571 愛知県豊田市トヨタ町1番地", "JP"),                 # script + 〒 mark
    ("Seoul 서울특별시 강남구", "KR"),
    ("Level 10, 1 Martin Place, Sydney NSW 2000", "AU"),
    ("SAUN Quadra 5, Lote B, Brasília - DF, 70040-912", "BR"),
    ("1 Raffles Place, #20-01, Singapore 048616", "SG"),
    ("Office 1204, Business Bay, Dubai, UAE", "AE"),
    ("15 rue de Rivoli 75001 Paris", "FR"),
    ("Kalverstraat 1, 1012 NX Amsterdam", "NL"),
    ("Via Roma 1, 00100 Roma RM, ITA", "IT"),                     # trailing ISO3
    ("14 Independence Avenue, Windhoek, Namibia", "NA"),
    ("Tbilisi, Georgia", "GE"),
])
def test_detects_country(detector, address, expected):
    assert detector.detect(address).country_code == expected


@pytest.mark.parametrize("address", [
    "Canada Square, London E14 5AB",          # street named after a country
    "1 Jordan House, High St, Oxford OX1 4AA",
])
def test_country_names_used_as_street_names_do_not_win(detector, address):
    assert detector.detect(address).country_code == "GB"


@pytest.mark.parametrize("address", ["", None, "PO Box 123", "Sacramento, CA"])
def test_unknown_or_ambiguous_returns_none(detector, address):
    # "Sacramento, CA" is genuinely ambiguous without a ZIP (California vs Canada)
    assert detector.detect(address).country_code is None


def test_evidence_is_reported(detector):
    res = detector.detect("12 Baker St, London W1U 3BW")
    assert any("gb_postcode" in e for e in res.evidence)
    assert 0 < res.confidence <= 1


@pytest.mark.parametrize("value, expected", [
    ("GB", "GB"), ("gb", "GB"), ("UK", "GB"), ("GBR", "GB"), ("United Kingdom", "GB"),
    ("Deutschland", "DE"), ("NA", "NA"), ("BRA", "BR"), ("", None), ("XX", None),
])
def test_normalize_country_code(value, expected):
    assert normalize_country_code(value) == expected


def _det(code, conf):
    return CountryResult(code, conf)


@pytest.mark.parametrize("supplied, det, policy, expected", [
    ("US", _det("US", 0.9), "confidence", ("US", "MATCHED")),
    ("", _det("GB", 0.5), "confidence", ("GB", "INFERRED")),
    ("ZZ", _det("GB", 0.5), "confidence", ("GB", "INFERRED_INVALID_INPUT")),
    ("US", _det(None, 0.0), "confidence", ("US", "INPUT_ONLY")),
    ("US", _det("IN", 0.9), "confidence", ("IN", "CONFLICT_USED_ADDRESS")),
    ("US", _det("IN", 0.5), "confidence", ("US", "CONFLICT_KEPT_INPUT")),
    ("US", _det("IN", 0.9), "input", ("US", "CONFLICT_KEPT_INPUT")),
    ("US", _det("IN", 0.4), "address", ("IN", "CONFLICT_USED_ADDRESS")),
    ("", _det(None, 0.0), "confidence", ("", "UNKNOWN")),
])
def test_reconcile(supplied, det, policy, expected):
    assert reconcile_country(supplied, det, policy) == expected
