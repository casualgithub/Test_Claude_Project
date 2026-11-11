"""User corrections and what the resolver learns from them.

A correction says, for two groups of records:
  merge -> the records in `record_ids` are the same party as those in `against`
  split -> the records in `record_ids` are NOT the same party as those in `against`
It always carries a reason code and the user's free-text explanation.
Corrections live in a JSON file and are re-applied on every run, in two ways:

1. Pair constraints (exact). Merge -> must-link, split -> cannot-link. The
   clustering honours them whatever the scores say, including through
   transitive matches. A later correction overrides an earlier one on the same pair.
2. Learned rules (generalise to records nobody corrected), chosen by reason:
     name_alias                 the names are treated as identical everywhere
                                (name_sim 1.0, and blocked together)
     shared_account             that account stops counting as evidence of identity
     same_name_different_party  that name alone is not enough: a match also needs
                                a shared account or a similar address

Deleting a correction removes both effects. Rules are rebuilt from a snapshot of
the names/accounts taken when the correction was made, so they keep working
on later extracts that no longer contain the original records.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .matching import LearnedRules, text_similarity
from .normalize import normalize_account, normalize_name, to_str

MERGE, SPLIT = "merge", "split"

# reason -> (label, what the resolver learns beyond the pair itself)
REASONS: dict[str, dict[str, tuple[str, str]]] = {
    MERGE: {
        "name_alias": (
            "Different names of the same party (abbreviation, trading name, former name, transliteration)",
            "These names are treated as the same name everywhere, for all records.",
        ),
        "same_party": (
            "Same party, details differ (moved address, typo, new account)",
            "Only these records are linked.",
        ),
    },
    SPLIT: {  # order = tie-break order for suggest_reason (most specific first)
        "shared_account": (
            "Account is shared by different parties (joint, nominee, omnibus, custodian)",
            "This account no longer counts as evidence that two records are the same party.",
        ),
        "same_name_different_party": (
            "Same or similar name, but a different party",
            "These names no longer match on name alone: a shared account or similar address is required.",
        ),
        "different_party": (
            "Different party (other reason)",
            "Only these records are kept apart.",
        ),
    },
}
DEFAULT_REASON = {MERGE: "same_party", SPLIT: "different_party"}

# Keywords that hint at a reason in a free-text explanation (lowercase, punctuation -> space).
_REASON_KEYWORDS = {
    "name_alias": (
        "aka", "a k a", "also known", "known as", "trading as", "t/a", "dba", "d/b/a", "doing business",
        "formerly", "former name", "renamed", "rebrand", "rebranded", "abbreviation", "abbreviated",
        "acronym", "short for", "alias", "brand", "transliteration", "spelled", "spelling", "nickname",
    ),
    "same_party": (
        "moved", "relocated", "new address", "old address", "typo", "misspelled", "new account",
        "same person", "same company", "same customer", "same party",
    ),
    "shared_account": (
        "joint", "shared", "nominee", "omnibus", "custodian", "custody", "pooled", "household",
        "same account", "on behalf", "trust account", "escrow", "family account",
    ),
    "same_name_different_party": (
        "same name", "common name", "namesake", "homonym", "different person", "different people",
        "different company", "father", "son", "jr", "sr", "junior", "senior",
    ),
}
_KW_CLEAN = re.compile(r"[^a-z0-9/]+")


def suggest_reason(action: str, explanation: str) -> str:
    """Best reason code for an explanation, by keyword hits (default if none)."""
    if action not in REASONS:
        raise ValueError(f"action must be one of {sorted(REASONS)}")
    text = f" {_KW_CLEAN.sub(' ', to_str(explanation).lower()).strip()} "
    best, best_hits = DEFAULT_REASON[action], 0
    for reason in REASONS[action]:
        hits = sum(1 for kw in _REASON_KEYWORDS.get(reason, ()) if f" {kw} " in text)
        if hits > best_hits:
            best, best_hits = reason, hits
    return best


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


@dataclass
class Correction:
    id: str
    action: str
    record_ids: list[str]
    against: list[str]
    reason: str
    explanation: str
    created: str
    author: str = ""
    # record_id -> {"name", "name_core", "account"} at the time of the correction
    snapshot: dict[str, dict[str, str]] = field(default_factory=dict)

    def _cores(self, ids: list[str]) -> set[str]:
        return {self.snapshot[r]["name_core"] for r in ids
                if self.snapshot.get(r, {}).get("name_core")}

    def _accounts(self, ids: list[str]) -> set[str]:
        return {self.snapshot[r]["account"] for r in ids
                if self.snapshot.get(r, {}).get("account")}


class FeedbackStore:
    """Corrections persisted as JSON. `path=None` keeps them in memory only."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else None
        self.corrections: list[Correction] = []
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.corrections = [Correction(**c) for c in data.get("corrections", [])]

    def __len__(self) -> int:
        return len(self.corrections)

    # ------------------------------------------------------------------ edit
    def add(self, action: str, record_ids: list[str], against: list[str], explanation: str,
            records: dict[str, tuple[str, str]], reason: str | None = None,
            author: str = "") -> Correction:
        """Record a correction. `records` maps record_id -> (raw name, raw account)
        for at least every record involved; it is snapshotted for rule learning."""
        if action not in REASONS:
            raise ValueError(f"action must be one of {sorted(REASONS)}")
        explanation = to_str(explanation).strip()
        if not explanation:
            raise ValueError("an explanation is required: it is what the resolver learns from")
        reason = reason or suggest_reason(action, explanation)
        if reason not in REASONS[action]:
            raise ValueError(f"reason for {action} must be one of {sorted(REASONS[action])}")
        record_ids = list(dict.fromkeys(to_str(r) for r in record_ids))
        against = [a for a in dict.fromkeys(to_str(r) for r in against) if a not in record_ids]
        if not record_ids or not against:
            raise ValueError("both record_ids and against need at least one record (and must differ)")
        missing = [r for r in record_ids + against if r not in records]
        if missing:
            raise ValueError(f"unknown record_id(s): {missing[:10]}")

        snapshot = {}
        for rid in record_ids + against:
            name, acct = records[rid]
            snapshot[rid] = {"name": to_str(name), "name_core": normalize_name(name).core,
                             "account": normalize_account(acct)}
        number = max((int(c.id.split("-")[-1]) for c in self.corrections), default=0) + 1
        correction = Correction(
            id=f"FB-{number:04d}", action=action, record_ids=record_ids, against=against,
            reason=reason, explanation=explanation, author=to_str(author).strip(),
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"), snapshot=snapshot,
        )
        self.corrections.append(correction)
        self.save()
        return correction

    def remove(self, correction_id: str) -> Correction:
        for i, c in enumerate(self.corrections):
            if c.id == correction_id:
                del self.corrections[i]
                self.save()
                return c
        raise KeyError(correction_id)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "corrections": [asdict(c) for c in self.corrections]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ----------------------------------------------------------------- learn
    def constraints(self) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        """(must_link, cannot_link) record_id pairs; later corrections win."""
        must: set[tuple[str, str]] = set()
        cannot: set[tuple[str, str]] = set()
        for c in self.corrections:
            if c.action == MERGE:
                for a in c.record_ids:
                    for b in c.against:
                        cannot.discard(_pair(a, b))
                    must.add(_pair(a, c.against[0]))
            else:
                for a in c.record_ids:
                    for b in c.against:
                        must.discard(_pair(a, b))
                        cannot.add(_pair(a, b))
                # records detached together stay together
                for a, b in zip(c.record_ids, c.record_ids[1:]):
                    cannot.discard(_pair(a, b))
                    must.add(_pair(a, b))
        return must, cannot

    def rules(self) -> LearnedRules:
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        shared: set[str] = set()
        ambiguous: set[str] = set()
        for c in self.corrections:
            if c.action == MERGE and c.reason == "name_alias":
                cores = sorted(c._cores(c.record_ids + c.against))
                for other in cores[1:]:
                    ra, rb = find(cores[0]), find(other)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
            elif c.action == SPLIT and c.reason == "shared_account":
                shared |= c._accounts(c.record_ids) & c._accounts(c.against)
            elif c.action == SPLIT and c.reason == "same_name_different_party":
                mine = c._cores(c.record_ids)
                ambiguous |= mine
                ambiguous |= {o for o in c._cores(c.against)
                              if any((text_similarity(o, m) or 0) >= 0.85 for m in mine)}

        aliases = {core: find(core) for core in parent}
        sizes: dict[str, int] = {}
        for root in aliases.values():
            sizes[root] = sizes.get(root, 0) + 1
        aliases = {core: root for core, root in aliases.items() if sizes[root] > 1}
        return LearnedRules(name_aliases=aliases, shared_accounts=frozenset(shared),
                            ambiguous_names=frozenset(ambiguous))

    def rules_summary(self) -> dict:
        rules = self.rules()
        groups: dict[str, list[str]] = {}
        for core, root in rules.name_aliases.items():
            groups.setdefault(root, []).append(core)
        return {
            "name_aliases": sorted(sorted(g) for g in groups.values()),
            "shared_accounts": sorted(rules.shared_accounts),
            "ambiguous_names": sorted(rules.ambiguous_names),
        }


def calibration(labelled, current_threshold: float, min_labels: int = 5) -> dict:
    """How well the model agrees with the user's labelled pairs, and the
    weighted-score threshold that would have agreed best. `labelled` is
    ResolutionResult.labelled."""
    n = len(labelled)
    out = {"labelled_pairs": n, "base_agreement": None, "learned_agreement": None,
           "current_score_threshold": current_threshold, "suggested_score_threshold": None}
    if not n:
        return out
    same = labelled["label"] == "SAME"
    out["base_agreement"] = round(float(((labelled["base_decision"] == "MATCH") == same).mean()), 3)
    out["learned_agreement"] = round(float(((labelled["learned_decision"] == "MATCH") == same).mean()), 3)
    if n >= min_labels and same.any() and (~same).any():
        scores = labelled["base_score"].astype(float)
        candidates = sorted(set(scores.round(2))) + [current_threshold]
        best = max(candidates, key=lambda t: (((scores >= t) == same).mean(), -abs(t - current_threshold)))
        out["suggested_score_threshold"] = round(float(best), 2)
    return out
