"""Local web UI: search entities, inspect why records matched, and correct them.

  python -m party_er serve data/sample_parties.csv --feedback feedback/corrections.json

Standard library only (http.server). Binds to 127.0.0.1 by default and has no
authentication, so do not expose it on a shared network.
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rapidfuzz import fuzz, process

from ..feedback import MERGE, REASONS, SPLIT, FeedbackStore, calibration, suggest_reason
from ..normalize import clean_text, normalize_account, to_str
from ..pipeline import EntityResolver, ResolverConfig, load_previous_mapping, read_table

log = logging.getLogger("party_er.web")
STATIC = Path(__file__).with_name("static")


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


class AppState:
    """The loaded dataset, the corrections, and the latest resolution."""

    def __init__(self, input_path: str, feedback_path: str | None, output_path: str | None = None,
                 previous_path: str | None = None, config: ResolverConfig | None = None) -> None:
        self.config = config or ResolverConfig()
        self.cols = self.config.columns
        self.input_path = input_path
        self.output_path = output_path
        self.df = read_table(input_path)
        self.store = FeedbackStore(feedback_path)
        self.resolver = EntityResolver(self.config)
        self.lock = threading.RLock()
        if previous_path is None and output_path and Path(output_path).exists():
            previous_path = output_path  # keep IDs stable across server restarts
        self.previous = load_previous_mapping(previous_path, self.cols.record_id) if previous_path else None
        self.resolve()

    # ----------------------------------------------------------- resolution
    def resolve(self) -> None:
        with self.lock:
            result = self.resolver.resolve(self.df, self.previous, self.store)
            self.result = result
            recs = result.records
            rid = self.cols.record_id
            self.previous = dict(zip(recs[rid], recs["entity_id"]))
            self.records = {r[rid]: r for r in recs.to_dict("records")}
            self.members: dict[str, list[str]] = {}
            for r in self.records.values():
                self.members.setdefault(r["entity_id"], []).append(r[rid])
            self.pairs = result.pairs.to_dict("records")
            # search index
            self._ids = list(self.records)
            self._names = [clean_text(r[self.cols.name]) for r in self.records.values()]
            self._addresses = [clean_text(r[self.cols.address]) for r in self.records.values()]
            self._accounts: dict[str, list[str]] = {}
            for r in self.records.values():
                acct = normalize_account(r[self.cols.acct])
                if acct:
                    self._accounts.setdefault(acct, []).append(r[rid])
            if self.output_path:
                Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
                recs.to_csv(self.output_path, index=False, encoding="utf-8-sig")

    # --------------------------------------------------------------- views
    def record_view(self, record_id: str) -> dict:
        r, c = self.records[record_id], self.cols
        return {
            "record_id": record_id, "entity_id": r["entity_id"],
            "name": to_str(r[c.name]), "acct": to_str(r[c.acct]), "address": to_str(r[c.address]),
            "country_code": to_str(r[c.country_code]), "country": r["country_code_final"],
            "country_status": r["country_status"], "country_evidence": r["country_evidence"],
            "country_confidence": r["country_confidence"],
            "name_normalized": r["name_normalized"], "party_type": r["party_type"],
        }

    def entity_summary(self, entity_id: str) -> dict:
        members = self.members[entity_id]
        recs = [self.records[m] for m in members]
        names = Counter(to_str(r[self.cols.name]) for r in recs)
        return {
            "entity_id": entity_id, "size": len(members),
            "name": names.most_common(1)[0][0],
            "names": list(names),
            "accounts": sorted({to_str(r[self.cols.acct]) for r in recs} - {""}),
            "countries": sorted({r["country_code_final"] for r in recs} - {""}),
            "party_type": Counter(r["party_type"] for r in recs).most_common(1)[0][0],
            "corrected": any(self._touches(c, set(members)) for c in self.store.corrections),
        }

    @staticmethod
    def _touches(correction, ids: set[str]) -> bool:
        return bool(ids & (set(correction.record_ids) | set(correction.against)))

    def _pair_view(self, p: dict) -> dict:
        out = {k: (None if isinstance(v, float) and v != v else v) for k, v in p.items()}
        out["left_entity_id"] = self.records[p["left_record_id"]]["entity_id"]
        out["right_entity_id"] = self.records[p["right_record_id"]]["entity_id"]
        return out

    def search(self, query: str, limit: int = 50) -> list[dict]:
        q = to_str(query).strip()
        scores: dict[str, float] = {}

        def hit(record_id: str, score: float) -> None:
            eid = self.records[record_id]["entity_id"]
            scores[eid] = max(scores.get(eid, 0.0), score)

        if not q:
            ranked = sorted(self.members, key=lambda e: (-len(self.members[e]), e))
            return [dict(self.entity_summary(e), score=None) for e in ranked[:limit]]
        qu = q.upper()
        if qu in self.members:
            scores[qu] = 100.0
        if q in self.records:
            hit(q, 100.0)
        acct = normalize_account(q)
        if len(acct) >= 3:
            for a, ids in self._accounts.items():
                if a == acct or a.lstrip("0") == acct.lstrip("0"):
                    for rid in ids:
                        hit(rid, 100.0)
                elif len(acct) >= 4 and acct in a:
                    for rid in ids:
                        hit(rid, 85.0)
        qn = clean_text(q)
        if qn:
            for _, score, i in process.extract(qn, self._names, scorer=fuzz.WRatio,
                                               limit=None, score_cutoff=60):
                hit(self._ids[i], float(score))
            if len(qn) >= 3:
                for _, score, i in process.extract(qn, self._addresses, scorer=fuzz.partial_ratio,
                                                   limit=None, score_cutoff=80):
                    hit(self._ids[i], float(score) * 0.8)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return [dict(self.entity_summary(e), score=round(s, 1)) for e, s in ranked]

    def entity_detail(self, entity_id: str) -> dict:
        if entity_id not in self.members:
            raise ApiError(f"unknown entity {entity_id}", HTTPStatus.NOT_FOUND)
        ids = set(self.members[entity_id])
        internal, external = [], []
        for p in self.pairs:
            left, right = p["left_record_id"] in ids, p["right_record_id"] in ids
            if left and right:
                internal.append(self._pair_view(p))
            elif left or right:
                external.append(self._pair_view(p))
        external.sort(key=lambda p: -p["score"])
        return {
            **self.entity_summary(entity_id),
            "records": [self.record_view(r) for r in sorted(ids)],
            "links": internal,
            "near": external,
            "corrections": [self.correction_view(c) for c in self.store.corrections
                            if self._touches(c, ids)],
        }

    def review_queue(self, limit: int = 200) -> list[dict]:
        out = []
        for p in self.pairs:
            if p["decision"] != "REVIEW" or p["feedback"]:
                continue
            view = self._pair_view(p)
            if view["left_entity_id"] == view["right_entity_id"]:
                continue
            view["left"] = self.record_view(p["left_record_id"])
            view["right"] = self.record_view(p["right_record_id"])
            out.append(view)
        out.sort(key=lambda p: -p["score"])
        return out[:limit]

    def correction_view(self, c) -> dict:
        label, effect = REASONS[c.action][c.reason]
        return {
            "id": c.id, "action": c.action, "record_ids": c.record_ids, "against": c.against,
            "reason": c.reason, "reason_label": label, "learned": effect,
            "explanation": c.explanation, "author": c.author, "created": c.created,
            "names": {rid: s.get("name", "") for rid, s in c.snapshot.items()},
            "entities": sorted({self.records[r]["entity_id"] for r in c.record_ids + c.against
                                if r in self.records}),
        }

    def learning(self) -> dict:
        return {
            "corrections": [self.correction_view(c) for c in reversed(self.store.corrections)],
            "rules": self.store.rules_summary(),
            "calibration": calibration(self.result.labelled, self.config.match.score_threshold),
            "labelled": self.result.labelled.to_dict("records"),
        }

    def summary(self) -> dict:
        recs = self.result.records
        return {
            "input": self.input_path, "records": len(recs), "entities": len(self.members),
            "multi_record_entities": sum(1 for m in self.members.values() if len(m) > 1),
            "review_pairs": len(self.review_queue(limit=10**9)),
            "corrections": len(self.store),
            "feedback_file": str(self.store.path) if self.store.path else None,
            "reasons": {a: {k: {"label": v[0], "learned": v[1]} for k, v in rs.items()}
                        for a, rs in REASONS.items()},
        }

    # ---------------------------------------------------------- corrections
    def correct(self, body: dict) -> dict:
        action = to_str(body.get("action")).lower()
        record_ids = [to_str(r) for r in body.get("record_ids") or []]
        against = [to_str(r) for r in body.get("against") or []]
        explanation = to_str(body.get("explanation"))
        reason = to_str(body.get("reason")) or None
        author = to_str(body.get("author"))
        with self.lock:
            if body.get("against_entity"):
                eid = to_str(body["against_entity"])
                if eid not in self.members:
                    raise ApiError(f"unknown entity {eid}")
                against = [r for r in self.members[eid] if r not in record_ids]
            unknown = [r for r in record_ids + against if r not in self.records]
            if unknown:
                raise ApiError(f"unknown record_id(s): {unknown[:10]}")
            lookup = {r: (self.records[r][self.cols.name], self.records[r][self.cols.acct])
                      for r in record_ids + against}
            made = []
            try:
                if action == "move":
                    # detach from the current entity, then merge into the target
                    current = {self.records[r]["entity_id"] for r in record_ids}
                    rest = [m for e in current for m in self.members[e] if m not in record_ids]
                    if rest:
                        lookup.update({r: (self.records[r][self.cols.name], self.records[r][self.cols.acct])
                                       for r in rest})
                        made.append(self.store.add(SPLIT, record_ids, rest, explanation, lookup,
                                                   _split_reason(reason), author))
                    made.append(self.store.add(MERGE, record_ids, against, explanation, lookup,
                                               _merge_reason(reason), author))
                elif action in (MERGE, SPLIT):
                    made.append(self.store.add(action, record_ids, against, explanation, lookup,
                                               reason, author))
                else:
                    raise ApiError("action must be merge, split or move")
            except ValueError as exc:
                for c in made:
                    self.store.remove(c.id)
                raise ApiError(str(exc)) from exc
            self.resolve()
            return {
                "corrections": [self.correction_view(c) for c in made],
                "entity_ids": {r: self.records[r]["entity_id"] for r in record_ids + against},
            }

    def undo(self, correction_id: str) -> dict:
        with self.lock:
            try:
                c = self.store.remove(correction_id)
            except KeyError:
                raise ApiError(f"unknown correction {correction_id}", HTTPStatus.NOT_FOUND) from None
            self.resolve()
            return {"removed": c.id,
                    "entity_ids": {r: self.records[r]["entity_id"] for r in c.record_ids + c.against
                                   if r in self.records}}


def _split_reason(reason: str | None) -> str | None:
    return reason if reason in REASONS[SPLIT] else None


def _merge_reason(reason: str | None) -> str | None:
    return reason if reason in REASONS[MERGE] else None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    routes = [
        ("GET", r"/api/summary", lambda m, q, b: state.summary()),
        ("GET", r"/api/search", lambda m, q, b: {"results": state.search(q.get("q", ""),
                                                                         int(q.get("limit", 50)))}),
        ("GET", r"/api/entity/([^/]+)", lambda m, q, b: state.entity_detail(m.group(1))),
        ("GET", r"/api/review", lambda m, q, b: {"pairs": state.review_queue()}),
        ("GET", r"/api/corrections", lambda m, q, b: state.learning()),
        ("POST", r"/api/corrections", lambda m, q, b: state.correct(b)),
        ("DELETE", r"/api/corrections/([^/]+)", lambda m, q, b: state.undo(m.group(1))),
        ("GET", r"/api/suggest-reason",
         lambda m, q, b: {"reason": suggest_reason(q.get("action", MERGE), q.get("text", ""))}),
    ]

    class Handler(BaseHTTPRequestHandler):
        server_version = "party_er"

        def log_message(self, fmt, *args):  # quieter than stderr spam
            log.info("%s - %s", self.address_string(), fmt % args)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = HTTPStatus.OK):
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _dispatch(self, method: str):
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if method == "GET" and path in ("/", "/index.html"):
                    body = (STATIC / "index.html").read_bytes()
                    return self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                if method == "GET" and path == "/api/export":
                    buf = io.StringIO()
                    with state.lock:
                        state.result.records.to_csv(buf, index=False)
                    return self._send(HTTPStatus.OK, buf.getvalue().encode("utf-8-sig"),
                                      "text/csv; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="resolved.csv"'})
                body = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ApiError("Content-Type must be application/json",
                                       HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                    body = json.loads(self.rfile.read(length) or b"{}")
                for m, pattern, fn in routes:
                    match = re.fullmatch(pattern, path)
                    if m == method and match:
                        with state.lock:
                            return self._json(fn(match, query, body))
                raise ApiError("not found", HTTPStatus.NOT_FOUND)
            except ApiError as exc:
                self._json({"error": str(exc)}, exc.status)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover - logged for the operator
                log.exception("request failed")
                self._json({"error": f"internal error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def serve(state: AppState, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"party_er UI on http://{host}:{port}  ({len(state.records)} records, "
          f"{len(state.members)} entities, {len(state.store)} corrections)  Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
