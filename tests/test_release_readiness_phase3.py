"""
Phase 3 verification tests for release_readiness.py.

Convention note: this repo has no pytest/testing framework listed in
requirements.txt, and the existing test files referenced in code comments
(test_dependency_risk.py, test_release_readiness.py, test_completion_pct.py,
test_phase2_corrections.py) are all invoked as plain scripts (a custom
check()/PASS-FAIL/sys.exit pattern), matching what this file already used
last turn. Kept as-is rather than introducing a new pytest dependency the
project doesn't otherwise use.

Everything here is either:
  (A) a pure-function / logic unit test, actually executed, or
  (B) a report-wiring test that stubs out the four DB-hitting methods
      (get_release_data, get_open_bugs_detail,
      get_release_blocked_story_points, get_project_wide_blocked_story_points)
      so generate_report() itself runs end-to-end without a live DB.

Nothing here executes real SQL or connects to MySQL - this sandbox has no
network path to the project's database. Where a claim can only be verified
by running the real query against real data, that is called out explicitly
in the accompanying summary, not tested here.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "analytics"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))

import pandas as pd
from datetime import datetime, timedelta
from release_readiness import ReleaseReadinessAnalyzer as RRA
import dependency_risk

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


print("=== (0) Semantics verified by inspecting real dependency_risk.py ===")
# These aren't "tests" in the executable sense (there's no DB), they
# confirm - by reading the actual source, not the Phase 2 handoff prose -
# that the claims made about Module 3 are accurate.
import inspect
src = inspect.getsource(dependency_risk)
check("dependency_risk.get_blockers() defines source_issue_key as blocker_key",
      "fil.source_issue_key as blocker_key" in src)
check("dependency_risk.get_blockers() defines target_issue_key as blocked_key",
      "fil.target_issue_key as blocked_key" in src)
check("dependency_risk.generate_report() dedups by 'blocked_key' before summing 'blocked_story_points'",
      "drop_duplicates(subset=['blocked_key'])['blocked_story_points']" in src)
# => release_readiness's `fil.target_issue_key = fi.issue_key` correctly
# requires the release's issue to be the BLOCKED side, matching Module 3's
# own target=blocked convention. No query change needed.


print("\n=== _compute_days_until_release ===")

d, days = RRA._compute_days_until_release(None)
check("None -> (None, None)", d is None and days is None)

d, days = RRA._compute_days_until_release("")
check("'' -> (None, None)", d is None and days is None)

d, days = RRA._compute_days_until_release(pd.NaT)
check("pd.NaT -> (None, None)", d is None and days is None)

future_str = (datetime.now().date() + timedelta(days=30)).isoformat()
d, days = RRA._compute_days_until_release(future_str)
check(f"future ISO string -> days=30", d == future_str and days == 30)

future_date = datetime.now().date() + timedelta(days=10)
d, days = RRA._compute_days_until_release(future_date)
check("future date object -> days=10", d == future_date.isoformat() and days == 10)

future_ts = pd.Timestamp(datetime.now().date() + timedelta(days=5))
d, days = RRA._compute_days_until_release(future_ts)
check("pandas.Timestamp -> days=5", days == 5)

past_str = (datetime.now().date() - timedelta(days=7)).isoformat()
d, days = RRA._compute_days_until_release(past_str)
check("past date -> negative days (overdue)", days == -7)

d, days = RRA._compute_days_until_release("not-a-date")
check("garbage string -> (None, None), no crash", d is None and days is None)


print("\n=== _sum_blocked_story_points (dedup rule) ===")

df = pd.DataFrame([
    {"issue_key": "MBA-1", "story_points": 5.0},
    {"issue_key": "MBA-1", "story_points": 5.0},
    {"issue_key": "MBA-2", "story_points": 3.0},
])
total = RRA._sum_blocked_story_points(df, key_col="issue_key", sp_col="story_points")
check("dedup by issue_key: 5+3 not 5+5+3", total == 8.0)

df2 = pd.DataFrame([
    {"issue_key": "MBA-3", "story_points": None},
    {"issue_key": "MBA-4", "story_points": 2.0},
])
total2 = RRA._sum_blocked_story_points(df2, key_col="issue_key", sp_col="story_points")
check("missing SP treated as 0", total2 == 2.0)

check("empty df -> 0.0", RRA._sum_blocked_story_points(pd.DataFrame(), "issue_key", "story_points") == 0.0)
check("None df -> 0.0", RRA._sum_blocked_story_points(None, "issue_key", "story_points") == 0.0)

df3 = pd.DataFrame([
    {"blocked_key": "MBA-10", "blocked_story_points": 8.0},
    {"blocked_key": "MBA-10", "blocked_story_points": 8.0},
    {"blocked_key": "MBA-11", "blocked_story_points": 2.0},
])
total3 = RRA._sum_blocked_story_points(df3, key_col="blocked_key", sp_col="blocked_story_points")
check("dedup works for Module 3's own blocked_key/blocked_story_points shape too", total3 == 10.0)

# Point 3 check: for the release-scoped query's actual SELECT (issue_key,
# story_points only, no blocker-identifying column), simulate what
# SELECT DISTINCT would already do at the SQL level - i.e. a "relationship
# grain" input with no differentiating column collapses to one row per
# issue BEFORE it even reaches _sum_blocked_story_points.
raw_relationship_grain = pd.DataFrame([
    {"issue_key": "MBA-20", "story_points": 8.0},   # blocker A -> MBA-20
    {"issue_key": "MBA-20", "story_points": 8.0},   # blocker B -> MBA-20 (2nd relationship)
])
sql_distinct_result = raw_relationship_grain.drop_duplicates()
check("SELECT DISTINCT on (issue_key, story_points) alone already collapses 2 relationships to 1 row",
      len(sql_distinct_result) == 1)
# i.e. the Python-side dedup in _sum_blocked_story_points is a defensive
# no-op for this specific query shape, not the actual place dedup happens.


print("\n=== _check_release_scope_sanity ===")

check("release <= project -> consistent",
      RRA._check_release_scope_sanity(10.0, 47.0)["consistent"] is True)
check("release == project -> consistent",
      RRA._check_release_scope_sanity(47.0, 47.0)["consistent"] is True)
check("release > project -> INCONSISTENT",
      RRA._check_release_scope_sanity(50.0, 47.0)["consistent"] is False)
check("project_wide=None -> treated as consistent, not falsely flagged",
      RRA._check_release_scope_sanity(10.0, None)["consistent"] is True)
check("both zero -> consistent",
      RRA._check_release_scope_sanity(0.0, 0.0)["consistent"] is True)


print("\n=== (B) generate_report() wiring, with DB-hitting methods stubbed ===")


class StubRRA(RRA):
    """Subclass that skips __init__ (no engine needed) and overrides the
    four methods that would otherwise hit MySQL, returning canned data
    shaped like real query results. Everything downstream (generate_report)
    is the REAL, unmodified method under test."""

    def __init__(self, release_date=None, release_scoped_sp_rows=None,
                 project_wide_sp=47.0, remaining_sp=30, open_bugs_rows=None,
                 open_bugs_count=3, total_sp=50, completed_sp=20):
        self._release_date = release_date
        self._release_scoped_sp_rows = release_scoped_sp_rows or []
        self._project_wide_sp = project_wide_sp
        self._remaining_sp = remaining_sp
        self._open_bugs_rows = open_bugs_rows if open_bugs_rows is not None else [
            {"issue_key": "MBA-1", "priority_name": "High"},
            {"issue_key": "MBA-2", "priority_name": "Medium"},
            {"issue_key": "MBA-3", "priority_name": "Highest"},
        ]
        self._open_bugs_count = open_bugs_count
        self._total_sp = total_sp
        self._completed_sp = completed_sp

    def get_release_data(self, version_name=None):
        return {
            "version_id": 1,
            "version_name": "Mobile Banking 2.1",
            "version_state": "unreleased",
            "release_date": self._release_date,
            "total_issues": 10,
            "total_story_points": self._total_sp,
            "completed_story_points": self._completed_sp,
            "total_bugs": 3,
            "open_bugs": self._open_bugs_count,
            "remaining_story_points": self._remaining_sp,
        }

    def get_open_bugs_detail(self, version_id):
        return pd.DataFrame(self._open_bugs_rows)

    def get_release_blocked_story_points(self, version_id):
        return pd.DataFrame(self._release_scoped_sp_rows, columns=["issue_key", "story_points"]) \
            if self._release_scoped_sp_rows else pd.DataFrame(columns=["issue_key", "story_points"])

    def get_project_wide_blocked_story_points(self):
        return self._project_wide_sp


# Baseline: no release date set (matches real current MBA data), 8 SP
# release-scoped blocked, 47 SP project-wide.
stub = StubRRA(release_date=None, release_scoped_sp_rows=[{"issue_key": "MBA-5", "story_points": 8.0}])
report = stub.generate_report()

check("report has 'release_date' key", "release_date" in report)
check("no release date set -> release_date is None", report["release_date"] is None)
check("no release date set -> days_until_release is None", report["days_until_release"] is None)

check("report has 'release_scoped_blocked_story_points'", "release_scoped_blocked_story_points" in report)
check("release-scoped SP = 8.0", report["release_scoped_blocked_story_points"] == 8.0)

check("report has 'project_wide_blocked_story_points'", "project_wide_blocked_story_points" in report)
check("project-wide SP = 47.0 (passthrough of stub)", report["project_wide_blocked_story_points"] == 47.0)

check("report has 'release_scope_sanity_check'", "release_scope_sanity_check" in report)
check("sanity check reports consistent (8 <= 47)", report["release_scope_sanity_check"]["consistent"] is True)

# remaining=30 -> penalty min(30*2,30)=30
# bugs: High(1.5)+Medium(1.0)+Highest(2.0) = 4.5 * 5 = 22.5, under cap 40
# score = 100 - 30 - 22.5 = 47.5 -> round() = 48 (banker's rounding, 48 is even)
check("readiness_score computed as expected from core inputs only (48)",
      report["readiness_score"] == 48)
check("risk_level HIGH for score 48", report["risk_level"] == "HIGH")


# Now change ONLY the two new-feature inputs (release date + release-scoped
# SP + project-wide SP), keep every core scoring input identical, and
# confirm readiness_score does NOT move - i.e. the new fields are additive
# and do not leak into scoring.
stub2 = StubRRA(
    release_date="2027-01-01",
    release_scoped_sp_rows=[{"issue_key": "MBA-5", "story_points": 8.0},
                             {"issue_key": "MBA-6", "story_points": 40.0}],  # much bigger
    project_wide_sp=999.0,  # deliberately different
)
report2 = stub2.generate_report()
check("changing release_date does not change readiness_score",
      report2["readiness_score"] == report["readiness_score"])
check("changing release-scoped/project-wide SP does not change readiness_score",
      report2["readiness_score"] == report["readiness_score"])
check("changing release_date does not change risk_level",
      report2["risk_level"] == report["risk_level"])
check("new release_date is surfaced correctly", report2["release_date"] == "2027-01-01")
check("days_until_release computed for the new date", report2["days_until_release"] is not None)


# Sanity-check violation path: release-scoped > project-wide should be
# flagged, not silently accepted, and STILL must not affect readiness_score.
stub3 = StubRRA(
    release_date=None,
    release_scoped_sp_rows=[{"issue_key": "MBA-9", "story_points": 100.0}],
    project_wide_sp=47.0,
)
report3 = stub3.generate_report()
check("release-scoped (100) > project-wide (47) -> sanity check flags inconsistency",
      report3["release_scope_sanity_check"]["consistent"] is False)
check("an inconsistent sanity check still does not alter readiness_score",
      report3["readiness_score"] == report["readiness_score"])


# Empty release-scoped result (e.g. no blockers touch this release) -> 0.0,
# not None/crash, and still <= any non-negative project-wide total.
stub4 = StubRRA(release_date=None, release_scoped_sp_rows=[], project_wide_sp=47.0)
report4 = stub4.generate_report()
check("no release-scoped blockers -> 0.0, not None/crash",
      report4["release_scoped_blocked_story_points"] == 0.0)
check("0.0 release-scoped is trivially consistent with project-wide",
      report4["release_scope_sanity_check"]["consistent"] is True)


print(f"\n{'='*40}\n{passed} passed, {failed} failed\n{'='*40}")
sys.exit(1 if failed else 0)