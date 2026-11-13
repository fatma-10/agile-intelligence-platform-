"""
Tests for sprint_completion_pct, isolated from DB/Jira - exercises the
same calculation logic that lives inline in generate_report().
"""

def compute_completion_pct(planned, completed):
    if planned and planned > 0:
        return round((completed / planned) * 100, 1)
    return None

print("=== Test A: real-dataset-shaped case (MBA Sprint 4) ===")
planned, completed = 44.0, 0.0
pct = compute_completion_pct(planned, completed)
print(f"planned={planned}, completed={completed} -> sprint_completion_pct={pct}")
assert pct == 0.0, "Expected 0.0% when nothing is completed yet but scope exists"
print("PASS")

print("\n=== Test B (synthetic): zero-planned-SP edge case ===")
planned, completed = 0, 0
pct = compute_completion_pct(planned, completed)
print(f"planned={planned}, completed={completed} -> sprint_completion_pct={pct}")
assert pct is None, "Expected None (not 0.0, not NaN, not a crash) when nothing is planned"
print("PASS - no division-by-zero, no NaN, explicit None for 'not meaningful yet'")

print("\n=== Test C (synthetic): partial completion, sanity check on the formula ===")
planned, completed = 20.0, 15.0
pct = compute_completion_pct(planned, completed)
print(f"planned={planned}, completed={completed} -> sprint_completion_pct={pct}")
assert pct == 75.0
print("PASS")

print("\nAll sprint_completion_pct tests passed.")
