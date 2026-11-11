"""Text normalization for party names, account numbers and addresses.

Everything here is deterministic and dependency-free so that the same raw
value always produces the same normalized value (important for stable IDs).
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

# Characters that NFKD does not decompose into base letter + accent.
_SPECIAL_FOLD = str.maketrans({
    "ß": "ss", "ẞ": "SS", "Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae",
    "Œ": "OE", "œ": "oe", "Ł": "L", "ł": "l", "Đ": "D", "đ": "d",
    "Þ": "TH", "þ": "th", "ı": "i",
})

_DOTTED_ABBREV = re.compile(r"\b(?:[A-Za-z]\.){2,}")  # U.S.A. / L.L.C. / S.A.
_NON_WORD = re.compile(r"[^\w]+")
_SPACES = re.compile(r"\s+")


def to_str(value) -> str:
    """Convert a cell value to str, treating None/NaN as empty."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def fold_accents(text: str) -> str:
    """Remove diacritics: 'Zürich' -> 'Zurich', 'Straße' -> 'Strasse'."""
    text = text.translate(_SPECIAL_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_text(value, upper: bool = True) -> str:
    """Fold accents, collapse dotted abbreviations, strip punctuation.

    Non-Latin letters (CJK, Cyrillic, ...) are kept, only punctuation is removed.
    """
    s = fold_accents(to_str(value))
    s = _DOTTED_ABBREV.sub(lambda m: m.group(0).replace(".", ""), s)
    s = s.replace("&", " and ")
    s = _NON_WORD.sub(" ", s).replace("_", " ")
    s = _SPACES.sub(" ", s).strip()
    return s.upper() if upper else s


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #
# Variant -> canonical legal-form token.
LEGAL_FORMS = {
    "LIMITED": "LTD", "LTD": "LTD", "INCORPORATED": "INC", "INC": "INC",
    "CORPORATION": "CORP", "CORP": "CORP", "COMPANY": "CO", "CO": "CO",
    "LLC": "LLC", "LLP": "LLP", "LP": "LP", "PLC": "PLC", "PUBLIC": "PUBLIC",
    "PRIVATE": "PVT", "PVT": "PVT", "PTE": "PTE", "PTY": "PTY",
    "GMBH": "GMBH", "AG": "AG", "AKTIENGESELLSCHAFT": "AG", "KG": "KG",
    "SA": "SA", "SAS": "SAS", "SARL": "SARL", "SL": "SL", "SPA": "SPA",
    "SRL": "SRL", "BV": "BV", "NV": "NV", "AB": "AB", "AS": "AS", "ASA": "ASA",
    "OY": "OY", "OYJ": "OYJ", "KK": "KK", "SE": "SE", "CIE": "CIE",
    "LTDA": "LTDA", "BHD": "BHD", "SDN": "SDN", "FZE": "FZE", "FZCO": "FZCO",
}
LEGAL_CANON = set(LEGAL_FORMS.values())

TITLES = {"MR", "MRS", "MS", "MISS", "MX", "DR", "PROF", "SIR", "MADAM", "DAME", "LORD"}

ORG_HINTS = {
    "BANK", "BANCO", "TRADING", "HOLDINGS", "HOLDING", "GROUP", "INTERNATIONAL",
    "SERVICES", "SOLUTIONS", "TECHNOLOGIES", "TECHNOLOGY", "INDUSTRIES",
    "ENTERPRISES", "FOODS", "MOTOR", "MOTORS", "LOGISTICS", "CONSULTANCY",
    "CONSULTING", "PARTNERS", "CAPITAL", "FUND", "TRUST", "FOUNDATION",
    "ASSOCIATION", "UNIVERSITY", "HOSPITAL", "INSURANCE", "SYSTEMS", "WIDGETS",
}


@dataclass(frozen=True)
class NormalizedName:
    full: str        # cleaned, legal forms canonicalised
    core: str        # legal forms / leading THE removed: used for matching
    party_type: str  # "ORG" or "PERSON" (heuristic)


def _canon_tokens(text: str) -> list[str]:
    return [LEGAL_FORMS.get(t, t) for t in clean_text(text).split()]


def normalize_name(raw) -> NormalizedName:
    text = to_str(raw)
    tokens = _canon_tokens(text)
    while tokens and tokens[0] in TITLES:
        tokens.pop(0)

    is_org = any(t in LEGAL_CANON or t in ORG_HINTS for t in tokens)

    # "SMITH, JOHN" -> "JOHN SMITH" for people.
    if not is_org and text.count(",") == 1:
        last, first = text.split(",")
        tokens = _canon_tokens(f"{first} {last}")
        while tokens and tokens[0] in TITLES:
            tokens.pop(0)

    core = list(tokens)
    while core and (core[-1] in LEGAL_CANON or core[-1] == "AND"):
        core.pop()
    if core and core[0] == "THE":
        core.pop(0)
    if not core:
        core = list(tokens)

    return NormalizedName(" ".join(tokens), " ".join(core), "ORG" if is_org else "PERSON")


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
_ACCT_PLACEHOLDERS = {"", "0", "NA", "NONE", "NULL", "UNKNOWN", "TBD", "NIL"}


def normalize_account(raw) -> str:
    """'000-123-456' -> '123456'. Placeholders become ''."""
    s = re.sub(r"[^0-9A-Za-z]", "", fold_accents(to_str(raw))).upper()
    if s.isdigit():
        s = s.lstrip("0")
    return "" if s in _ACCT_PLACEHOLDERS else s


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #
ADDRESS_ABBREVIATIONS = {
    "STREET": "ST", "STR": "ST", "STRASSE": "ST", "ROAD": "RD", "AVENUE": "AVE",
    "AV": "AVE", "AVENIDA": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "LANE": "LN", "PLACE": "PL", "SQUARE": "SQ", "COURT": "CT", "HIGHWAY": "HWY",
    "PARKWAY": "PKWY", "TERRACE": "TER", "SUITE": "STE", "APARTMENT": "APT",
    "FLOOR": "FL", "BUILDING": "BLDG", "NUMBER": "NO", "CALLE": "C",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH",
    "FIFTH": "5TH", "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH",
    "NINTH": "9TH", "TENTH": "10TH",
}


def normalize_address(raw) -> str:
    tokens = clean_text(raw).split()
    out = []
    for t in tokens:
        # Compound German street names: BAHNHOFSTRASSE / BAHNHOFSTR -> BAHNHOF ST
        for suffix in ("STRASSE", "STR"):
            if len(t) > len(suffix) + 2 and t.endswith(suffix):
                out.extend([t[:-len(suffix)], "ST"])
                break
        else:
            out.append(ADDRESS_ABBREVIATIONS.get(t, t))
    return " ".join(out)
