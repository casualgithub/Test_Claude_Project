"""Command line interface.

  python -m party_er resolve data/sample_parties.csv -o output/resolved.csv --pairs output/pairs.csv
  python -m party_er country "15 Rue de Rivoli, 75001 Paris"
  python -m party_er serve data/sample_parties.csv --feedback feedback/corrections.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .country import CONFLICT_POLICIES, CountryDetector
from .feedback import FeedbackStore
from .pipeline import ColumnMap, EntityResolver, ResolverConfig, load_previous_mapping, read_table


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="party_er", description="Party entity resolution")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("resolve", help="assign entity ids to a CSV/XLSX of party records")
    r.add_argument("input")
    r.add_argument("-o", "--output", required=True, help="output CSV of resolved records")
    r.add_argument("--pairs", help="optional CSV of matched / review pairs with scores")
    r.add_argument("--previous", help="previous run's output, to keep entity ids stable")
    r.add_argument("--feedback", help="corrections JSON (from the web UI) to apply and learn from")
    _add_common(r)

    s = sub.add_parser("serve", help="web UI to search entities and correct them")
    s.add_argument("input")
    s.add_argument("--feedback", default="feedback/corrections.json",
                   help="corrections JSON, created if missing (default: %(default)s)")
    s.add_argument("-o", "--output", help="rewrite this resolved CSV after every correction; "
                   "if it exists at start-up it also seeds stable entity ids")
    s.add_argument("--previous", help="previous run's output, to keep entity ids stable")
    s.add_argument("--host", default="127.0.0.1", help="default: %(default)s (local only, no auth)")
    s.add_argument("--port", type=int, default=8000)
    _add_common(s)

    c = sub.add_parser("country", help="infer the country of one or more addresses")
    c.add_argument("address", nargs="+")
    return p


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--conflict-policy", choices=CONFLICT_POLICIES, default="confidence")
    p.add_argument("--override-confidence", type=float, default=0.7)
    p.add_argument("--min-country-confidence", type=float, default=0.3)
    for field in ("record_id", "name", "acct", "address", "country_code"):
        p.add_argument(f"--{field.replace('_', '-')}-col", dest=f"{field}_col", default=field)


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; addresses can contain any script.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = _build_parser().parse_args(argv)

    if args.command == "country":
        detector = CountryDetector()
        for addr in args.address:
            res = detector.detect(addr)
            print(f"{addr}\n  -> {res.country_code or 'UNKNOWN'}  confidence={res.confidence:.2f}"
                  f"  runner_up={res.runner_up}\n  evidence: {res.evidence_str()}")
        return 0

    columns = ColumnMap(args.record_id_col, args.name_col, args.acct_col,
                        args.address_col, args.country_code_col)
    config = ResolverConfig(columns=columns,
                            conflict_policy=args.conflict_policy,
                            override_confidence=args.override_confidence,
                            min_country_confidence=args.min_country_confidence)

    if args.command == "serve":
        from .web import AppState, serve
        state = AppState(args.input, args.feedback, args.output, args.previous, config)
        serve(state, args.host, args.port)
        return 0

    previous = load_previous_mapping(args.previous, columns.record_id) if args.previous else None
    feedback = FeedbackStore(args.feedback) if args.feedback else None
    if args.feedback and not Path(args.feedback).exists():
        print(f"warning: feedback file {args.feedback} not found; no corrections applied", file=sys.stderr)

    result = EntityResolver(config).resolve(read_table(args.input), previous, feedback)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    result.records.to_csv(args.output, index=False, encoding="utf-8-sig")
    if args.pairs:
        Path(args.pairs).parent.mkdir(parents=True, exist_ok=True)
        result.pairs.to_csv(args.pairs, index=False, encoding="utf-8-sig")

    recs = result.records
    print(f"records:  {len(recs)}")
    print(f"entities: {result.entity_count}  (multi-record: {(recs.groupby('entity_id').size() > 1).sum()})")
    print(f"pairs:    {(result.pairs['decision'] == 'MATCH').sum()} matched, "
          f"{(result.pairs['decision'] == 'REVIEW').sum()} for review")
    print("country status:")
    for status, n in recs["country_status"].value_counts().items():
        print(f"  {status:<24}{n}")
    print(f"wrote {args.output}" + (f" and {args.pairs}" if args.pairs else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
