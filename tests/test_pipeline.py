from pathlib import Path

import pandas as pd
import pytest

from party_er import EntityResolver, read_table
from party_er.clustering import assign_entity_ids

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_parties.csv"

SAME_ENTITY = [
    {"R001", "R002", "R003"},  # Acme: account match + name/address match
    {"R004", "R005"},          # John Smith, "Smith, John", same account
    {"R007", "R008"},          # Siemens AG / Aktiengesellschaft
    {"R009", "R010"},          # Tata: R009's wrong country code is corrected from address
    {"R011", "R012"},          # Toyota: Japanese vs English address, same account
    {"R013", "R014"}, {"R015", "R016"}, {"R018", "R019"}, {"R021", "R022"},
    {"R023", "R024"}, {"R026", "R027"}, {"R028", "R029"},
]
DIFFERENT_ENTITY = [
    ("R004", "R006"),  # same name, different address/account
    ("R018", "R020"),  # Maria Garcia in Spain vs Mexico
    ("R023", "R025"),  # Global Widgets AU vs US
]


@pytest.fixture(scope="module")
def result():
    return EntityResolver().resolve(read_table(str(SAMPLE)))


def _eid(result, rid):
    recs = result.records
    return recs.loc[recs["record_id"] == rid, "entity_id"].iat[0]


@pytest.mark.parametrize("group", SAME_ENTITY, ids=lambda g: "+".join(sorted(g)))
def test_duplicates_share_entity_id(result, group):
    assert len({_eid(result, r) for r in group}) == 1


@pytest.mark.parametrize("a, b", DIFFERENT_ENTITY)
def test_distinct_parties_get_different_ids(result, a, b):
    assert _eid(result, a) != _eid(result, b)


def test_country_columns(result):
    recs = result.records.set_index("record_id")
    assert recs.at["R002", "country_code_final"] == "GB"
    assert recs.at["R002", "country_status"] == "INFERRED"
    assert recs.at["R009", "country_status"] == "CONFLICT_USED_ADDRESS"
    assert recs.at["R009", "country_code_final"] == "IN"
    assert recs.at["R031", "country_code_final"] == "NA"   # Namibia, not a missing value
    assert recs.at["R030", "country_status"] == "UNKNOWN"


def test_ids_are_deterministic(result):
    again = EntityResolver().resolve(read_table(str(SAMPLE)))
    assert again.records["entity_id"].tolist() == result.records["entity_id"].tolist()


def test_previous_ids_are_kept_when_new_records_arrive(result):
    previous = dict(zip(result.records["record_id"], result.records["entity_id"]))
    new = pd.DataFrame([{"record_id": "R100", "name": "ACME TRADING LIMITED",
                         "acct": "123456", "address": "12 Baker St, London", "country_code": ""}])
    df = pd.concat([read_table(str(SAMPLE)), new], ignore_index=True)
    rerun = EntityResolver().resolve(df, previous)
    recs = rerun.records.set_index("record_id")
    assert recs.at["R100", "entity_id"] == previous["R001"]
    for rid, eid in previous.items():
        assert recs.at[rid, "entity_id"] == eid


def test_split_entity_keeps_id_on_largest_part():
    # previously R1,R2,R3 were one entity; now R3 is split off
    prev = {"R1": "ENT-OLD", "R2": "ENT-OLD", "R3": "ENT-OLD"}
    ids = assign_entity_ids([[0, 1], [2]], ["R1", "R2", "R3"], prev)
    assert ids[0] == ids[1] == "ENT-OLD"
    assert ids[2] not in ("ENT-OLD", "")


def test_record_id_generated_and_duplicates_rejected():
    df = pd.DataFrame({"name": ["A Ltd", "A Limited"], "acct": ["1", "1"],
                       "address": ["", ""], "country_code": ["GB", "GB"]})
    res = EntityResolver().resolve(df)
    assert res.records["entity_id"].nunique() == 1
    with pytest.raises(ValueError):
        EntityResolver().resolve(df.assign(record_id=["X", "X"]))
