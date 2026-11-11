---
name: explain-match
description: Explain why two party_er records did or did not get the same entity_id, or tune MatchConfig thresholds. Use for "why did R00x and R00y merge / not merge" or "too many / too few matches" questions.
---

# Explain or tune a match decision

`PY=/c/Users/casua/AppData/Local/Programs/Python/Python312/python.exe` (bare `python` is miniconda without deps).

## 1. Get the pair evidence (don't read the code first)
```bash
$PY -m party_er resolve <input.csv> -o output/resolved.csv --pairs output/pairs.csv
grep -E "R001.*R007|R007.*R001" output/pairs.csv
```
`pairs.csv` columns are `left_record_id, right_record_id, left_name, right_name, name_sim, address_sim, account_match, score, decision, rule, entity_id`. It holds **only MATCH and REVIEW pairs**.

`resolved.csv` adds these columns to the input: `country_code_inferred, country_confidence, country_evidence, country_code_final, country_status, name_normalized, party_type, entity_id, entity_size`.

## 2. Interpret
**Same entity_id but no direct MATCH row:** clustering is transitive (union-find). Follow the chain of MATCH rows through a third record.

**Pair absent from pairs.csv:** one of two things happened.
- The records were never compared. Blocking keys are the account, the sorted full name and each name token, and token blocks larger than `max_block_size` (500) are skipped.
- They were compared and got NO_MATCH (`COUNTRY_CONFLICT` or `LOW_SIMILARITY`). Check `country_code_final` for both records in resolved.csv.

**Rules** in `matching.compare()`, first hit wins (defaults from `MatchConfig`):
| # | Rule | Condition |
|---|---|---|
| 1 | COUNTRY_CONFLICT → NO_MATCH | both countries known, they differ, no shared account |
| 2 | ACCOUNT+NAME | same account and name_sim ≥ 0.80 |
| 3 | NAME+ADDRESS | name_sim ≥ 0.92 and address_sim ≥ 0.75 |
| 4 | WEIGHTED_SCORE | score ≥ 0.88 and name_sim ≥ 0.85 |
| 5 | REVIEW (BELOW_THRESHOLD) | score ≥ 0.75 |

`score = 0.6*name + 0.4*addr`. If the address is missing, `score = name*0.9` instead. A shared account adds 0.15, capped at 1. Different house numbers multiply address_sim by 0.7. Names compare on `NormalizedName.core`, which has legal forms stripped and "LAST, FIRST" flipped. **Differing accounts are not negative evidence. That's deliberate.**

## 3. Tune
Edit the `MatchConfig` defaults (`party_er/matching.py`) or pass `ResolverConfig(match=MatchConfig(...))`. Then run `$PY -m pytest -q`. `tests/test_pipeline.py` asserts groupings from `data/sample_parties.csv`, so a failure there is the signal that behaviour changed.
