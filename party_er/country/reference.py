"""Reference data used to infer an ISO-3166 alpha-2 country code from an address.

Country names / ISO codes come from `pycountry` (all 249 ISO countries).
Everything else (aliases, regions, cities, postal formats) is a curated list
that you are expected to extend for the geographies that matter to your data.
"""
from __future__ import annotations

import re
from functools import lru_cache

import pycountry

from ..normalize import clean_text

ISO2: frozenset[str] = frozenset(c.alpha_2 for c in pycountry.countries)
ISO3_TO_ISO2: dict[str, str] = {c.alpha_3: c.alpha_2 for c in pycountry.countries}

# --------------------------------------------------------------------------- #
# Country aliases (local-language names, abbreviations, common short forms)
# --------------------------------------------------------------------------- #
COUNTRY_ALIASES: dict[str, list[str]] = {
    "US": ["USA", "U S A", "U S", "UNITED STATES", "UNITED STATES OF AMERICA", "AMERICA"],
    "GB": ["UK", "U K", "UNITED KINGDOM", "GREAT BRITAIN", "BRITAIN", "ENGLAND",
           "SCOTLAND", "WALES", "NORTHERN IRELAND"],
    "DE": ["GERMANY", "DEUTSCHLAND", "BRD", "ALLEMAGNE"],
    "FR": ["FRANCE", "REPUBLIQUE FRANCAISE"],
    "ES": ["SPAIN", "ESPANA"],
    "IT": ["ITALY", "ITALIA"],
    "NL": ["NETHERLANDS", "THE NETHERLANDS", "HOLLAND", "NEDERLAND"],
    "BE": ["BELGIUM", "BELGIQUE", "BELGIE"],
    "CH": ["SWITZERLAND", "SCHWEIZ", "SUISSE", "SVIZZERA"],
    "AT": ["AUSTRIA", "OSTERREICH", "OESTERREICH"],
    "SE": ["SWEDEN", "SVERIGE"], "NO": ["NORWAY", "NORGE"], "DK": ["DENMARK", "DANMARK"],
    "FI": ["FINLAND", "SUOMI"], "PL": ["POLAND", "POLSKA"], "GR": ["GREECE", "HELLAS"],
    "HU": ["HUNGARY", "MAGYARORSZAG"], "HR": ["CROATIA", "HRVATSKA"],
    "CZ": ["CZECHIA", "CZECH REPUBLIC"], "IE": ["IRELAND", "EIRE"],
    "PT": ["PORTUGAL"], "TR": ["TURKEY", "TURKIYE"],
    "RU": ["RUSSIA", "RUSSIAN FEDERATION"],
    "AE": ["UAE", "U A E", "UNITED ARAB EMIRATES", "EMIRATES"],
    "SA": ["KSA", "SAUDI ARABIA"],
    "KR": ["SOUTH KOREA", "KOREA", "REPUBLIC OF KOREA"],
    "KP": ["NORTH KOREA", "DPRK"],
    "CN": ["CHINA", "PRC", "PEOPLES REPUBLIC OF CHINA", "PEOPLE S REPUBLIC OF CHINA"],
    "TW": ["TAIWAN", "REPUBLIC OF CHINA"], "HK": ["HONG KONG", "HONGKONG", "HONG KONG SAR"],
    "MO": ["MACAU", "MACAO"], "JP": ["JAPAN", "NIPPON", "NIHON"],
    "IN": ["INDIA", "BHARAT"], "VN": ["VIETNAM", "VIET NAM"], "LA": ["LAOS"],
    "IR": ["IRAN"], "SY": ["SYRIA"], "MM": ["MYANMAR", "BURMA"], "BN": ["BRUNEI"],
    "BR": ["BRAZIL", "BRASIL"], "MX": ["MEXICO", "ESTADOS UNIDOS MEXICANOS"],
    "BO": ["BOLIVIA"], "VE": ["VENEZUELA"], "TZ": ["TANZANIA"], "MD": ["MOLDOVA"],
    "MK": ["NORTH MACEDONIA", "MACEDONIA"], "CI": ["IVORY COAST", "COTE D IVOIRE"],
    "CD": ["DR CONGO", "DRC", "DEMOCRATIC REPUBLIC OF THE CONGO"],
    "CG": ["REPUBLIC OF THE CONGO"], "CV": ["CAPE VERDE"], "SZ": ["SWAZILAND", "ESWATINI"],
    "PS": ["PALESTINE"], "VA": ["VATICAN", "VATICAN CITY"], "VG": ["BVI"],
    "NZ": ["NEW ZEALAND", "AOTEAROA"], "PH": ["PHILIPPINES"], "LC": ["ST LUCIA"],
}

# --------------------------------------------------------------------------- #
# Sub-national regions (state / province names). Weight is per entry kind.
# --------------------------------------------------------------------------- #
US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}
US_STATE_ABBR = frozenset(US_STATES.values())

CA_PROVINCES = {
    "ONTARIO": "ON", "QUEBEC": "QC", "BRITISH COLUMBIA": "BC", "ALBERTA": "AB",
    "MANITOBA": "MB", "SASKATCHEWAN": "SK", "NOVA SCOTIA": "NS", "NEW BRUNSWICK": "NB",
    "NEWFOUNDLAND AND LABRADOR": "NL", "NEWFOUNDLAND": "NL",
    "PRINCE EDWARD ISLAND": "PE", "YUKON": "YT", "NORTHWEST TERRITORIES": "NT",
    "NUNAVUT": "NU",
}
CA_PROVINCE_ABBR = frozenset(CA_PROVINCES.values())

AU_STATES = {
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD",
    "WESTERN AUSTRALIA": "WA", "SOUTH AUSTRALIA": "SA", "TASMANIA": "TAS",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT", "NORTHERN TERRITORY": "NT",
}
AU_STATE_ABBR = frozenset(AU_STATES.values())
# Abbreviations safe to use as standalone phrases (others clash with ISO codes / words).
AU_SAFE_ABBR = ["NSW", "VIC", "QLD", "TAS"]

IN_STATES = [
    "ANDHRA PRADESH", "ARUNACHAL PRADESH", "ASSAM", "BIHAR", "CHHATTISGARH", "GOA",
    "GUJARAT", "HARYANA", "HIMACHAL PRADESH", "JHARKHAND", "KARNATAKA", "KERALA",
    "MADHYA PRADESH", "MAHARASHTRA", "MANIPUR", "MEGHALAYA", "MIZORAM", "NAGALAND",
    "ODISHA", "ORISSA", "RAJASTHAN", "SIKKIM", "TAMIL NADU", "TELANGANA", "TRIPURA",
    "UTTAR PRADESH", "UTTARAKHAND", "WEST BENGAL", "JAMMU AND KASHMIR", "LADAKH",
    "PUDUCHERRY", "CHANDIGARH",
]
SHARED_REGIONS = {"PUNJAB": ["IN", "PK"]}

# --------------------------------------------------------------------------- #
# Major cities (weak evidence on their own; London, Ontario exists!)
# --------------------------------------------------------------------------- #
CITIES: dict[str, list[str]] = {
    "US": ["NEW YORK", "NEW YORK CITY", "LOS ANGELES", "CHICAGO", "HOUSTON",
           "SAN FRANCISCO", "BOSTON", "SEATTLE", "MIAMI", "DALLAS", "ATLANTA",
           "PHILADELPHIA", "PHOENIX", "SAN DIEGO", "SAN JOSE", "DENVER", "LAS VEGAS",
           "DETROIT", "CHARLOTTE", "AUSTIN", "BROOKLYN", "MANHATTAN"],
    "GB": ["LONDON", "MANCHESTER", "BIRMINGHAM", "LIVERPOOL", "EDINBURGH", "GLASGOW",
           "LEEDS", "BRISTOL", "CARDIFF", "BELFAST", "SHEFFIELD", "NOTTINGHAM"],
    "DE": ["BERLIN", "MUNICH", "MUNCHEN", "MUENCHEN", "HAMBURG", "FRANKFURT",
           "FRANKFURT AM MAIN", "COLOGNE", "KOLN", "KOELN", "STUTTGART", "DUSSELDORF",
           "DUESSELDORF", "LEIPZIG", "DRESDEN", "HANNOVER", "NURNBERG", "NUREMBERG"],
    "FR": ["PARIS", "LYON", "MARSEILLE", "TOULOUSE", "BORDEAUX", "LILLE",
           "STRASBOURG", "NANTES", "MONTPELLIER"],
    "IT": ["ROMA", "ROME", "MILANO", "MILAN", "NAPOLI", "NAPLES", "TORINO", "TURIN",
           "FIRENZE", "FLORENCE", "VENEZIA", "VENICE", "BOLOGNA", "GENOVA"],
    "ES": ["MADRID", "BARCELONA", "SEVILLA", "SEVILLE", "BILBAO", "MALAGA", "ZARAGOZA"],
    "NL": ["AMSTERDAM", "ROTTERDAM", "THE HAGUE", "DEN HAAG", "UTRECHT", "EINDHOVEN"],
    "BE": ["BRUSSELS", "BRUXELLES", "BRUSSEL", "ANTWERP", "ANTWERPEN", "GHENT", "GENT"],
    "CH": ["ZURICH", "GENEVA", "GENEVE", "GENF", "BASEL", "BERN", "LAUSANNE", "LUGANO"],
    "AT": ["VIENNA", "WIEN", "SALZBURG", "GRAZ", "INNSBRUCK"],
    "IE": ["DUBLIN", "CORK", "GALWAY"], "SE": ["STOCKHOLM", "GOTHENBURG", "GOTEBORG", "MALMO"],
    "NO": ["OSLO", "BERGEN"], "DK": ["COPENHAGEN", "KOBENHAVN"], "FI": ["HELSINKI"],
    "PL": ["WARSAW", "WARSZAWA", "KRAKOW", "WROCLAW", "GDANSK"],
    "CZ": ["PRAGUE", "PRAHA", "BRNO"], "PT": ["LISBON", "LISBOA", "PORTO"],
    "GR": ["ATHENS", "THESSALONIKI"], "TR": ["ISTANBUL", "ANKARA", "IZMIR"],
    "RU": ["MOSCOW", "MOSKVA", "SAINT PETERSBURG", "ST PETERSBURG"],
    "IN": ["MUMBAI", "BOMBAY", "DELHI", "NEW DELHI", "BANGALORE", "BENGALURU",
           "CHENNAI", "MADRAS", "KOLKATA", "CALCUTTA", "PUNE", "AHMEDABAD",
           "GURGAON", "GURUGRAM", "NOIDA", "JAIPUR"],
    "PK": ["KARACHI", "LAHORE", "ISLAMABAD"], "BD": ["DHAKA"], "LK": ["COLOMBO"],
    "CN": ["BEIJING", "SHANGHAI", "SHENZHEN", "GUANGZHOU", "CHENGDU", "HANGZHOU"],
    "HK": ["KOWLOON", "WAN CHAI", "CENTRAL HONG KONG"],
    "JP": ["TOKYO", "OSAKA", "KYOTO", "YOKOHAMA", "NAGOYA", "AICHI", "TOYOTA CITY"],
    "KR": ["SEOUL", "BUSAN"], "TW": ["TAIPEI"], "SG": ["SINGAPORE"],
    "MY": ["KUALA LUMPUR"], "TH": ["BANGKOK"], "ID": ["JAKARTA"],
    "PH": ["MANILA", "MAKATI"], "VN": ["HANOI", "HO CHI MINH", "HO CHI MINH CITY"],
    "AE": ["DUBAI", "ABU DHABI", "SHARJAH"], "SA": ["RIYADH", "JEDDAH"], "QA": ["DOHA"],
    "KW": ["KUWAIT CITY"], "BH": ["MANAMA"], "IL": ["TEL AVIV", "JERUSALEM"],
    "EG": ["CAIRO"], "GE": ["TBILISI"], "ZA": ["JOHANNESBURG", "CAPE TOWN", "DURBAN", "PRETORIA"],
    "NG": ["LAGOS", "ABUJA"], "KE": ["NAIROBI"],
    "AU": ["SYDNEY", "MELBOURNE", "BRISBANE", "PERTH", "ADELAIDE", "CANBERRA"],
    "NZ": ["AUCKLAND", "WELLINGTON"],
    "CA": ["TORONTO", "MONTREAL", "VANCOUVER", "CALGARY", "OTTAWA", "MISSISSAUGA"],
    "MX": ["MEXICO CITY", "CIUDAD DE MEXICO", "CDMX", "GUADALAJARA", "MONTERREY"],
    "BR": ["SAO PAULO", "RIO DE JANEIRO", "BRASILIA"], "AR": ["BUENOS AIRES"],
    "CL": ["SANTIAGO DE CHILE"], "CO": ["BOGOTA", "MEDELLIN"], "PE": ["LIMA"],
}

# --------------------------------------------------------------------------- #
# Street-type words. A country/city name followed by one of these is probably a
# street or building name ("Canada Square", "London Road", "Jordan House").
# --------------------------------------------------------------------------- #
STREET_WORDS = frozenset({
    "ST", "STREET", "RD", "ROAD", "AVE", "AVENUE", "LN", "LANE", "PL", "PLACE",
    "SQ", "SQUARE", "DR", "DRIVE", "WAY", "BLVD", "BOULEVARD", "PLAZA", "HOUSE",
    "TOWER", "TOWERS", "BUILDING", "BLDG", "CENTRE", "CENTER", "COURT", "PARK",
    "GARDENS", "TERRACE", "CRESCENT", "CLOSE", "WHARF", "QUAY", "HALL",
})

# --------------------------------------------------------------------------- #
# Street-vocabulary keywords: (token -> {iso2: weight})
# --------------------------------------------------------------------------- #
KEYWORDS: dict[str, dict[str, float]] = {
    "CEDEX": {"FR": 6}, "RUE": {"FR": 2, "BE": 0.5, "CH": 0.5},
    "STRASSE": {"DE": 2, "AT": 1, "CH": 1}, "GASSE": {"AT": 1.5, "DE": 1, "CH": 1},
    "STRAAT": {"NL": 2, "BE": 1}, "CALLE": {"ES": 1.5, "MX": 1}, "RUA": {"BR": 2, "PT": 1.5},
    "COLONIA": {"MX": 2}, "NAGAR": {"IN": 2}, "MARG": {"IN": 2}, "CHOWK": {"IN": 2},
    "JALAN": {"MY": 2, "ID": 2}, "SOI": {"TH": 2}, "GATAN": {"SE": 2}, "VAGEN": {"SE": 1.5},
    "VEJ": {"DK": 2}, "KATU": {"FI": 2}, "ULICA": {"PL": 2}, "ULITSA": {"RU": 2},
    "PREFECTURE": {"JP": 2}, "CHOME": {"JP": 3},
}
# Compound-word suffixes (BAHNHOFSTRASSE, KALVERSTRAAT, DROTTNINGGATAN ...)
SUFFIX_KEYWORDS: dict[str, dict[str, float]] = {
    "STRASSE": {"DE": 1.5, "AT": 1, "CH": 1}, "STRAAT": {"NL": 2, "BE": 1},
    "GATAN": {"SE": 2}, "VEJ": {"DK": 2}, "GADE": {"DK": 2},
}

# --------------------------------------------------------------------------- #
# Postal-code formats: (regex applied to accent-folded UPPERCASE text, scores, label)
# Ordered roughly from most to least specific.
# --------------------------------------------------------------------------- #
_US_ABBR_ALT = "|".join(sorted(US_STATE_ABBR))
_AU_ABBR_ALT = "|".join(sorted(AU_STATE_ABBR))

POSTAL_RULES: list[tuple[re.Pattern, dict[str, float], str]] = [
    (re.compile(rf"\b(?:{_US_ABBR_ALT})\s+\d{{5}}(?:-\d{{4}})?\b"), {"US": 9}, "us_state_zip"),
    (re.compile(r"\b\d{5}-\d{4}\b"), {"US": 5}, "us_zip_plus4"),
    (re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\s?\d[ABCEGHJ-NPRSTV-Z]\d\b"),
     {"CA": 8}, "ca_postcode"),
    (re.compile(r"\b(?:GIR 0AA|[A-PR-UWYZ](?:\d{1,2}|[A-HK-Y]\d{1,2}|\d[A-HJKSTUW]|"
                r"[A-HK-Y]\d[ABEHMNPRV-Y])\s?\d[ABD-HJLNP-UW-Z]{2})\b"), {"GB": 8}, "gb_postcode"),
    (re.compile(rf"\b(?:{_AU_ABBR_ALT})\s+\d{{4}}\b"), {"AU": 9}, "au_state_postcode"),
    (re.compile(r"\bPIN(?:\s?CODE)?\s*[:\-]?\s*\d{3}\s?\d{3}\b"), {"IN": 8}, "in_pin_code"),
    (re.compile(r"\b\d{5}-\d{3}\b"), {"BR": 6}, "br_cep"),
    (re.compile(r"\b[1-9]\d{3}\s?[A-Z]{2}\b"), {"NL": 5}, "nl_postcode"),
    (re.compile(r"\b\d{4}-\d{3}\b"), {"PT": 4}, "pt_postcode"),
    (re.compile(r"\b\d{3}-\d{4}\b"), {"JP": 3}, "jp_postcode"),
    (re.compile(r"\b\d{2}-\d{3}\b"), {"PL": 3}, "pl_postcode"),
    (re.compile(r"\bD-\s?\d{5}\b"), {"DE": 6}, "de_prefixed_postcode"),
    (re.compile(r"\bF-\s?\d{5}\b"), {"FR": 6}, "fr_prefixed_postcode"),
    (re.compile(r"\bCH-\s?\d{4}\b"), {"CH": 6}, "ch_prefixed_postcode"),
    (re.compile(r"\bNL-\s?\d{4}\b"), {"NL": 6}, "nl_prefixed_postcode"),
    (re.compile(r"\bDK-\s?\d{4}\b"), {"DK": 6}, "dk_prefixed_postcode"),
    (re.compile(r"\bSE-\s?\d{3}\s?\d{2}\b"), {"SE": 6}, "se_prefixed_postcode"),
    (re.compile(r"(?<![\d-])\d{6}(?![\d-])"), {"IN": 1.5, "SG": 1, "CN": 1}, "six_digit_postcode"),
]
JP_POSTAL_MARK = re.compile(r"〒\s?\d{3}-?\d{4}")

# --------------------------------------------------------------------------- #
# Unicode scripts: (pattern, scores, label). Evaluated on the raw address.
# --------------------------------------------------------------------------- #
SCRIPT_RULES: list[tuple[re.Pattern, dict[str, float], str]] = [
    (re.compile(r"[぀-ヿ]"), {"JP": 6}, "japanese_kana"),
    (re.compile(r"[가-힯ᄀ-ᇿ]"), {"KR": 6}, "hangul"),
    (re.compile(r"[฀-๿]"), {"TH": 6}, "thai"),
    (re.compile(r"[֐-׿]"), {"IL": 6}, "hebrew"),
    (re.compile(r"[Ͱ-Ͽ]"), {"GR": 5, "CY": 1.5}, "greek"),
    (re.compile(r"[Ⴀ-ჿ]"), {"GE": 6}, "georgian"),
    (re.compile(r"[԰-֏]"), {"AM": 6}, "armenian"),
    (re.compile(r"[ऀ-ॿ]"), {"IN": 4, "NP": 2}, "devanagari"),
    (re.compile(r"[ঀ-৿]"), {"BD": 3, "IN": 2}, "bengali"),
    (re.compile(r"[஀-௿]"), {"IN": 3, "LK": 1.5}, "tamil"),
    (re.compile(r"[Ѐ-ӿ]"), {"RU": 3, "UA": 1.5, "BY": 1, "KZ": 1, "BG": 1}, "cyrillic"),
    (re.compile(r"[؀-ۿ]"), {"SA": 1.5, "AE": 1.5, "EG": 1.5, "IR": 1}, "arabic"),
]
HAN = re.compile(r"[一-鿿]")  # only used if no kana was found


# --------------------------------------------------------------------------- #
# Phrase table: cleaned phrase -> list of (iso2, kind, base_weight)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def country_name_index() -> dict[str, str]:
    """Cleaned country name / alias -> ISO2 (for normalising user-supplied codes)."""
    index: dict[str, str] = {}
    for c in pycountry.countries:
        for attr in ("name", "official_name", "common_name"):
            value = getattr(c, attr, None)
            if value:
                index[clean_text(value)] = c.alpha_2
    for iso2, aliases in COUNTRY_ALIASES.items():
        for alias in aliases:
            index[clean_text(alias)] = iso2
    return index


@lru_cache(maxsize=1)
def phrase_table() -> dict[str, list[tuple[str, str, float]]]:
    table: dict[str, dict[tuple[str, str], float]] = {}

    def add(phrase: str, iso2: str, kind: str, weight: float) -> None:
        key = clean_text(phrase)
        slot = table.setdefault(key, {})
        slot[(iso2, kind)] = max(weight, slot.get((iso2, kind), 0))

    for phrase, iso2 in country_name_index().items():
        add(phrase, iso2, "country", 10)
    for name in US_STATES:
        add(name, "US", "region", 4)
    for name in CA_PROVINCES:
        add(name, "CA", "region", 5)
    for name in AU_STATES:
        add(name, "AU", "region", 4)
    for abbr in AU_SAFE_ABBR:
        add(abbr, "AU", "region", 3)
    for name in IN_STATES:
        add(name, "IN", "region", 5)
    for name, isos in SHARED_REGIONS.items():
        for iso2 in isos:
            add(name, iso2, "region", 4)
    for iso2, cities in CITIES.items():
        for city in cities:
            add(city, iso2, "city", 3)

    return {k: [(iso, kind, w) for (iso, kind), w in v.items()] for k, v in table.items()}
