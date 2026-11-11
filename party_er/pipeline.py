"""End-to-end entity resolution over a pandas DataFrame."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from .clustering import assign_entity_ids, constrained_components
from .country import CountryDetector, reconcile_country
from .matching import MATCH, NO_MATCH, NO_RULES, REVIEW, MatchConfig, PreparedRecord, compare, score_candidates

if TYPE_CHECKING:
    from .feedback import FeedbackStore

PAIR_COLUMNS = ["left_record_id", "right_record_id", "left_name", "right_name",
                "name_sim", "address_sim", "account_match", "score", "decision",
                "rule", "entity_id", "model_decision", "learned", "feedback"]
LABELLED_COLUMNS = ["left_record_id", "right_record_id", "label", "base_score",
                    "base_decision", "base_rule", "learned_decision", "learned_rule"]
from .normalize import normalize_account, normalize_address, normalize_name, to_str


@dataclass
class ColumnMap:
    record_id: str = "record_id"
    name: str = "name"
    acct: str = "acct"
    address: str = "address"
    country_code: str = "country_code"


@dataclass
class ResolverConfig:
    columns: ColumnMap = field(default_factory=ColumnMap)
    match: MatchConfig = field(default_factory=MatchConfig)
    min_country_confidence: float = 0.3
    # How to settle "supplied country_code disagrees with the address":
    #   confidence -> use address country if its confidence >= override_confidence
    #   input      -> always keep the supplied code
    #   address    -> always use the address country
    conflict_policy: str = "confidence"
    override_confidence: float = 0.7
    entity_prefix: str = "ENT"


@dataclass
class ResolutionResult:
    records: pd.DataFrame  # input rows + country + entity columns
    pairs: pd.DataFrame    # MATCH and REVIEW pairs with scores (explainability)
    # one row per user-labelled pair: what the model says with and without learned rules
    labelled: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=LABELLED_COLUMNS))

    @property
    def entity_count(self) -> int:
        return self.records["entity_id"].nunique()


class EntityResolver:
    def __init__(self, config: ResolverConfig | None = None) -> None:
        self.config = config or ResolverConfig()
        self.detector = CountryDetector(min_confidence=self.config.min_country_confidence)

    def resolve(self, df: pd.DataFrame, previous: dict[str, str] | None = None,
                feedback: FeedbackStore | None = None) -> ResolutionResult:
        """`feedback` (user corrections) adds must/cannot-link constraints and learned rules."""
        cfg, cols = self.config, self.config.columns
        df = df.copy()
        for col in (cols.name, cols.acct, cols.address, cols.country_code):
            if col not in df.columns:
                df[col] = ""
        if cols.record_id not in df.columns:
            df.insert(0, cols.record_id, [f"R{i + 1:07d}" for i in range(len(df))])
        df[cols.record_id] = df[cols.record_id].map(to_str).str.strip()
        dupes = df[cols.record_id][df[cols.record_id].duplicated()].unique()
        if len(dupes):
            raise ValueError(f"record_id must be unique; duplicates: {list(dupes[:10])}")

        # 1. Country: infer from address, reconcile with supplied code ----------
        detections = [self.detector.detect(a) for a in df[cols.address]]
        reconciled = [
            reconcile_country(code, det, cfg.conflict_policy, cfg.override_confidence)
            for code, det in zip(df[cols.country_code], detections)
        ]
        df["country_code_inferred"] = [d.country_code or "" for d in detections]
        df["country_confidence"] = [d.confidence for d in detections]
        df["country_evidence"] = [d.evidence_str() for d in detections]
        df["country_code_final"] = [r[0] for r in reconciled]
        df["country_status"] = [r[1] for r in reconciled]

        # 2. Normalize ----------------------------------------------------------
        names = [normalize_name(n) for n in df[cols.name]]
        df["name_normalized"] = [n.core for n in names]
        df["party_type"] = [n.party_type for n in names]
        record_ids = df[cols.record_id].tolist()
        prepared = [
            PreparedRecord(
                idx=i,
                record_id=record_ids[i],
                name_core=names[i].core,
                account=normalize_account(acct),
                address=normalize_address(addr),
                country=country,
            )
            for i, (acct, addr, country) in enumerate(
                zip(df[cols.acct], df[cols.address], df["country_code_final"])
            )
        ]

        # 3. Block, compare, cluster (honouring user corrections) ---------------
        rules = feedback.rules() if feedback is not None else NO_RULES
        must_ids, cannot_ids = feedback.constraints() if feedback is not None else (set(), set())
        index = {rid: i for i, rid in enumerate(record_ids)}

        def to_idx(pairs_by_id: set[tuple[str, str]]) -> set[tuple[int, int]]:
            out = set()
            for a, b in pairs_by_id:
                if a in index and b in index:
                    i, j = index[a], index[b]
                    out.add((min(i, j), max(i, j)))
            return out

        must, cannot = to_idx(must_ids), to_idx(cannot_ids)
        results = score_candidates(prepared, cfg.match, rules)
        by_pair = {(r.i, r.j): r for r in results}
        for i, j in sorted((must | cannot) - by_pair.keys()):
            by_pair[(i, j)] = compare(prepared[i], prepared[j], cfg.match, rules)
        matches = sorted((r for r in by_pair.values() if r.decision == MATCH and (r.i, r.j) not in cannot),
                         key=lambda r: (-r.score, r.i, r.j))
        clusters, rejected = constrained_components(
            len(prepared), [(r.i, r.j) for r in matches], sorted(must), cannot)
        entity_ids = assign_entity_ids(clusters, record_ids, previous, cfg.entity_prefix)

        df["entity_id"] = entity_ids
        df["entity_size"] = df.groupby("entity_id")["entity_id"].transform("size")

        rows = []
        for key in sorted(by_pair):
            r = by_pair[key]
            decision, note = r.decision, ""
            if key in cannot:
                decision, note = NO_MATCH, "USER_SPLIT"
            elif key in must:
                decision, note = (NO_MATCH, "USER_MERGE_CONFLICT") if key in rejected else (MATCH, "USER_MERGE")
            elif key in rejected:
                decision, note = NO_MATCH, "BLOCKED_BY_FEEDBACK"
            if decision not in (MATCH, REVIEW) and not note:
                continue
            rows.append({
                "left_record_id": record_ids[r.i],
                "right_record_id": record_ids[r.j],
                "left_name": to_str(df[cols.name].iat[r.i]),
                "right_name": to_str(df[cols.name].iat[r.j]),
                "name_sim": r.name_sim,
                "address_sim": r.address_sim,
                "account_match": r.account_match,
                "score": r.score,
                "decision": decision,
                "rule": r.rule,
                "entity_id": entity_ids[r.i] if decision == MATCH else "",
                "model_decision": r.decision,
                "learned": r.learned,
                "feedback": note,
            })
        pairs = pd.DataFrame(rows, columns=PAIR_COLUMNS)

        labelled = []
        for key in sorted(must | cannot):
            i, j = key
            base = compare(prepared[i], prepared[j], cfg.match)
            learned = by_pair[key]
            labelled.append({
                "left_record_id": record_ids[i], "right_record_id": record_ids[j],
                "label": "DIFFERENT" if key in cannot else "SAME",
                "base_score": base.score, "base_decision": base.decision, "base_rule": base.rule,
                "learned_decision": learned.decision, "learned_rule": learned.rule,
            })
        labelled_df = pd.DataFrame(labelled, columns=LABELLED_COLUMNS)
        return ResolutionResult(df, pairs, labelled_df)


def load_previous_mapping(path: str, record_col: str = "record_id",
                          entity_col: str = "entity_id") -> dict[str, str]:
    """Load record_id -> entity_id from a previous run's output (keeps IDs stable)."""
    prev = read_table(path)
    return dict(zip(prev[record_col].map(to_str), prev[entity_col].map(to_str)))


def read_table(path: str) -> pd.DataFrame:
    """Read CSV/XLSX as strings. keep_default_na=False so 'NA' (Namibia) survives."""
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
