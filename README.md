# Party Entity Resolution

Assigns a unique, stable **entity ID** to party records (`name`, `acct`, `address`,
`country_code`) so that records referring to the same real-world party share one ID,
and **infers the country code from the address** to fill gaps and catch bad codes.

```
input CSV ──► 1. country inference ──► 2. normalize ──► 3. block ──► 4. compare ──► 5. cluster ──► 6. entity IDs
              (address → ISO2,          (names, accts,   (candidate   (rules +       (connected    (deterministic,
               reconcile with input)     addresses)       pairs)       scores)        components)   stable across runs)
```

## Quick start

```bash
pip install -r requirements.txt

# resolve a file
python -m party_er resolve data/sample_parties.csv -o output/resolved.csv --pairs output/pairs.csv

# check what country an address resolves to (with evidence)
python -m party_er country "15 rue de Rivoli 75001 Paris" "350 5th Ave, New York NY 10118"

# next run: keep the IDs from the previous run stable
python -m party_er resolve new_extract.csv -o output/resolved_v2.csv --previous output/resolved.csv

pytest
```

Different column names? Use `--name-col`, `--acct-col`, `--address-col`,
`--country-code-col` or `--record-id-col`. If there is no `record_id` column, one is generated.
`.xlsx` input works if `openpyxl` is installed.

From Python:

```python
from party_er import EntityResolver, ResolverConfig, read_table
result = EntityResolver(ResolverConfig()).resolve(read_table("parties.csv"))
result.records   # input + country + entity columns
result.pairs     # MATCH / REVIEW pairs with scores and the rule that fired
```

## Web UI: search and correct entities

```bash
python -m party_er serve data/sample_parties.csv --feedback feedback/corrections.json -o output/resolved.csv
# open http://127.0.0.1:8000
```

- **Search** by name (fuzzy), account, address, record ID or entity ID. Open an entity to see its
  records, the evidence linking them (similarities, rule, learned rules) and near-miss records outside it.
- **Correct** it: detach records ("not this party"), move records to another entity, merge another
  entity in, or answer the **Review queue** of uncertain pairs. Every correction needs an explanation
  and a reason (pre-selected from the explanation's wording).
- **It learns** in two ways. First, the exact pairs become must-link / cannot-link constraints that hold
  on every re-run, transitive matches included. Second, the reason generalises to records nobody corrected:

  | reason | what is learned |
  |---|---|
  | names are aliases (abbreviation, trading/former name) | those names are treated as the same name everywhere |
  | shared account (joint, nominee, omnibus) | that account no longer counts as evidence |
  | same name, different party | that name alone no longer matches: also needs the account or address |

- The **Corrections & learning** tab lists every correction with **Undo**, the learned rules, and how well
  the base model agrees with your labels (with a suggested `score_threshold` once there are enough).

Corrections are saved to the `--feedback` JSON (keep it in version control). Apply them in batch with
`python -m party_er resolve ... --feedback feedback/corrections.json`. The server binds to 127.0.0.1 and
has no authentication.

## Output columns

| column | meaning |
|---|---|
| `country_code_inferred` | ISO-3166 alpha-2 country derived **only** from the address (blank if not confident) |
| `country_confidence` | 0–1 |
| `country_evidence` | why, e.g. `postal:gb_postcode[W1U 3BW](+8); city:LONDON(+3)` |
| `country_code_final` | the country used for matching (see policy below) |
| `country_status` | `MATCHED`, `INFERRED`, `INPUT_ONLY`, `CONFLICT_USED_ADDRESS`, `CONFLICT_KEPT_INPUT`, `INFERRED_INVALID_INPUT`, `INVALID_INPUT`, `UNKNOWN` |
| `name_normalized`, `party_type` | matching key for the name; ORG/PERSON heuristic |
| `entity_id`, `entity_size` | the resolved entity and how many records it has |

`pairs.csv` lists every matched pair and every near-miss (`REVIEW`) with `name_sim`,
`address_sim`, `account_match`, `score` and `rule`. Use it for audit and manual review.

## How country inference works

`party_er/country/detector.py` scores every piece of evidence in the address and adds
points to candidate countries:

| signal | example | points |
|---|---|---|
| country name / alias at end of address | `..., Deutschland`, `UAE`, `España` | 10 |
| country name mid-address | | 4 |
| …followed by a street word (street name, not a country) | `Canada Square`, `Jordan House` | 0.5 |
| postal-code formats | US `NY 10118`, CA `L5N 0A1`, GB `W1U 3BW`, AU `NSW 2000`, BR `70040-912`, NL `1012 NX`, JP `〒471-8571` … | 3–9 |
| state / province names and codes | `Maharashtra`, `Ontario`, `TX` | 3–5 |
| major cities | `Mumbai`, `Zurich` | 3 |
| writing system | kana → JP, Hangul → KR, Thai → TH, Hebrew → IL … | 1.5–6 |
| street vocabulary | `Cedex`, `Calle`, `*strasse`, `Nagar` | 1–6 |
| trailing ISO code (uppercase only) | `..., Berlin, DE`, `..., ITA` | 3–6 |

`confidence = (1 − e^(−top/6)) × (top − runner_up) / top`, so it rewards both the amount
of evidence and the margin over the runner-up. Below `min_country_confidence` (default 0.3)
no country is returned. Ambiguous cases, such as `Sacramento, CA` (California or Canada?),
deliberately come back as unknown instead of as a guess.

**Reconciling with the supplied `country_code`.** Supplied values may be ISO2, ISO3 or names
(`GB`, `GBR`, `UK`, `United Kingdom`). `NA` (Namibia) is read as a code, not as a missing value.
When the supplied code and the address disagree, `--conflict-policy` decides:

* `confidence` (default): use the address country if its confidence is at least `--override-confidence` (0.7), otherwise keep the input
* `input`: always keep the supplied code (conflict still flagged)
* `address`: always use the address country

## How matching works

1. **Normalize.** Accents are folded (`María` → `MARIA`) and legal forms are canonicalised and
   stripped (`Private Limited` = `Pvt Ltd`, `Aktiengesellschaft` = `AG`, `L.L.C.` = `LLC`).
   `Smith, John` becomes `JOHN SMITH`. Accounts become alphanumeric with leading zeros removed.
   Street words are abbreviated (`Fifth Avenue` = `5th Ave`, `Bahnhofstrasse` = `Bahnhofstr.`).
2. **Blocking.** Only records that share an account, the full sorted name, or a name token are
   compared. Name-token blocks bigger than `max_block_size` (very common tokens) are skipped.
3. **Rules,** in order (`party_er/matching.py`, `MatchConfig`):
   * countries differ (both known) and no shared account → **no match**
   * same account and name similarity ≥ 0.80 → `ACCOUNT+NAME`
   * name ≥ 0.92 and address ≥ 0.75 → `NAME+ADDRESS`
   * weighted score (0.6·name + 0.4·address, +0.15 for a shared account) ≥ 0.88 → `WEIGHTED_SCORE`
   * score ≥ 0.75 → `REVIEW` (not merged, written to pairs.csv)

   Different house numbers reduce address similarity (×0.7), so `10 Main St` ≠ `12 Main St`.
   One entity may hold several accounts, so differing accounts are not evidence against a match.
4. **Clustering.** Matched pairs are merged transitively (union-find).
5. **Entity IDs.** `ENT-<12 hex>` is derived from the smallest `record_id` in the cluster, so
   reruns are deterministic. With `--previous`, a cluster keeps the ID held by most of its members.
   If an old entity splits, the largest part keeps the ID. If entities merge, the ID with the
   most members survives. Retired IDs are never reissued.

## Tuning and extending

* **Thresholds:** `MatchConfig` (matching), `ResolverConfig` (country policy, ID prefix).
  Tune on a labelled sample. Check `pairs.csv` REVIEW rows to find the right cut-offs for your data.
* **Country coverage:** add cities, regions, aliases or postal formats to
  `party_er/country/reference.py`. Add a test case to `tests/test_country.py` for each new
  geography.
* **Scale:** blocking keeps comparisons near-linear, which suits up to a few million rows on one
  machine. Beyond that, run the same rules in Spark/Splink, or partition the data by
  `country_code_final`.
* **Transitive chaining:** A~B and B~C merges A, B and C even if A≁C. For very large clusters,
  check `entity_size` outliers, or switch to a stricter clustering step (for example, require
  each member to match at least two others).
* **Heavier address parsing:** for messy global addresses, the `libpostal` parser can feed
  extra structured signals (city, postcode, country) into the same scorer.
