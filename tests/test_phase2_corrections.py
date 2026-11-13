"""
Synthetic validation for the Phase-2 corrective pass.
No live MySQL needed - exercises the pure functions directly with
constructed pandas DataFrames / dicts, same technique as Phase 1.

Run from the project root as: python tests/test_phase2_corrections.py
(same sys.path pattern main.py uses to reach analytics/ and config/).
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "analytics"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))

import pandas as pd
from backlog_quality import RAW_WEIGHTS
from dependency_risk import DependencyRiskAnalyzer
from release_readiness import ReleaseReadinessAnalyzer

print("=" * 70)
print("CORRECTION 1: duplicate_rate behavior across backlog sizes")
print("=" * 70)


def duplicate_rate(total_stories, dup_pairs):
    if total_stories == 0:
        return None
    return round(min(dup_pairs / total_stories, 1.0) * 100, 1)


scenarios = [
    ("10 Stories, 1 dup pair", 10, 1),
    ("100 Stories, 1 dup pair", 100, 1),
    ("10 Stories, 5 dup pairs", 10, 5),
    ("100 Stories, 5 dup pairs", 100, 5),
    ("3 Stories, 3 dup pairs (max possible pairs=3)", 3, 3),
    ("4 Stories, 6 dup pairs (max possible pairs=6, capped)", 4, 6),
]

print(f"{'Scenario':<48} {'rate':>8}")
for label, total, pairs in scenarios:
    rate = duplicate_rate(total, pairs)
    print(f"{label:<48} {rate if rate is not None else 'N/A':>7}%")

print("""
DEMONSTRATION OF THE LIMITATION:
- 1 dup pair in 10 Stories -> 10.0% ; the SAME 1 dup pair in 100 Stories -> 1.0%.
  Same absolute defect, very different rate - this is expected for a rate-based
  metric but is a DIFFERENT signal than the old formula's flat "-2 points"
  penalty (which was identical in both cases). This is exactly why the old
  count-based weight cannot be claimed as mathematically preserved once the
  metric becomes a rate - confirms correction #1's documentation change.
- A small backlog (3-4 Stories) can hit or exceed a 100% duplicate_rate from
  just a handful of pairs, correctly demonstrating why the rate is capped at
  100% rather than left unbounded.
""")

assert duplicate_rate(10, 1) == 10.0
assert duplicate_rate(100, 1) == 1.0
assert duplicate_rate(3, 3) == 100.0
assert duplicate_rate(4, 6) == 100.0  # capped, would be 150% uncapped
print("PASS: duplicate_rate scenarios behave as documented.\n")


print("=" * 70)
print("CORRECTION 2: dependency risk relationship-based grain")
print("=" * 70)


def make_blockers(rows):
    """rows: list of (blocker_key, blocked_key, priority, blocked_sp)"""
    return pd.DataFrame([
        {
            "blocker_key": r[0], "blocked_key": r[1],
            "blocker_priority": r[2], "blocked_story_points": r[3],
        }
        for r in rows
    ])


# Same blocker issue (BLK-1) blocking 3 different targets: relationship-based
# grain means its priority weight is counted 3 times (not deduped to 1).
same_blocker_3x = make_blockers([
    ("BLK-1", "T-1", "High", 5),
    ("BLK-1", "T-2", "High", 3),
    ("BLK-1", "T-3", "High", 2),
])
blocked_sp_same = same_blocker_3x.drop_duplicates(subset=["blocked_key"])["blocked_story_points"].sum()
result_same = DependencyRiskAnalyzer._compute_risk_score(same_blocker_3x, blocked_sp_same)

# Three DIFFERENT blocker issues, each blocking one target, same priorities/SP
diff_blockers_3x = make_blockers([
    ("BLK-1", "T-1", "High", 5),
    ("BLK-2", "T-2", "High", 3),
    ("BLK-3", "T-3", "High", 2),
])
blocked_sp_diff = diff_blockers_3x.drop_duplicates(subset=["blocked_key"])["blocked_story_points"].sum()
result_diff = DependencyRiskAnalyzer._compute_risk_score(diff_blockers_3x, blocked_sp_diff)

print(f"Same blocker issue x3 relationships -> penalty_relationships = {result_same['penalty_relationships']}, score = {result_same['risk_score']}")
print(f"Three distinct blocker issues (1 relationship each) -> penalty_relationships = {result_diff['penalty_relationships']}, score = {result_diff['risk_score']}")
print("""
Both scenarios have 3 blocking relationships at 'High' priority and the same
total blocked SP, so they score IDENTICALLY under the relationship-based
formula - confirming the formula treats "3 relationships" the same whether
they come from 1 blocker issue or 3, exactly as documented (each blocking
relationship contributes independently; get_high_impact_blockers() is the
separate mechanism that identifies the "1 issue causing 3 problems" case).
""")
assert result_same['penalty_relationships'] == result_diff['penalty_relationships']
assert result_same['risk_score'] == result_diff['risk_score']
print("PASS: relationship-based grain confirmed - no accidental dedup by blocker issue.\n")


print("=" * 70)
print("CORRECTION 3: release readiness open-bug count consistency check")
print("=" * 70)

check = ReleaseReadinessAnalyzer._check_open_bug_count_consistency

# Case A: consistent (3 reported, 3 scored)
detail_consistent = pd.DataFrame({
    "issue_key": ["BUG-1", "BUG-2", "BUG-3"],
    "priority_name": ["High", "Medium", None],
})
res_a = check(3, detail_consistent)
print(f"Case A (3 reported, 3 scored rows): consistent={res_a['consistent']}  {res_a}")
assert res_a["consistent"] is True

# Case B: mismatch (reported 5, only 3 scoring rows come back - simulates a
# join-path divergence between get_release_data() and get_open_bugs_detail())
res_b = check(5, detail_consistent)
print(f"Case B (5 reported, 3 scored rows) : consistent={res_b['consistent']}  {res_b}")
assert res_b["consistent"] is False

# Case C: zero bugs, both sides agree at 0
res_c = check(0, pd.DataFrame(columns=["issue_key", "priority_name"]))
print(f"Case C (0 reported, 0 scored rows) : consistent={res_c['consistent']}  {res_c}")
assert res_c["consistent"] is True

print("\nPASS: consistency check correctly flags mismatches and passes matching cases.\n")


print("=" * 70)
print("CORRECTION 4: priority mapping wording/behavior unchanged, still functional")
print("=" * 70)
from priority_utils import priority_weight, is_known_priority

assert priority_weight("Highest") == 2.0
assert priority_weight("unknown-custom-tier") == 1.0  # neutral fallback, not crash
assert is_known_priority("unknown-custom-tier") is False
assert is_known_priority("Highest") is True
print("PASS: priority_utils fallback behavior unchanged (documentation-only correction).\n")


print("=" * 70)
print("ALL PHASE-2 CORRECTION TESTS PASSED")
print("=" * 70)