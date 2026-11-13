"""
Phase 4 tests - transformation layer.

Covers:
  - Scenario E: duplicate (issue, sprint) records collapse to one bridge row
  - Scenario F: issue with no sprint history produces no bridge row and
    does not crash
  - Scenario G: _pick_current_sprint() / fact_issue.sprint_id regression -
    unchanged behavior vs pre-Phase-4
  - dim_sprint now contains sprints that are NOT anyone's current pick
    (the bug this phase fixes so historical sprints aren't silently
    absent from dim_sprint)
  - malformed sprint reference (no id) is dropped, not crashed on / merged
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "transformation"))

from transform_jira_data import transform_jira_data, _pick_current_sprint


def _issue(key, sprint_field, issue_id="1", issuetype="Story", status="À faire"):
    return {
        "id": issue_id,
        "key": key,
        "fields": {
            "project": {"id": "90001", "key": "TEST", "name": "Generic Test Project"},
            "issuetype": {"id": "10012", "name": issuetype},
            "status": {"id": "10003", "name": status},
            "priority": {"id": "3", "name": "Medium"},
            "assignee": None,
            "reporter": None,
            "customfield_10016": None,
            "customfield_10020": sprint_field,
            "summary": f"Summary for {key}",
            "description": None,
            "created": "2026-01-01T00:00:00.000+0000",
            "updated": "2026-01-01T00:00:00.000+0000",
            "resolutiondate": None,
            "fixVersions": [],
            "issuelinks": [],
        },
    }


SPRINT_1 = {"id": 1, "name": "Sprint 1", "state": "closed", "startDate": "2026-01-01T00:00:00.000Z", "endDate": "2026-01-14T00:00:00.000Z"}
SPRINT_2 = {"id": 2, "name": "Sprint 2", "state": "closed", "startDate": "2026-01-15T00:00:00.000Z", "endDate": "2026-01-28T00:00:00.000Z"}
SPRINT_3_ACTIVE = {"id": 3, "name": "Sprint 3", "state": "active", "startDate": "2026-01-29T00:00:00.000Z", "endDate": "2026-02-11T00:00:00.000Z"}


def test_naive_aware_datetime_comparison_does_not_crash():
    """ROBUSTNESS FIX (this pass): a candidate sprint with NO startDate
    (a real, reachable Jira shape - e.g. a 'future' sprint Jira hasn't
    scheduled a start for yet), mixed with a candidate that HAS a real
    startDate, and neither is 'active' - so _pick_current_sprint() falls
    through to max(..., key=_parse_jira_datetime). Before this fix this
    raised TypeError: can't compare offset-naive and offset-aware
    datetimes, because the no-startDate fallback was a naive datetime.min
    while the real Jira timestamp is timezone-aware. It must now resolve
    without raising, and the sprint WITH a real startDate must win (an
    unscheduled sprint should never be picked over one with an actual
    date)."""
    no_start_date = {"id": 9, "name": "Future Sprint", "state": "future", "startDate": None, "endDate": None}
    has_start_date = {"id": 1, "name": "Sprint 1", "state": "closed", "startDate": "2026-01-01T00:00:00.000Z", "endDate": "2026-01-14T00:00:00.000Z"}

    picked = _pick_current_sprint([no_start_date, has_start_date])  # must not raise
    assert picked["id"] == 1

    picked_order_swapped = _pick_current_sprint([has_start_date, no_start_date])
    assert picked_order_swapped["id"] == 1

    # Two candidates both missing startDate: must also not raise, and must
    # resolve deterministically (max()'s standard first-occurrence tie-break).
    also_no_date = {"id": 10, "name": "Another Future Sprint", "state": "future", "startDate": None, "endDate": None}
    picked_both_missing = _pick_current_sprint([no_start_date, also_no_date])
    assert picked_both_missing["id"] in (9, 10)


def test_scenario_G_current_sprint_pick_regression():
    """_pick_current_sprint() must be behaviorally unchanged for valid
    Jira sprint payloads: prefer active, else latest startDate. ("Valid"
    because _parse_jira_datetime()'s fallback tzinfo was fixed this pass -
    see test_naive_aware_datetime_comparison_does_not_crash - which
    changes behavior only for the previously-crashing no-startDate-mixed-
    with-real-startDate case, not for any payload this function used to
    return a result for.)"""
    assert _pick_current_sprint([SPRINT_1, SPRINT_2, SPRINT_3_ACTIVE])["id"] == 3
    assert _pick_current_sprint([SPRINT_1, SPRINT_2])["id"] == 2  # no active -> latest startDate
    assert _pick_current_sprint(None) is None
    assert _pick_current_sprint([]) is None
    assert _pick_current_sprint(SPRINT_1)["id"] == 1  # single dict form


def test_scenario_G_fact_issue_sprint_id_unchanged():
    raw = {"issues": [_issue("TEST-100", [SPRINT_1, SPRINT_2, SPRINT_3_ACTIVE])]}
    out = transform_jira_data(raw)
    fact = out["fact_issue"]
    assert fact.iloc[0]["sprint_id"] == "3"  # active sprint picked, as before


def test_scenario_F_no_sprint_history():
    raw = {"issues": [_issue("TEST-101", None)]}
    out = transform_jira_data(raw)
    fact = out["fact_issue"]
    assert fact.iloc[0]["sprint_id"] is None

    bridge = out["fact_issue_sprint"]
    assert bridge.empty
    assert list(bridge.columns) == ["issue_key", "sprint_id"]  # safe empty shape


def test_scenario_E_duplicate_historical_association_collapses():
    """Same sprint referenced twice for one issue (e.g. redundant Jira
    payload entries) collapses to a single bridge row."""
    raw = {"issues": [_issue("TEST-102", [SPRINT_1, SPRINT_1, SPRINT_2])]}
    out = transform_jira_data(raw)
    bridge = out["fact_issue_sprint"]
    rows = bridge[bridge["issue_key"] == "TEST-102"]
    assert len(rows) == 2  # Sprint 1 once, Sprint 2 once - not 3
    assert set(rows["sprint_id"]) == {"1", "2"}


def test_dim_sprint_includes_non_current_historical_sprints():
    """THE BUG THIS PHASE FIXES: previously only the picked CURRENT sprint
    per issue was ever added to dim_sprint. A sprint that no issue is
    currently sitting in (every issue that touched it has since moved on)
    must still appear in dim_sprint, or fact_issue_sprint could never
    reference it."""
    raw = {"issues": [_issue("TEST-103", [SPRINT_1, SPRINT_3_ACTIVE])]}
    out = transform_jira_data(raw)
    dim_sprint_ids = set(out["dim_sprint"]["sprint_id"])
    # Sprint 1 is NOT anyone's current pick here (Sprint 3 is active and wins)
    # but must still be present because TEST-103 was historically in it.
    assert "1" in dim_sprint_ids
    assert "3" in dim_sprint_ids

    bridge = out["fact_issue_sprint"]
    assert set(bridge[bridge["issue_key"] == "TEST-103"]["sprint_id"]) == {"1", "3"}


def test_malformed_sprint_reference_dropped_not_crashed():
    # NOTE: startDate is a valid ISO string here (not None) specifically to
    # avoid tripping an unrelated, PRE-EXISTING _parse_jira_datetime
    # naive-vs-aware datetime comparison bug when mixed with SPRINT_1's
    # timezone-aware startDate. That bug predates Phase 4, is not part of
    # this phase's scope, and is called out separately in the handoff
    # summary rather than silently fixed here.
    malformed = {"name": "No Id Sprint", "state": "closed", "startDate": "2026-01-05T00:00:00.000Z", "endDate": None}
    raw = {"issues": [_issue("TEST-104", [malformed, SPRINT_1])]}
    out = transform_jira_data(raw)  # should not raise
    bridge = out["fact_issue_sprint"]
    rows = bridge[bridge["issue_key"] == "TEST-104"]
    assert len(rows) == 1
    assert rows.iloc[0]["sprint_id"] == "1"


def test_multiple_issues_produce_correct_bridge_shape():
    raw = {
        "issues": [
            _issue("TEST-105", [SPRINT_1, SPRINT_2]),
            _issue("TEST-106", [SPRINT_2, SPRINT_3_ACTIVE]),
            _issue("TEST-107", SPRINT_3_ACTIVE),  # single-dict form, not a list
        ]
    }
    out = transform_jira_data(raw)
    bridge = out["fact_issue_sprint"]
    assert len(bridge) == 5  # 2 + 2 + 1
    assert not bridge.duplicated(subset=["issue_key", "sprint_id"]).any()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
