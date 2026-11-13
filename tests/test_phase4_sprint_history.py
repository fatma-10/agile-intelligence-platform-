"""
Phase 4 tests - sprint_planning.py spillover + historical completion rate.

These are pure-function tests against SprintPlanningAnalyzer._compute_spillover
and ._compute_historical_completion_rate, using synthetic DataFrames shaped
exactly like get_issue_sprint_history()'s query output - no live MySQL
connection required (see module docstring in dependency_risk.py for why
this pure-function-with-synthetic-data pattern is used project-wide).

Covers Scenarios A, B, C, D from the Phase 4 spec, plus NULL-current-sprint
handling and multi-sprint historical completion.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analytics"))

import pandas as pd
from sprint_planning import SprintPlanningAnalyzer


def _row(issue_key, hist_id, hist_name, hist_start, cur_id, cur_name, cur_start,
         status_name="À faire", sp=5.0):
    return {
        "issue_key": issue_key,
        "historical_sprint_id": hist_id,
        "historical_sprint_name": hist_name,
        "historical_start_date": pd.Timestamp(hist_start) if hist_start else pd.NaT,
        "current_sprint_id": cur_id,
        "current_sprint_name": cur_name,
        "current_start_date": pd.Timestamp(cur_start) if cur_start else pd.NaT,
        "status_id": "10006" if status_name == "Terminé" else "10003",
        "status_name": status_name,
        "story_points": sp,
    }


def test_scenario_A_realistic_carry_over_is_spillover():
    """Issue A: Sprint 1 (not completed there) then Sprint 3 (completed),
    current/final sprint = Sprint 3. Sprint 1 is earlier -> spillover."""
    df = pd.DataFrame([
        _row("ISSUE-A", "1", "Sprint 1", "2026-01-01", "3", "Sprint 3", "2026-02-01", status_name="Terminé"),
        _row("ISSUE-A", "3", "Sprint 3", "2026-02-01", "3", "Sprint 3", "2026-02-01", status_name="Terminé"),
    ])
    result = SprintPlanningAnalyzer._compute_spillover(df)
    assert result["spillover_count"] == 1
    assert result["spillover_issues"][0]["issue_key"] == "ISSUE-A"
    assert result["spillover_issues"][0]["earlier_sprints"] == ["Sprint 1"]
    assert result["spillover_story_points"] == 5.0

    completion = SprintPlanningAnalyzer._compute_historical_completion_rate(df)
    # Sprint 1: 1 historically-associated issue (ISSUE-A), currently done -> 100%
    assert completion["1"]["total_issues"] == 1
    assert completion["1"]["completed_issues"] == 1
    assert completion["1"]["completion_rate_snapshot"] == 100.0


def test_scenario_B_single_sprint_is_not_spillover():
    """Issue B only ever in Sprint 3 -> not spillover (nothing earlier to
    compare against)."""
    df = pd.DataFrame([
        _row("ISSUE-B", "3", "Sprint 3", "2026-02-01", "3", "Sprint 3", "2026-02-01"),
    ])
    result = SprintPlanningAnalyzer._compute_spillover(df)
    assert result["spillover_count"] == 0
    assert result["spillover_issues"] == []


def test_scenario_C_duplicate_sprint_names_do_not_cause_spillover():
    """Two DISTINCT sprint entities share the name 'MBA Sprint 1' (ids '10'
    and '74'), and neither is chronologically earlier than the issue's
    current sprint - so no spillover should be inferred purely from name
    duplication."""
    df = pd.DataFrame([
        _row("ISSUE-C", "10", "MBA Sprint 1", "2026-03-01", "74", "MBA Sprint 1", "2026-03-01"),
    ])
    # current_sprint_id = '74', historical row references sprint id '10'.
    # Fix the row's current pointer to '74' matching one of the duplicate ids:
    df.loc[0, "current_sprint_id"] = "74"
    df.loc[0, "current_sprint_name"] = "MBA Sprint 1"
    df.loc[0, "current_start_date"] = pd.Timestamp("2026-03-01")
    result = SprintPlanningAnalyzer._compute_spillover(df)
    assert result["spillover_count"] == 0  # same start_date, id differs but not earlier


def test_scenario_C_duplicate_names_with_real_earlier_start_IS_spillover():
    """Same duplicate-name setup, but this time the historical sprint id
    genuinely has an earlier start_date than the current one - spillover
    must be detected based on the date, not suppressed just because the
    names collide."""
    df = pd.DataFrame([
        _row("ISSUE-C2", "10", "MBA Sprint 1", "2026-01-01", "74", "MBA Sprint 1", "2026-03-01"),
        _row("ISSUE-C2", "74", "MBA Sprint 1", "2026-03-01", "74", "MBA Sprint 1", "2026-03-01"),
    ])
    result = SprintPlanningAnalyzer._compute_spillover(df)
    assert result["spillover_count"] == 1
    assert result["spillover_issues"][0]["earlier_sprints"] == ["MBA Sprint 1"]


def test_scenario_D_unique_names_normal_spillover():
    df = pd.DataFrame([
        _row("ISSUE-D", "1", "Alpha", "2026-01-01", "2", "Beta", "2026-02-01"),
        _row("ISSUE-D", "2", "Beta", "2026-02-01", "2", "Beta", "2026-02-01"),
        _row("ISSUE-E", "5", "Gamma", "2026-01-15", "5", "Gamma", "2026-01-15"),
    ])
    result = SprintPlanningAnalyzer._compute_spillover(df)
    keys = {r["issue_key"] for r in result["spillover_issues"]}
    assert keys == {"ISSUE-D"}
    assert result["spillover_count"] == 1


def test_chronology_uses_start_date_never_sprint_id_numeric_value():
    """EXPLICIT chronology-source test: sprint ids are deliberately chosen
    to be NON-chronological (historical sprint id=100 is numerically
    LARGER than current sprint id=5), while start_date correctly shows
    the id=100 sprint as EARLIER. If the code were (incorrectly) using
    the numeric value of sprint_id to infer order, id=100 > id=5 would
    make it look like the historical sprint is LATER, and spillover would
    be missed. The correct result, using start_date, is spillover=True."""
    df = pd.DataFrame([
        _row("ISSUE-CHRONO", "100", "Sprint With High Id", "2026-01-01",
             "5", "Sprint With Low Id", "2026-02-01"),
        _row("ISSUE-CHRONO", "5", "Sprint With Low Id", "2026-02-01",
             "5", "Sprint With Low Id", "2026-02-01"),
    ])
    result = SprintPlanningAnalyzer._compute_spillover(df)
    assert result["spillover_count"] == 1
    assert result["spillover_issues"][0]["issue_key"] == "ISSUE-CHRONO"
    assert result["spillover_issues"][0]["earlier_sprints"] == ["Sprint With High Id"]


def test_null_current_sprint_is_unevaluable_not_miscounted():
    """An issue with sprint history but NO current sprint (fact_issue.sprint_id
    is NULL) cannot be evaluated for spillover - it must not be silently
    counted as not-spillover."""
    df = pd.DataFrame([
        _row("ISSUE-F", "1", "Sprint 1", "2026-01-01", None, None, None),
    ])
    result = SprintPlanningAnalyzer._compute_spillover(df)
    assert result["spillover_count"] == 0
    assert "ISSUE-F" in result["unevaluable_issues"]


def test_empty_history_df_returns_safe_defaults():
    result = SprintPlanningAnalyzer._compute_spillover(pd.DataFrame())
    assert result["spillover_count"] == 0
    assert result["spillover_issues"] == []
    assert result["unevaluable_issues"] == []

    completion = SprintPlanningAnalyzer._compute_historical_completion_rate(pd.DataFrame())
    assert completion == {}

    result_none = SprintPlanningAnalyzer._compute_spillover(None)
    assert result_none["spillover_count"] == 0


def test_historical_completion_rate_multi_sprint_mixed_status():
    df = pd.DataFrame([
        _row("I1", "1", "Sprint 1", "2026-01-01", "1", "Sprint 1", "2026-01-01", status_name="Terminé"),
        _row("I2", "1", "Sprint 1", "2026-01-01", "2", "Sprint 2", "2026-01-15", status_name="En cours"),
        _row("I2", "2", "Sprint 2", "2026-01-15", "2", "Sprint 2", "2026-01-15", status_name="En cours"),
        _row("I3", "1", "Sprint 1", "2026-01-01", "1", "Sprint 1", "2026-01-01", status_name="À faire"),
    ])
    completion = SprintPlanningAnalyzer._compute_historical_completion_rate(df)
    # Sprint 1 historically had I1 (done), I2 (not done), I3 (not done) = 1/3
    assert completion["1"]["total_issues"] == 3
    assert completion["1"]["completed_issues"] == 1
    assert completion["1"]["completion_rate_snapshot"] == round(1 / 3 * 100, 1)
    # Sprint 2 historically had only I2 (not done) = 0/1
    assert completion["2"]["total_issues"] == 1
    assert completion["2"]["completed_issues"] == 0
    assert completion["2"]["completion_rate_snapshot"] == 0.0


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
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
