# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`party_er`: entity resolution for party records (`name`, `acct`, `address`, `country_code`). It assigns each real-world party a stable `entity_id` and infers the ISO-3166 alpha-2 country from the free-text address. Pure Python, using pandas, rapidfuzz and pycountry.

## Commands

Windows machine. **Don't use bare `python`.** On PATH it resolves to miniconda, which lacks the deps and fails with `ModuleNotFoundError: pycountry`. Always use the full path. In Bash that's `PY=/c/Users/casua/AppData/Local/Programs/Python/Python312/python.exe`, and in PowerShell it's `C:\Users\casua\AppData\Local\Programs\Python\Python312\python.exe`. It has the deps, and the 99 tests pass in about 2s. `$PY` below is shorthand. When running a command, type the full path in place of `$PY` so it matches the auto-allow rules in `.claude/settings.json`.

```bash
$PY -m pip install -r requirements.txt                 # or: pip install -e .[dev]
$PY -m pytest -q                                       # full suite (~1s)
$PY -m pytest tests/test_country.py -q                 # one file
$PY -m pytest "tests/test_country.py::test_detects_country" -q
$PY -m pytest -k "Georgia or Sacramento" -q            # by parametrize id / keyword

$PY -m party_er resolve data/sample_parties.csv -o output/resolved.csv --pairs output/pairs.csv
$PY -m party_er resolve new.csv -o out_v2.csv --previous output/resolved.csv   # stable IDs
$PY -m party_er country "12 Baker St, London W1U 3BW"   # debug country inference + evidence
$PY -m party_er serve data/sample_parties.csv --feedback feedback/corrections.json   # web UI on http://127.0.0.1:8000
$PY -m party_er resolve data/sample_parties.csv -o output/resolved.csv --feedback feedback/corrections.json  # apply UI corrections in batch
```

No linter or formatter is configured. `output/` is git-ignored scratch output.

## Code map (read only the file you need)

| File | Lines | Key symbols |
|---|---|---|
| `party_er/pipeline.py` | 193 | `ColumnMap`, `ResolverConfig`, `EntityResolver.resolve(df, previous, feedback)`, `ResolutionResult.labelled`, `read_table`, `load_previous_mapping` |
| `party_er/matching.py` | 182 | `MatchConfig`, `PreparedRecord`, `LearnedRules`, `blocking_keys`, `candidate_pairs`, `compare`, `score_candidates` |
| `party_er/feedback.py` | 283 | `FeedbackStore` (`add`, `remove`, `constraints`, `rules`), `Correction`, `REASONS`, `suggest_reason`, `calibration` |
| `party_er/web/server.py` | 381 | `AppState` (search, entity detail, review queue, `correct`, `undo`), `make_handler`, `serve`; UI is `web/static/index.html` (vanilla JS, no build) |
| `party_er/normalize.py` | 160 | `clean_text`, `fold_accents`, `normalize_name` → `NormalizedName`, `normalize_account`, `normalize_address`, `LEGAL_FORMS`, `ADDRESS_ABBREVIATIONS` |
| `party_er/clustering.py` | 127 | `UnionFind`, `connected_components`, `mint_entity_id`, `assign_entity_ids` |
| `party_er/country/detector.py` | 278 | weight constants (top of file), `CountryDetector.detect`, `normalize_country_code`, `reconcile_country` |
| `party_er/country/reference.py` | 282 | data only: `COUNTRY_ALIASES`, `*_STATES`, `CITIES`, `KEYWORDS`, `POSTAL_RULES`, `SCRIPT_RULES`, `phrase_table()` |
| `party_er/cli.py` | 109 | argparse for `resolve`, `serve` and `country` |

Project skills: `/add-geography` (fix or extend country inference) and `/explain-match` (why two records did or didn't merge, or tuning thresholds). Each holds the needed tables and rules, so you don't have to re-read the source.

## Architecture

`EntityResolver.resolve()` in `party_er/pipeline.py` is the single orchestration point. It runs these stages in order:

1. **Country inference.** `country/detector.py` `CountryDetector.detect(address)` uses evidence scoring. Each signal adds weighted points to candidate ISO2 codes, and every point is recorded as a human-readable evidence string. Signals: country names/aliases (greedy longest-phrase match against `reference.phrase_table()`), postal regexes, regions, cities, Unicode script, street keywords and trailing uppercase ISO codes. Confidence is `(1 - e^(-top/saturation)) * margin_over_runner_up`. Below `min_confidence` it returns `None`, and that is intentional for ambiguous cases like `"Sacramento, CA"`. Context rules reduce weights in these cases:
   - mid-address country names;
   - names followed by a `STREET_WORDS` token ("Canada Square");
   - phrases mapping to several countries ("GEORGIA" is a country and a US state);
   - ISO2 codes that are also US state or Canadian province codes (CA, IN, DE).

   `reconcile_country()` then combines the result with the supplied code according to `conflict_policy` and returns `(final_iso2, status)`.
2. **Normalization.** `normalize.py`: names (canonicalise, then strip trailing legal forms; `"LAST, FIRST"` is flipped for persons), accounts, addresses. `clean_text()` is the shared primitive. It folds accents and keeps non-Latin letters. Matching uses `NormalizedName.core`, not `.full`.
3. **Blocking and comparison.** `matching.py`: keys are the account, the sorted full name and each name token. Token blocks larger than `max_block_size` are skipped. `compare()` applies **ordered rules**:
   1. country conflict without a shared account means no match;
   2. `ACCOUNT+NAME`;
   3. `NAME+ADDRESS`;
   4. `WEIGHTED_SCORE`;
   5. `REVIEW`.

   Rule order matters. Differing accounts are deliberately *not* negative evidence, because one party can hold many accounts.
4. **Clustering and IDs.** `clustering.py`: `constrained_components` = union-find over MATCH pairs (transitive, strongest score first) after user must-links, refusing any join that would put a cannot-link pair together. `assign_entity_ids` mints `ENT-<sha1(min record_id)[:12]>`. With a previous mapping it reuses the majority previous ID, the largest cluster wins on splits, and retired IDs are never reissued.

5. **User corrections** (`feedback.py`, optional). Each correction is merge/split + reason + required explanation, stored as JSON with a name/account snapshot. It yields exact pair constraints (later corrections win) plus generalised `LearnedRules` by reason: `name_alias` → names treated as identical (and blocked together), `shared_account` → account ignored as evidence, `same_name_different_party` → name needs shared account or address ≥ `address_threshold`. `pairs` gets `model_decision`, `learned`, `feedback` columns. The web UI's "move" is a split + merge.

All tunables live in `MatchConfig` (matching) and `ResolverConfig` (country policy, thresholds, ID prefix, `ColumnMap` for input column names).

## Things that bite

- **Reading files:** always read input with `read_table()` (`dtype=str, keep_default_na=False`). Default pandas NA handling turns `"NA"` (Namibia) into NaN.
- **Extending coverage:** to add a geography, add data to `country/reference.py`, not code. `phrase_table()` and `country_name_index()` are `lru_cache`d and built from it. Add a case to `tests/test_country.py` alongside.
- **Coupled tests:** `tests/test_pipeline.py` asserts specific record groupings from `data/sample_parties.csv`. Changes to weights, thresholds or sample rows can break these on purpose. Update the expectations only when the new behaviour is genuinely correct.
- **Console encoding:** the Windows console is cp1252. The CLI reconfigures stdout to UTF-8; ad-hoc scripts that print addresses need `sys.stdout.reconfigure(encoding="utf-8")`.
- **Regex input:** postal regexes run on accent-folded **uppercase text with punctuation kept**, because formats like `471-8571` need the hyphen. Phrase matching runs on `clean_text` tokens, which have punctuation removed. Uppercase-only code checks use `clean_text(..., upper=False)`.
