"""Candidate generation (blocking) and pairwise match decisions."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from rapidfuzz import fuzz

MATCH, REVIEW, NO_MATCH = "MATCH", "REVIEW", "NO_MATCH"


@dataclass(frozen=True)
class PreparedRecord:
    idx: int
    record_id: str
    name_core: str
    account: str
    address: str
    country: str  # final ISO2, "" if unknown


@dataclass
class MatchConfig:
    # Rule 1: same account + similar name
    account_name_threshold: float = 0.80
    # Rule 2: very similar name + similar address
    strong_name_threshold: float = 0.92
    address_threshold: float = 0.75
    # Rule 3: weighted overall score
    score_threshold: float = 0.88
    min_name_for_score: float = 0.85
    name_weight: float = 0.6            # address weight = 1 - name_weight
    account_bonus: float = 0.15
    missing_address_factor: float = 0.9
    # Pairs scoring at least this (but not matched) are written out for manual review
    review_threshold: float = 0.75
    # Blocks larger than this are skipped (very common name tokens like JOHN)
    max_block_size: int = 500
    # Different house/building numbers -> address similarity multiplied by this
    number_mismatch_penalty: float = 0.7


@dataclass(frozen=True)
class LearnedRules:
    """Generalised lessons from user corrections (built by party_er.feedback)."""
    # name_core -> canonical name_core of its alias group (names the user said are one party)
    name_aliases: dict[str, str] = field(default_factory=dict)
    # accounts the user said are shared by different parties: not evidence of identity
    shared_accounts: frozenset[str] = frozenset()
    # name cores the user said are not distinctive: need a shared account or a similar address
    ambiguous_names: frozenset[str] = frozenset()


NO_RULES = LearnedRules()


@dataclass
class PairResult:
    i: int
    j: int
    name_sim: float
    address_sim: float | None
    account_match: bool
    score: float
    decision: str
    rule: str
    learned: str = ""  # which learned rules influenced this pair, ";"-separated


# --------------------------------------------------------------------------- #
# Similarity functions
# --------------------------------------------------------------------------- #
def text_similarity(a: str, b: str) -> float | None:
    """0..1, order-insensitive. Average of token-sort and token-set ratios:
    token-set alone would say 'BANK' == 'BANK OF AMERICA'."""
    if not a or not b:
        return None
    return (fuzz.token_sort_ratio(a, b) + fuzz.token_set_ratio(a, b)) / 200.0


_NUMBER = re.compile(r"\d+")


def address_similarity(a: str, b: str, penalty: float) -> float | None:
    sim = text_similarity(a, b)
    if sim is None:
        return None
    nums_a, nums_b = set(_NUMBER.findall(a)), set(_NUMBER.findall(b))
    if nums_a and nums_b and not (nums_a & nums_b):
        sim *= penalty
    return sim


# --------------------------------------------------------------------------- #
# Blocking
# --------------------------------------------------------------------------- #
def blocking_keys(rec: PreparedRecord, rules: LearnedRules = NO_RULES) -> set[str]:
    keys = set()
    alias = rules.name_aliases.get(rec.name_core)
    if alias:
        keys.add(f"AL:{alias}")
    if rec.account:
        keys.add(f"A:{rec.account}")
    tokens = rec.name_core.split()
    if tokens:
        keys.add("NF:" + " ".join(sorted(tokens)))
    for t in tokens:
        if len(t) >= 2:
            keys.add(f"NT:{t}")
    return keys


def candidate_pairs(records: list[PreparedRecord], max_block_size: int,
                    rules: LearnedRules = NO_RULES) -> set[tuple[int, int]]:
    blocks: dict[str, list[int]] = defaultdict(list)
    for rec in records:
        for key in blocking_keys(rec, rules):
            blocks[key].append(rec.idx)
    pairs: set[tuple[int, int]] = set()
    for key, members in blocks.items():
        if len(members) < 2:
            continue
        # Exact account / full-name blocks are always compared; token blocks are capped.
        if key.startswith("NT:") and len(members) > max_block_size:
            continue
        pairs.update(combinations(sorted(members), 2))
    return pairs


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def compare(a: PreparedRecord, b: PreparedRecord, cfg: MatchConfig,
            rules: LearnedRules = NO_RULES) -> PairResult:
    learned: list[str] = []
    name = text_similarity(a.name_core, b.name_core) or 0.0
    alias = rules.name_aliases.get(a.name_core)
    if alias and alias == rules.name_aliases.get(b.name_core) and name < 1.0:
        name = 1.0
        learned.append("NAME_ALIAS")
    addr = address_similarity(a.address, b.address, cfg.number_mismatch_penalty)
    acct = bool(a.account) and a.account == b.account
    if acct and a.account in rules.shared_accounts:
        acct = False
        learned.append("SHARED_ACCOUNT")

    if addr is None:
        score = name * cfg.missing_address_factor
    else:
        score = cfg.name_weight * name + (1 - cfg.name_weight) * addr
    if acct:
        score = min(1.0, score + cfg.account_bonus)

    def result(decision: str, rule: str) -> PairResult:
        return PairResult(a.idx, b.idx, round(name, 4),
                          None if addr is None else round(addr, 4),
                          acct, round(score, 4), decision, rule, ";".join(learned))

    if a.country and b.country and a.country != b.country and not acct:
        return result(NO_MATCH, "COUNTRY_CONFLICT")
    if (not acct and (a.name_core in rules.ambiguous_names or b.name_core in rules.ambiguous_names)
            and (addr is None or addr < cfg.address_threshold)):
        learned.append("AMBIGUOUS_NAME")
        return result(REVIEW if score >= cfg.review_threshold else NO_MATCH, "LEARNED_AMBIGUOUS_NAME")
    if acct and name >= cfg.account_name_threshold:
        return result(MATCH, "ACCOUNT+NAME")
    if addr is not None and name >= cfg.strong_name_threshold and addr >= cfg.address_threshold:
        return result(MATCH, "NAME+ADDRESS")
    if score >= cfg.score_threshold and name >= cfg.min_name_for_score:
        return result(MATCH, "WEIGHTED_SCORE")
    if score >= cfg.review_threshold:
        return result(REVIEW, "BELOW_THRESHOLD")
    return result(NO_MATCH, "LOW_SIMILARITY")


def score_candidates(records: list[PreparedRecord], cfg: MatchConfig,
                     rules: LearnedRules | None = None) -> list[PairResult]:
    rules = rules or NO_RULES
    pairs = candidate_pairs(records, cfg.max_block_size, rules)
    return [compare(records[i], records[j], cfg, rules) for i, j in sorted(pairs)]
