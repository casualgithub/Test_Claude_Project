"""Infer the country of a free-text address.

Approach: evidence scoring. Every signal found in the address (country name,
postal-code format, state/province, city, script, street vocabulary, trailing
ISO code) adds points to one or more candidate countries. The country with the
most points wins; confidence reflects both how much evidence there is and how
far ahead the winner is of the runner-up. Every point is recorded as evidence
so the decision is explainable / auditable.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..normalize import clean_text, fold_accents, to_str
from . import reference as ref

_SEGMENT_SPLIT = re.compile(r"[,\n;|]+")

# Weights that depend on position / context.
COUNTRY_TAIL_WEIGHT = 10      # country name at the end of the address
COUNTRY_INNER_WEIGHT = 4      # country name in the middle (could be a street name)
STREETY_WEIGHT = 0.5          # name followed by ST/ROAD/HOUSE... -> street, not place
ISO2_WEIGHT = 5
ISO2_AMBIGUOUS_WEIGHT = 3     # ISO2 that is also a US state / CA province code (CA, IN, DE...)
ISO3_WEIGHT = 6
STATE_ABBR_WEIGHT = 3
JP_POSTAL_MARK_WEIGHT = 9


@dataclass
class CountryResult:
    country_code: str | None
    confidence: float
    evidence: list[str] = field(default_factory=list)          # evidence for the winner
    scores: dict[str, float] = field(default_factory=dict)      # all candidates
    runner_up: str | None = None

    def evidence_str(self) -> str:
        return "; ".join(self.evidence)


class _Scorer:
    def __init__(self) -> None:
        # iso2 -> {reason: weight}; same reason counted once (no inflation by repetition)
        self._evidence: dict[str, dict[str, float]] = {}

    def add(self, iso2: str, weight: float, reason: str) -> None:
        if weight <= 0:
            return
        slot = self._evidence.setdefault(iso2, {})
        slot[reason] = max(weight, slot.get(reason, 0))

    def scores(self) -> dict[str, float]:
        return {iso: round(sum(r.values()), 3) for iso, r in self._evidence.items()}

    def evidence_for(self, iso2: str) -> list[str]:
        items = sorted(self._evidence.get(iso2, {}).items(), key=lambda kv: -kv[1])
        return [f"{reason}(+{w:g})" for reason, w in items]


class CountryDetector:
    def __init__(self, min_confidence: float = 0.3, saturation: float = 6.0) -> None:
        """
        min_confidence: below this the detector returns country_code=None.
        saturation:     evidence points at which confidence reaches ~63% (before margin).
        """
        self.min_confidence = min_confidence
        self.saturation = saturation
        self.phrases = ref.phrase_table()
        self.max_phrase_len = max(len(p.split()) for p in self.phrases)

    # ------------------------------------------------------------------ #
    def detect(self, address) -> CountryResult:
        raw = to_str(address)
        if not raw.strip():
            return CountryResult(None, 0.0)

        sc = _Scorer()
        self._score_scripts(raw, sc)
        self._score_postal(raw, sc)
        segments = [s for s in _SEGMENT_SPLIT.split(raw) if clean_text(s)]
        last_consumed = self._score_phrases(segments, sc)
        self._score_codes(segments, sc, last_consumed)
        return self._result(sc)

    # ------------------------------------------------------------------ #
    def _score_scripts(self, raw: str, sc: _Scorer) -> None:
        kana_found = False
        for pattern, scores, label in ref.SCRIPT_RULES:
            if pattern.search(raw):
                kana_found |= label == "japanese_kana"
                for iso, w in scores.items():
                    sc.add(iso, w, f"script:{label}")
        if not kana_found and ref.HAN.search(raw):
            for iso, w in {"CN": 3, "TW": 1.5, "HK": 1.5}.items():
                sc.add(iso, w, "script:han")

    def _score_postal(self, raw: str, sc: _Scorer) -> None:
        if ref.JP_POSTAL_MARK.search(raw):
            sc.add("JP", JP_POSTAL_MARK_WEIGHT, "postal:jp_postal_mark")
        upper = fold_accents(raw).upper()
        for pattern, scores, label in ref.POSTAL_RULES:
            m = pattern.search(upper)
            if m:
                for iso, w in scores.items():
                    sc.add(iso, w, f"postal:{label}[{m.group(0).strip()}]")

    def _score_phrases(self, segments: list[str], sc: _Scorer) -> bool:
        """Greedy longest-match of known phrases. Returns True if the very last
        token of the address was consumed by a phrase."""
        seg_tokens = [clean_text(s).split() for s in segments]
        total = sum(len(t) for t in seg_tokens)
        multi_segment = len(seg_tokens) > 1
        offset = 0
        last_consumed = False

        for si, toks in enumerate(seg_tokens):
            is_last_seg = si == len(seg_tokens) - 1
            i = 0
            while i < len(toks):
                entries, n = None, 0
                for n in range(min(self.max_phrase_len, len(toks) - i), 0, -1):
                    entries = self.phrases.get(" ".join(toks[i:i + n]))
                    if entries:
                        break
                if not entries:
                    self._score_keyword(toks[i], sc)
                    i += 1
                    continue

                phrase = " ".join(toks[i:i + n])
                end = offset + i + n
                tail = (is_last_seg and multi_segment) or end >= total - 2
                nxt = toks[i + n] if i + n < len(toks) else None
                streety = nxt in ref.STREET_WORDS
                # "GEORGIA" is a country and a US state: halve the country weight
                # so the other evidence (city, ZIP) decides.
                ambiguous = len({iso for iso, _, _ in entries}) > 1

                best: dict[str, tuple[float, str]] = {}
                for iso, kind, base in entries:
                    if streety:
                        w = STREETY_WEIGHT
                    elif kind == "country":
                        w = COUNTRY_TAIL_WEIGHT if tail else COUNTRY_INNER_WEIGHT
                        if ambiguous:
                            w /= 2
                    else:
                        w = base
                    if w > best.get(iso, (0, ""))[0]:
                        best[iso] = (w, f"{kind}:{phrase}")
                for iso, (w, reason) in best.items():
                    sc.add(iso, w, reason)

                if is_last_seg and i + n == len(toks):
                    last_consumed = True
                i += n
            offset += len(toks)
        return last_consumed

    @staticmethod
    def _score_keyword(token: str, sc: _Scorer) -> None:
        if token in ref.KEYWORDS:
            for iso, w in ref.KEYWORDS[token].items():
                sc.add(iso, w, f"keyword:{token}")
            return
        for suffix, scores in ref.SUFFIX_KEYWORDS.items():
            if len(token) > len(suffix) + 2 and token.endswith(suffix):
                for iso, w in scores.items():
                    sc.add(iso, w, f"keyword:*{suffix}")
                return

    def _score_codes(self, segments: list[str], sc: _Scorer, last_consumed: bool) -> None:
        """2/3-letter codes. Only UPPERCASE tokens count, to avoid words like 'in', 'de'."""
        # State / province abbreviations at the end of one of the last two segments,
        # or immediately before a postal code: "Austin, TX" / "Mississauga ON L5N 0A1".
        for seg in segments[-2:]:
            toks = clean_text(seg, upper=False).split()
            for k, t in enumerate(toks):
                if len(t) != 2 or not t.isalpha() or not t.isupper():
                    continue
                at_end = k == len(toks) - 1
                before_postal = k + 1 < len(toks) and any(ch.isdigit() for ch in toks[k + 1])
                if not (at_end or before_postal):
                    continue
                if t in ref.US_STATE_ABBR:
                    sc.add("US", STATE_ABBR_WEIGHT, f"us_state_abbr:{t}")
                if t in ref.CA_PROVINCE_ABBR:
                    sc.add("CA", STATE_ABBR_WEIGHT, f"ca_province_abbr:{t}")

        # Trailing ISO code: "..., Berlin, DE" / "..., GBR"
        if last_consumed or not segments:
            return
        toks = clean_text(segments[-1], upper=False).split()
        if not toks:
            return
        t = toks[-1]
        if not (t.isalpha() and t.isupper()):
            return
        if len(t) == 3 and t in ref.ISO3_TO_ISO2:
            sc.add(ref.ISO3_TO_ISO2[t], ISO3_WEIGHT, f"iso3_code:{t}")
        elif len(t) == 2 and t in ref.ISO2:
            ambiguous = t in ref.US_STATE_ABBR or t in ref.CA_PROVINCE_ABBR
            sc.add(t, ISO2_AMBIGUOUS_WEIGHT if ambiguous else ISO2_WEIGHT, f"iso2_code:{t}")

    # ------------------------------------------------------------------ #
    def _result(self, sc: _Scorer) -> CountryResult:
        scores = sc.scores()
        if not scores:
            return CountryResult(None, 0.0)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top_iso, top = ranked[0]
        runner_up, second = ranked[1] if len(ranked) > 1 else (None, 0.0)
        strength = 1 - math.exp(-top / self.saturation)
        margin = (top - second) / top
        confidence = round(strength * margin, 3)
        code = top_iso if confidence >= self.min_confidence else None
        return CountryResult(code, confidence, sc.evidence_for(top_iso), scores, runner_up)


# --------------------------------------------------------------------------- #
# Input code normalisation and reconciliation with the inferred country
# --------------------------------------------------------------------------- #
def normalize_country_code(value) -> str | None:
    """Accept ISO2, ISO3 or a country name/alias; return ISO2 or None if unrecognised."""
    s = clean_text(value)
    if not s:
        return None
    if len(s) == 2 and s in ref.ISO2:
        return s
    if s == "UK":
        return "GB"
    if len(s) == 3 and s in ref.ISO3_TO_ISO2:
        return ref.ISO3_TO_ISO2[s]
    return ref.country_name_index().get(s)


CONFLICT_POLICIES = ("confidence", "input", "address")


def reconcile_country(
    input_value,
    detected: CountryResult,
    policy: str = "confidence",
    override_confidence: float = 0.7,
) -> tuple[str, str]:
    """Combine the supplied country code with the address-derived one.

    Returns (final_iso2_or_empty, status). Status is one of:
      MATCHED                 supplied code agrees with address
      INPUT_ONLY              supplied code used, address had no clear country
      INFERRED                no supplied code, inferred from address
      INFERRED_INVALID_INPUT  supplied code unrecognised, inferred from address
      CONFLICT_KEPT_INPUT     disagreement, supplied code kept
      CONFLICT_USED_ADDRESS   disagreement, address country used
      INVALID_INPUT           supplied code unrecognised, nothing inferred
      UNKNOWN                 nothing supplied, nothing inferred
    """
    if policy not in CONFLICT_POLICIES:
        raise ValueError(f"policy must be one of {CONFLICT_POLICIES}")
    supplied = normalize_country_code(input_value)
    had_input = bool(clean_text(input_value))
    inferred = detected.country_code

    if supplied and inferred:
        if supplied == inferred:
            return supplied, "MATCHED"
        use_address = policy == "address" or (
            policy == "confidence" and detected.confidence >= override_confidence
        )
        return (inferred, "CONFLICT_USED_ADDRESS") if use_address else (supplied, "CONFLICT_KEPT_INPUT")
    if supplied:
        return supplied, "INPUT_ONLY"
    if inferred:
        return inferred, ("INFERRED_INVALID_INPUT" if had_input else "INFERRED")
    return "", ("INVALID_INPUT" if had_input else "UNKNOWN")
