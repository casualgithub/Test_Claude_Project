import pytest

from party_er.normalize import clean_text, normalize_account, normalize_address, normalize_name


@pytest.mark.parametrize("a, b", [
    ("Acme Trading Limited", "ACME TRADING LTD."),
    ("Tata Consultancy Services Pvt Ltd", "Tata Consultancy Services Private Limited"),
    ("Siemens AG", "SIEMENS AKTIENGESELLSCHAFT"),
    ("Emirates Logistics LLC", "Emirates Logistics L.L.C."),
    ("The Acme Company", "Acme Co"),
])
def test_legal_forms_are_removed_from_core(a, b):
    assert normalize_name(a).core == normalize_name(b).core


def test_person_name_comma_flip_and_titles():
    assert normalize_name("Smith, John").core == "JOHN SMITH"
    assert normalize_name("Dr. John Smith").core == "JOHN SMITH"
    assert normalize_name("María García").core == "MARIA GARCIA"


def test_party_type():
    assert normalize_name("Acme Ltd").party_type == "ORG"
    assert normalize_name("John Smith").party_type == "PERSON"


@pytest.mark.parametrize("raw, expected", [
    ("000123456", "123456"), ("DE-4411", "DE4411"), ("  ", ""), ("N/A", ""), (None, ""),
])
def test_normalize_account(raw, expected):
    assert normalize_account(raw) == expected


def test_normalize_address():
    assert normalize_address("350 Fifth Avenue") == normalize_address("350 5th Ave")
    assert normalize_address("Bahnhofstrasse 10") == "BAHNHOF ST 10"
    assert normalize_address("Bahnhofstr. 10") == "BAHNHOF ST 10"
    assert normalize_address("Werner-von-Siemens-Straße 1") == normalize_address("Werner von Siemens Str. 1")


def test_clean_text_keeps_non_latin_letters():
    assert clean_text("U.S.A.") == "USA"
    assert "トヨタ" in clean_text("トヨタ町")
