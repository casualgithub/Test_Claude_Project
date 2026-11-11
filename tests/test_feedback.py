import json
import threading
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from party_er import EntityResolver, read_table
from party_er.feedback import FeedbackStore, calibration, suggest_reason

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_parties.csv"


def frame(rows):
    return pd.DataFrame(rows, columns=["record_id", "name", "acct", "address", "country_code"])


def lookup(df):
    return {r.record_id: (r.name, r.acct) for r in df.itertuples()}


def entity(result, rid):
    recs = result.records
    return recs.loc[recs["record_id"] == rid, "entity_id"].iat[0]


@pytest.fixture(scope="module")
def sample():
    return read_table(str(SAMPLE))


# --------------------------------------------------------------- constraints
def test_split_detaches_record_and_largest_part_keeps_id(sample):
    resolver = EntityResolver()
    before = resolver.resolve(sample)
    store = FeedbackStore()
    store.add("split", ["R003"], ["R001", "R002"], "Different company at the same building",
              lookup(sample))
    previous = dict(zip(before.records["record_id"], before.records["entity_id"]))
    after = resolver.resolve(sample, previous, store)
    assert entity(after, "R001") == entity(after, "R002") == entity(before, "R001")
    assert entity(after, "R003") != entity(after, "R001")
    assert set(after.pairs.loc[after.pairs["feedback"] == "USER_SPLIT", "right_record_id"]) == {"R003"}


def test_cannot_link_holds_through_transitive_matches(sample):
    store = FeedbackStore()
    # only R001 vs R003 is labelled, but R002 matches both: the weaker edge must be refused
    store.add("split", ["R003"], ["R001"], "different party", lookup(sample))
    res = EntityResolver().resolve(sample, feedback=store)
    assert entity(res, "R001") == entity(res, "R002") != entity(res, "R003")
    blocked = res.pairs[res.pairs["feedback"] == "BLOCKED_BY_FEEDBACK"]
    assert list(zip(blocked["left_record_id"], blocked["right_record_id"])) == [("R002", "R003")]


def test_merge_overrides_country_conflict(sample):
    store = FeedbackStore()
    store.add("merge", ["R025"], ["R023", "R024"], "US branch of the same group, same owner",
              lookup(sample))
    res = EntityResolver().resolve(sample, feedback=store)
    assert entity(res, "R025") == entity(res, "R023") == entity(res, "R024")
    row = res.pairs[(res.pairs["right_record_id"] == "R025") & (res.pairs["feedback"] == "USER_MERGE")]
    assert row["model_decision"].iat[0] == "NO_MATCH" and row["decision"].iat[0] == "MATCH"


def test_later_correction_wins(sample):
    store = FeedbackStore()
    store.add("merge", ["R025"], ["R023"], "same group", lookup(sample))
    store.add("split", ["R025"], ["R023"], "no, separate legal entity", lookup(sample))
    must, cannot = store.constraints()
    assert ("R023", "R025") in cannot and ("R023", "R025") not in must


def test_no_feedback_changes_nothing(sample):
    base = EntityResolver().resolve(sample)
    empty = EntityResolver().resolve(sample, feedback=FeedbackStore())
    assert list(base.records["entity_id"]) == list(empty.records["entity_id"])
    assert empty.labelled.empty


# -------------------------------------------------------- generalised rules
def test_name_alias_generalises_to_new_records():
    first = frame([
        ["A1", "International Business Machines Corp", "", "1 New Orchard Rd, Armonk, NY 10504", "US"],
        ["A2", "IBM", "", "1 New Orchard Road, Armonk NY 10504", "US"],
    ])
    resolver = EntityResolver()
    assert entity(resolver.resolve(first), "A1") != entity(resolver.resolve(first), "A2")
    store = FeedbackStore()
    c = store.add("merge", ["A2"], ["A1"], "IBM is the abbreviation of International Business Machines",
                  lookup(first))
    assert c.reason == "name_alias"  # suggested from the explanation

    # a later extract: different records, never corrected
    later = frame([
        ["B1", "IBM", "", "590 Madison Avenue, New York, NY 10022", "US"],
        ["B2", "International Business Machines Corporation", "", "590 Madison Ave, New York NY 10022", "US"],
    ])
    res = resolver.resolve(later, feedback=store)
    assert entity(res, "B1") == entity(res, "B2")
    assert "NAME_ALIAS" in res.pairs["learned"].iat[0]


def test_shared_account_stops_counting_as_evidence():
    df = frame([
        ["J1", "Anna Lee", "JOINT-77", "4 Elm St, Boston, MA 02108", "US"],
        ["J2", "Anna Lea", "JOINT-77", "90 Oak Ave, Austin, TX 78701", "US"],
        ["J3", "Ben Carter", "JOINT-77", "4 Elm St, Boston, MA 02108", "US"],
        ["J4", "Ben Cartar", "JOINT-77", "77 Pine Rd, Denver, CO 80202", "US"],
    ])
    resolver = EntityResolver()
    base = resolver.resolve(df)
    assert entity(base, "J3") == entity(base, "J4")  # same account + similar name
    store = FeedbackStore()
    store.add("split", ["J2"], ["J1"], "joint account held by two different people", lookup(df))
    res = resolver.resolve(df, feedback=store)
    assert entity(res, "J3") != entity(res, "J4")  # J3/J4 were never corrected
    assert store.rules_summary()["shared_accounts"] == ["JOINT77"]


def test_ambiguous_name_needs_more_than_the_name():
    df = frame([
        ["W1", "Wei Zhang", "", "", ""],
        ["W2", "Wei Zhang", "", "", ""],
        ["W3", "WEI ZHANG", "", "", ""],
    ])
    resolver = EntityResolver()
    assert resolver.resolve(df).records["entity_id"].nunique() == 1
    store = FeedbackStore()
    store.add("split", ["W2"], ["W1"], "common name, different person", lookup(df))
    res = resolver.resolve(df, feedback=store)
    assert res.records["entity_id"].nunique() == 3
    assert set(res.pairs["rule"]) == {"LEARNED_AMBIGUOUS_NAME"}


# ------------------------------------------------------- store mechanics
def test_store_persists_and_undo_restores(tmp_path, sample):
    path = tmp_path / "fb" / "corrections.json"
    store = FeedbackStore(path)
    c = store.add("split", ["R003"], ["R001", "R002"], "different party", lookup(sample), author="ops")
    reloaded = FeedbackStore(path)
    assert [x.id for x in reloaded.corrections] == [c.id]
    assert reloaded.constraints() == store.constraints()
    assert reloaded.corrections[0].snapshot["R001"]["account"] == "123456"
    reloaded.remove(c.id)
    assert json.loads(path.read_text(encoding="utf-8"))["corrections"] == []


def test_validation(sample):
    store = FeedbackStore()
    with pytest.raises(ValueError, match="explanation"):
        store.add("merge", ["R001"], ["R004"], "   ", lookup(sample))
    with pytest.raises(ValueError, match="unknown"):
        store.add("merge", ["NOPE"], ["R004"], "x", lookup(sample))
    with pytest.raises(ValueError, match="reason"):
        store.add("merge", ["R001"], ["R004"], "x", lookup(sample), reason="shared_account")


@pytest.mark.parametrize("action, text, reason", [
    ("merge", "Trading as Acme, formerly Acme Holdings", "name_alias"),
    ("merge", "Customer moved to a new address", "same_party"),
    ("merge", "", "same_party"),
    ("split", "Joint account with his wife", "shared_account"),
    ("split", "Same name but a different person (father and son)", "same_name_different_party"),
    ("split", "Joint account, two different people", "shared_account"),
    ("split", "Not them", "different_party"),
])
def test_suggest_reason(action, text, reason):
    assert suggest_reason(action, text) == reason


def test_calibration_reports_agreement(sample):
    store = FeedbackStore()
    store.add("merge", ["R025"], ["R023"], "same group", lookup(sample))
    store.add("split", ["R003"], ["R001"], "different party", lookup(sample))
    res = EntityResolver().resolve(sample, feedback=store)
    cal = calibration(res.labelled, 0.88)
    assert cal["labelled_pairs"] == 2 and cal["base_agreement"] == 0.0
    assert cal["suggested_score_threshold"] is None  # too few labels


# ------------------------------------------------------------------- web
def test_web_api_round_trip(tmp_path):
    from http.server import ThreadingHTTPServer

    from party_er.web import AppState, make_handler

    state = AppState(str(SAMPLE), str(tmp_path / "c.json"), str(tmp_path / "out.csv"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert b"Party entity search" in urllib.request.urlopen(base + "/").read()
        _, found = call("/api/search?q=acme%20trading")
        acme = found["results"][0]
        assert acme["size"] == 3 and acme["score"] >= 90
        _, by_acct = call("/api/search?q=123456")
        assert by_acct["results"][0]["entity_id"] == acme["entity_id"]

        status, err = call("/api/corrections", {"action": "split", "record_ids": ["R003"],
                                                "against_entity": acme["entity_id"], "explanation": ""})
        assert status == 400 and "explanation" in err["error"]

        status, res = call("/api/corrections", {"action": "split", "record_ids": ["R003"],
                                                "against_entity": acme["entity_id"],
                                                "explanation": "different company in the same building"})
        assert status == 200
        assert res["entity_ids"]["R001"] == acme["entity_id"] != res["entity_ids"]["R003"]
        _, detail = call(f"/api/entity/{acme['entity_id']}")
        assert detail["size"] == 2 and len(detail["corrections"]) == 1

        # move R003 into the Siemens entity (split + merge), then undo both
        siemens = state.records["R007"]["entity_id"]
        _, moved = call("/api/corrections", {"action": "move", "record_ids": ["R004"],
                                             "against_entity": siemens, "explanation": "booked to wrong party"})
        assert [c["action"] for c in moved["corrections"]] == ["split", "merge"]
        assert moved["entity_ids"]["R004"] == siemens
        _, learning = call("/api/corrections")
        assert len(learning["corrections"]) == 3 and learning["calibration"]["labelled_pairs"] >= 3
        for c in learning["corrections"]:
            assert call(f"/api/corrections/{c['id']}", method="DELETE")[0] == 200
        assert state.records["R003"]["entity_id"] == acme["entity_id"]
        assert (tmp_path / "out.csv").exists()
        assert call("/api/entity/NOPE")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
