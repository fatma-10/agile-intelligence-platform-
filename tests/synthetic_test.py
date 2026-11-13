"""
Synthetic test for the get_historical_velocity() fix.

Doesn't hit real MySQL - builds a tiny in-memory fact_issue / dim_sprint /
dim_status dataset that mimics the MBA project's shape (including a case
where a closed sprint contains SP that was never actually finished), then
runs the OLD query logic and the NEW query logic against it in pandas to
show the numbers diverge.
"""
import pandas as pd

# --- dim_status ---
dim_status = pd.DataFrame([
    {"status_id": 1, "status_name": "Terminé"},
    {"status_id": 2, "status_name": "In Progress"},
    {"status_id": 3, "status_name": "To Do"},
])

# --- dim_sprint (two closed sprints) ---
dim_sprint = pd.DataFrame([
    {"sprint_id": 10, "sprint_name": "MBA Sprint 1", "sprint_state": "closed"},
    {"sprint_id": 11, "sprint_name": "MBA Sprint 2", "sprint_state": "closed"},
])

# --- fact_issue ---
# Sprint 1: 20 SP attributed, but only 14 SP actually finished (6 SP carried
# over/abandoned but never re-pointed to a later sprint - the exact gap
# this fix targets).
# Sprint 2: 18 SP attributed, all 18 finished (old and new should agree here).
fact_issue = pd.DataFrame([
    {"issue_key": "MBA-1", "sprint_id": 10, "status_id": 1, "story_points": 5},   # Terminé
    {"issue_key": "MBA-2", "sprint_id": 10, "status_id": 1, "story_points": 9},   # Terminé
    {"issue_key": "MBA-3", "sprint_id": 10, "status_id": 2, "story_points": 4},   # In Progress - NOT done
    {"issue_key": "MBA-4", "sprint_id": 10, "status_id": 3, "story_points": 2},   # To Do - NOT done
    {"issue_key": "MBA-5", "sprint_id": 11, "status_id": 1, "story_points": 10},  # Terminé
    {"issue_key": "MBA-6", "sprint_id": 11, "status_id": 1, "story_points": 8},   # Terminé
])

def old_logic(fact_issue, dim_sprint, sprint_count=4):
    merged = fact_issue.merge(dim_sprint, on="sprint_id")
    closed = merged[merged["sprint_state"] == "closed"]
    grouped = closed.groupby(["sprint_id", "sprint_name", "sprint_state"], as_index=False)["story_points"].sum()
    grouped = grouped.rename(columns={"story_points": "total_story_points"})
    grouped = grouped.sort_values("sprint_id", ascending=False).head(sprint_count)
    return grouped

def new_logic(fact_issue, dim_sprint, dim_status, sprint_count=4):
    terminee_ids = set(dim_status[dim_status["status_name"] == "Terminé"]["status_id"])
    fi = fact_issue.copy()
    fi["counted_sp"] = fi.apply(
        lambda r: r["story_points"] if r["status_id"] in terminee_ids else 0, axis=1
    )
    merged = fi.merge(dim_sprint, on="sprint_id")
    closed = merged[merged["sprint_state"] == "closed"]
    grouped = closed.groupby(["sprint_id", "sprint_name", "sprint_state"], as_index=False)["counted_sp"].sum()
    grouped = grouped.rename(columns={"counted_sp": "total_story_points"})
    grouped = grouped.sort_values("sprint_id", ascending=False).head(sprint_count)
    return grouped

old_result = old_logic(fact_issue, dim_sprint)
new_result = new_logic(fact_issue, dim_sprint, dim_status)

print("=== OLD (unfiltered) ===")
print(old_result[["sprint_name", "total_story_points"]].to_string(index=False))
print(f"average_velocity = {round(old_result['total_story_points'].mean(), 1)}")

print("\n=== NEW (Terminé-only) ===")
print(new_result[["sprint_name", "total_story_points"]].to_string(index=False))
print(f"average_velocity = {round(new_result['total_story_points'].mean(), 1)}")

diff = old_result["total_story_points"].sum() - new_result["total_story_points"].sum()
print(f"\nTotal SP difference across sprints: {diff}")
assert diff > 0, "Expected the filtered total to be lower where incomplete SP exists"
print("PASS: filtered velocity is strictly lower than unfiltered where incomplete SP was included.")
