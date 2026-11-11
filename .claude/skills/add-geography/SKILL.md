---
name: add-geography
description: Add or fix party_er country inference for an address pattern (country alias, region, city, postal-code format, street keyword, Unicode script). Use when an address is detected as the wrong country, or as None/UNKNOWN when it should not be.
---

# Add or fix a geography

Everything here is **data in `party_er/country/reference.py`**. Change `detector.py` only when you need a new *kind* of signal. You do not need to read the whole file; the tables are below.

`PY=/c/Users/casua/AppData/Local/Programs/Python/Python312/python.exe` (bare `python` is miniconda without deps).

## 1. Reproduce
```bash
$PY -m party_er country "<address>" ["<address2>" ...]
# -> GE  confidence=0.37  runner_up=US
#    evidence: country:GEORGIA(+5); city:TBILISI(+3)
```
The evidence line shows which signal is missing or wrong.

## 2. Pick the table (reference.py)
| Need | Structure | Base weight |
|---|---|---|
| Country name variant or abbreviation | `COUNTRY_ALIASES[iso2]` list | 10 at the tail, 4 mid-address, 0.5 before a street word |
| State or province | `US_STATES` / `CA_PROVINCES` / `AU_STATES` (name→abbr), `IN_STATES` list | 4–5 |
| Region shared by several countries | `SHARED_REGIONS[name] = [iso2, ...]` | 4 each |
| City | `CITIES[iso2]` list | 3 (weak by design: London, Ontario) |
| Street vocabulary token | `KEYWORDS[token] = {iso2: w}` | 0.5–6 |
| Compound-word suffix (…STRASSE) | `SUFFIX_KEYWORDS` | 1–2 |
| Postal-code format | `POSTAL_RULES` (regex, {iso2: w}, label) | 1.5–9 |
| Non-Latin script | `SCRIPT_RULES` (regex, {iso2: w}, label) | 1–6 |
| Street-like word that blocks a country-name match | `STREET_WORDS` | — |

Pycountry already supplies all 249 official/common names and ISO2/ISO3 codes, so check that before adding an alias.

## 3. Text-form rules
- **Phrases** (aliases, regions, cities, keywords) are matched on `clean_text` tokens: UPPERCASE, accents folded, punctuation turned into spaces. Write them that way: `"U S A"`, `"MUNCHEN"`. Longest phrase wins.
- **Postal regexes** run on accent-folded UPPERCASE text with **punctuation kept**. Use `\b`, and insert the rule before any looser rule that would also match. The list is ordered from most to least specific.
- Ambiguity is handled automatically. A phrase that maps to several countries is down-weighted (GEORGIA, for example), as is a 2-letter code that is also a US or Canadian state code (CA, IN, DE). If a result is genuinely ambiguous, put it in the `None` test list. Don't force a winner.

## 4. Test
Add the case to `tests/test_country.py` → `test_detects_country` parametrize list (or `test_unknown_or_ambiguous_returns_none`), then:
```bash
$PY -m pytest -q        # whole suite ~1s
```
If `tests/test_pipeline.py` breaks, a sample-row grouping changed. Update its expectation only if the new grouping is genuinely correct.
