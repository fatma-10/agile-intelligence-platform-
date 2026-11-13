"""
Tests for analytics_snapshot.build_analytics_snapshot().

Pure-function tests only - no DB, no Jira. Stub report dicts are shaped
EXACTLY like the real return values of the four modules (verified
against the actual source via grep - see chat transcript), not
guessed. This mirrors the existing project convention (see
test_release_readiness_phase3.py's StubRRA pattern).

Run: python tests/test_analytics_snapshot.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analytics"))

from analytics_snapshot import build_analytics_snapshot

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        print(f"  FAIL: {label}")


# --- Fixtures shaped exactly like real report output ---------------------
# Values chosen to match the numbers already reported in the project
# context doc (backlog 97.9%, velocity 27.5, sprint 44.0/0.0 planned/
# completed, dependency risk 10%, release readiness 50%) so a human
# reviewer can eyeball-match this fixture against known-good output.

backlog_result_stub = {
    "total_stories": 20,
    "missing_story_points": ["MBA-19"],
    "missing_priority": [],
    "unassigned": ["MBA-12"],
    "duplicate_pairs": [],
    "total_bugs": 6,
    "bugs_missing_priority": [],
    "bugs_unassigned": [],
    "pct_stories_estimated": 95.0,
    "pct_stories_assigned": 95.0,
    "pct_stories_prioritized": 100.0,
    "pct_bugs_assigned": 100.0,
    "pct_bugs_prioritized": 100.0,
    "duplicate_rate": 0.0,
    "quality_score": 97.9,
}

sprint_report_stub = {
    "historical_velocity": {
        "average_velocity": 27.5,
        "sprint_count": 2,
        "sprints": [
            {"sprint_name": "MBA Sprint 3", "sprint_state": "closed", "total_story_points": 24.0},
            {"sprint_name": "MBA Sprint 3", "sprint_state": "closed", "total_story_points": 31.0},
        ],
    },
    "current_sprint": {
        "sprint_id": "77",
        "sprint_name": "MBA Sprint 4",
        "sprint_state": "active",
        "planned_story_points": 44.0,
        "issue_count": 12,
        "completed_story_points": 0.0,
    },
    "sprint_completion_pct": 0.0,
    "scope_to_velocity_ratio": 160.0,
    "risk_level": "HIGH",
    "risk_color": "\U0001F534",
    "recommendation": "Sprint scope significantly exceeds historical velocity. Consider moving some stories to next sprint.",
    "spillover": {
        "spillover_count": 3,
        "spillover_story_points": 13.0,
        "spillover_issues": [],
        "unevaluable_issues": [],
    },
    "historical_sprint_completion": {},
}

dependency_report_stub = {
    "blockers_count": 11,
    "high_impact": [],
    "high_impact_count": 2,
    "blocked_in_active_sprint": 5,
    "blocked_story_points": 47.0,
    "risk_score": 10.0,
    "risk_level": "HIGH",
    "risk_color": "\U0001F534",
    "risk_penalty_relationships": 50.0,
    "risk_penalty_blocked_sp": 40.0,
    "highest_priority_blocker": "Highest",
    "blockers_with_unknown_priority": 0,
    "critical_blocker": "MBA-29: Implement transaction limit validation service",
    "critical_affected": 2,
}

release_report_stub = {
    "release": {
        "version_id": "10001",
        "version_name": "Mobile Banking 2.1",
        "version_state": "unreleased",
        "release_date": None,
        "total_issues": 13,
        "total_story_points": 46.0,
        "completed_story_points": 0.0,
        "total_bugs": 3,
        "open_bugs": 3,
        "remaining_story_points": 46.0,
    },
    "release_date": None,
    "days_until_release": None,
    "release_scoped_blocked_story_points": 21.0,
    "project_wide_blocked_story_points": 47.0,
    "release_scope_sanity_check": {"consistent": True, "release_scoped_blocked_sp": 21.0, "project_wide_blocked_sp": 47.0},
    "completion_rate": 0.0,
    "open_bug_count_consistency": {"consistent": True, "reported_open_bugs": 3, "scored_open_bugs": 3},
    "open_bug_priority_breakdown": [],
    "open_bugs_unknown_priority": 0,
    "open_bug_penalty": 22.5,
    "readiness_score": 50,
    "risk_level": "MEDIUM",
    "risk_color": "\U0001F7E1",
    "status": "Proceed with Caution",
    "blockers": ["3 unresolved bugs", "46.0 SP remaining"],
}

print("=" * 70)
print("TEST 1: happy path - all four reports well-formed")
print("=" * 70)

snapshot = build_analytics_snapshot(
    backlog_result_stub, sprint_report_stub, dependency_report_stub, release_report_stub
)

check("snapshot is a single dict (one row)", isinstance(snapshot, dict))
check("has run_ts", "run_ts" in snapshot)

check("backlog_quality_score == 97.9", snapshot["backlog_quality_score"] == 97.9)
check("backlog_total_stories == 20", snapshot["backlog_total_stories"] == 20)

check("historical_velocity_avg == 27.5", snapshot["historical_velocity_avg"] == 27.5)
check("current_sprint_name == 'MBA Sprint 4'", snapshot["current_sprint_name"] == "MBA Sprint 4")
check("planned_sp == 44.0", snapshot["planned_sp"] == 44.0)
check("completed_sp == 0.0", snapshot["completed_sp"] == 0.0)
check("sprint_completion_pct == 0.0 (top-level, not nested)", snapshot["sprint_completion_pct"] == 0.0)
check("scope_to_velocity_ratio == 160.0", snapshot["scope_to_velocity_ratio"] == 160.0)
check("sprint_risk_level == 'HIGH'", snapshot["sprint_risk_level"] == "HIGH")
check("spillover_count == 3", snapshot["spillover_count"] == 3)
check("spillover_sp == 13.0", snapshot["spillover_sp"] == 13.0)

check("dependency_risk_score == 10.0", snapshot["dependency_risk_score"] == 10.0)
check("dependency_risk_level == 'HIGH'", snapshot["dependency_risk_level"] == "HIGH")
check("blockers_count == 11", snapshot["blockers_count"] == 11)
check("blocked_sp == 47.0", snapshot["blocked_sp"] == 47.0)
check("high_impact_count == 2", snapshot["high_impact_count"] == 2)

check("release_version_name == 'Mobile Banking 2.1'", snapshot["release_version_name"] == "Mobile Banking 2.1")
check("release_completion_pct == 0.0 (top-level, not release['completion_rate'])", snapshot["release_completion_pct"] == 0.0)
check("release_readiness_score == 50", snapshot["release_readiness_score"] == 50)
check("release_risk_level == 'MEDIUM'", snapshot["release_risk_level"] == "MEDIUM")
check("release_status == 'Proceed with Caution'", snapshot["release_status"] == "Proceed with Caution")
check("open_bugs == 3", snapshot["open_bugs"] == 3)
check("remaining_sp == 46.0", snapshot["remaining_sp"] == 46.0)
check("release_scoped_blocked_sp == 21.0", snapshot["release_scoped_blocked_sp"] == 21.0)

print("\n" + "=" * 70)
print("TEST 2: risk_level namespacing - three modules' identically-named")
print("keys must not collide/overwrite each other in the flattened row")
print("=" * 70)
check("sprint_risk_level != dependency_risk_level (HIGH vs HIGH - same value here, check independently below)",
      snapshot["sprint_risk_level"] == "HIGH" and snapshot["dependency_risk_level"] == "HIGH")
check("release_risk_level is independently 'MEDIUM', not overwritten by the other two 'HIGH' values",
      snapshot["release_risk_level"] == "MEDIUM")
# Stronger check with three different values to prove no collision at all
sprint_report_diff = dict(sprint_report_stub); sprint_report_diff["risk_level"] = "LOW"
dependency_report_diff = dict(dependency_report_stub); dependency_report_diff["risk_level"] = "MEDIUM"
release_report_diff = dict(release_report_stub); release_report_diff["risk_level"] = "HIGH"
snap2 = build_analytics_snapshot(backlog_result_stub, sprint_report_diff, dependency_report_diff, release_report_diff)
check("three distinct risk_level values preserved independently",
      snap2["sprint_risk_level"] == "LOW"
      and snap2["dependency_risk_level"] == "MEDIUM"
      and snap2["release_risk_level"] == "HIGH")

print("\n" + "=" * 70)
print("TEST 3: sprint_report == {'error': ...} raises, does not silently write Nones")
print("=" * 70)
try:
    build_analytics_snapshot(backlog_result_stub, {"error": "No active or future sprint found"},
                              dependency_report_stub, release_report_stub)
    check("expected ValueError for sprint error state", False)
except ValueError as e:
    check(f"raised ValueError as expected: {e}", True)

print("\n" + "=" * 70)
print("TEST 4: release_report == {'error': ...} raises, does not silently write Nones")
print("=" * 70)
try:
    build_analytics_snapshot(backlog_result_stub, sprint_report_stub,
                              dependency_report_stub, {"error": "No release/version data found"})
    check("expected ValueError for release error state", False)
except ValueError as e:
    check(f"raised ValueError as expected: {e}", True)

print("\n" + "=" * 70)
print("TEST 5: None inputs raise, not crash with AttributeError deep in the function")
print("=" * 70)
for label, args in [
    ("backlog_result is None", (None, sprint_report_stub, dependency_report_stub, release_report_stub)),
    ("sprint_report is None", (backlog_result_stub, None, dependency_report_stub, release_report_stub)),
    ("dependency_report is None", (backlog_result_stub, sprint_report_stub, None, release_report_stub)),
    ("release_report is None", (backlog_result_stub, sprint_report_stub, dependency_report_stub, None)),
]:
    try:
        build_analytics_snapshot(*args)
        check(f"{label} -> expected ValueError", False)
    except ValueError:
        check(f"{label} -> raised ValueError cleanly", True)

print("\n" + "=" * 70)
print("TEST 6: quality_score None (empty backlog) is passed through as None, not 0")
print("=" * 70)
empty_backlog = dict(backlog_result_stub)
empty_backlog["quality_score"] = None
snap3 = build_analytics_snapshot(empty_backlog, sprint_report_stub, dependency_report_stub, release_report_stub)
check("quality_score None is preserved, not coerced to 0", snap3["backlog_quality_score"] is None)

print(f"\n{'='*40}\n{passed} passed, {failed} failed\n{'='*40}")
sys.exit(1 if failed else 0)
