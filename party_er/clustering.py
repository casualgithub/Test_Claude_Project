"""Turn pairwise matches into entities and assign stable entity IDs."""
from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def connected_components(n: int, edges: Iterable[tuple[int, int]]) -> list[list[int]]:
    uf = UnionFind(n)
    for a, b in edges:
        uf.union(a, b)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())


def constrained_components(
    n: int,
    edges: Iterable[tuple[int, int]],
    must_link: Iterable[tuple[int, int]] = (),
    cannot_link: Iterable[tuple[int, int]] = (),
) -> tuple[list[list[int]], set[tuple[int, int]]]:
    """Connected components that honour user constraints.

    Must-links are joined first, then `edges` in the given (priority) order.
    A join is refused when the two components contain a cannot-link pair, so a
    user's "these are different" holds even through transitive model matches.
    Returns (clusters, rejected) where rejected holds the (i, j) edges and
    must-links that were refused. Without constraints this equals
    connected_components.
    """
    uf = UnionFind(n)
    forbid: dict[int, set[int]] = {}
    for a, b in cannot_link:
        if a != b:
            forbid.setdefault(a, set()).add(b)
            forbid.setdefault(b, set()).add(a)

    rejected: set[tuple[int, int]] = set()

    def join(a: int, b: int) -> None:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            return
        fa, fb = forbid.get(ra, set()), forbid.get(rb, set())
        if any(uf.find(x) == rb for x in fa) or any(uf.find(x) == ra for x in fb):
            rejected.add((min(a, b), max(a, b)))
            return
        uf.union(ra, rb)
        root = uf.find(ra)
        merged = fa | fb
        forbid.pop(ra, None)
        forbid.pop(rb, None)
        if merged:
            forbid[root] = merged

    for a, b in must_link:
        join(a, b)
    for a, b in edges:
        join(a, b)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values()), rejected


def mint_entity_id(anchor: str, prefix: str = "ENT") -> str:
    digest = hashlib.sha1(anchor.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def assign_entity_ids(
    clusters: list[list[int]],
    record_ids: list[str],
    previous: dict[str, str] | None = None,
    prefix: str = "ENT",
) -> list[str]:
    """Return an entity id for every record index.

    * New clusters get a deterministic id derived from their smallest record_id,
      so re-running on the same data yields the same ids.
    * If `previous` (record_id -> entity_id from an earlier run) is given, a
      cluster re-uses the previous id held by most of its members. When an old
      entity splits, the largest part keeps the id and the rest get new ones;
      when old entities merge, the id with the most members survives.
    """
    previous = previous or {}
    reserved = set(previous.values())
    used: set[str] = set()
    out = [""] * len(record_ids)

    order = sorted(clusters, key=lambda c: (-len(c), min(record_ids[i] for i in c)))
    for cluster in order:
        entity_id = None
        votes = Counter(previous[record_ids[i]] for i in cluster if record_ids[i] in previous)
        for candidate, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
            if candidate not in used:
                entity_id = candidate
                break
        if entity_id is None:
            anchor = min(record_ids[i] for i in cluster)
            entity_id, salt = mint_entity_id(anchor, prefix), 0
            while entity_id in used or entity_id in reserved:
                salt += 1
                entity_id = mint_entity_id(f"{anchor}#{salt}", prefix)
        used.add(entity_id)
        for i in cluster:
            out[i] = entity_id
    return out
