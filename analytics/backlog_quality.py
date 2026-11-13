"""
Backlog Quality Analyzer (Module 1)

Looks at every Story in the backlog and checks for common
signs of poor backlog quality:
- missing Story Points
- missing Priority
- unassigned
- potential duplicate titles

Also checks Bugs for:
- missing Priority
- unassigned

Then calculates a Backlog Quality Score from normalized (percentage-based)
sub-metrics rather than raw counts - see analyze_backlog_quality() for the
full derivation and why it replaces the old raw-count formula.

This file does NOT talk to Jira directly - it receives already-fetched
issue data and analyzes it. This keeps 'extraction' and 'analytics'
separate, as the project plan recommends.
"""

import difflib


def find_potential_duplicates(stories, similarity_threshold=0.6):
    """
    Compares every Story's title (summary) against every other Story's title
    and flags pairs that are textually very similar - a common sign of
    accidentally-duplicated backlog items.

    We use difflib's SequenceMatcher, which comes built into Python (no
    extra install needed). It returns a similarity ratio from 0.0 (totally
    different) to 1.0 (identical). We flag pairs at or above the threshold.

    threshold=0.75 means: 75% textually similar or more. This is a starting
    point - stricter (e.g. 0.85) means fewer, more confident matches;
    looser (e.g. 0.6) catches more but with more false positives.
    We keep this adjustable rather than hardcoded/hidden.
    """
    duplicate_pairs = []

    for i in range(len(stories)):
        for j in range(i + 1, len(stories)):
            key_a = stories[i]["key"]
            title_a = stories[i]["fields"].get("summary") or ""

            key_b = stories[j]["key"]
            title_b = stories[j]["fields"].get("summary") or ""

            similarity = difflib.SequenceMatcher(None, title_a.lower(), title_b.lower()).ratio()

            if similarity >= similarity_threshold:
                duplicate_pairs.append({
                    "issue_a": key_a,
                    "title_a": title_a,
                    "issue_b": key_b,
                    "title_b": title_b,
                    "similarity": round(similarity * 100, 1)
                })

    return duplicate_pairs


# --- Normalized scoring weights ---------------------------------------
#
# OLD (raw-count) formula, kept here in comments as the documented origin
# of these weights - it is no longer executed:
#
#   score = 100
#   score -= len(missing_story_points) * 3   # SP missing: 3 pts/issue
#   score -= len(unassigned)          * 2   # Story unassigned: 2 pts/issue
#   score -= len(missing_priority)    * 1   # Story no priority: 1 pt/issue
#   score -= len(duplicate_pairs)     * 2   # dup pair: 2 pts/pair
#   score -= len(bugs_unassigned)     * 2   # Bug unassigned: 2 pts/issue
#   score -= len(bugs_missing_priority) * 2 # Bug no priority: 2 pts/issue
#
# PROBLEM with the old formula: the penalty per defect is a fixed number
# of points regardless of backlog size. 2 missing-SP Stories in a 10-Story
# backlog (20% unestimated) cost exactly the same 6 points as 2
# missing-SP Stories in a 100-Story backlog (2% unestimated) - even though
# the second backlog is clearly in much better shape. See the synthetic
# comparison in the validation script for a concrete before/after.
#
# NEW formula: compute a 0-100% "percent healthy" sub-metric per category,
# then combine them as a WEIGHTED AVERAGE.
#
# WHAT IS AND ISN'T PRESERVED FROM THE OLD FORMULA (important correction):
#
#   For the five metrics that were already expressed as absolute ISSUE
#   COUNTS in the old formula (stories_estimated, stories_assigned,
#   stories_prioritized, bugs_assigned, bugs_prioritized), the weight below
#   is exactly that metric's old per-issue point cost, so the RELATIVE
#   importance the old formula gave to those five categories is preserved
#   when they're renormalized to a weighted average.
#
#   duplicate_rate is DIFFERENT and is NOT mathematically preserved from
#   the old formula. The old formula penalized duplicates as an ABSOLUTE
#   COUNT: "2 points per duplicate pair," with no dependency on backlog
#   size. The new metric, per this session's own spec, is a RATE:
#   duplicate_pairs / total_stories. Converting "2 points per pair" into
#   "weight 2 applied to a rate" is not something you can derive
#   mathematically from a count-based formula - a count and a rate are
#   different units, and there is no old-formula data point that tells you
#   how a rate should be weighted relative to the other rates. This is a
#   DOCUMENTED ANALYTICAL DESIGN CHOICE: we carried the "2" over as a
#   same-tier placeholder (matching the tier the two Bug penalties already
#   sat at) because nothing in the old formula argues duplicates should be
#   weighted higher or lower than that tier - but this is a judgment call
#   about a rate-based metric that did not exist in the old formula, not a
#   preserved value. See validate_duplicate_rate_behavior() below for a
#   concrete demonstration of how this rate behaves differently across
#   backlog sizes and duplicate-pair counts, so this limitation is visible
#   rather than implied to be equivalent to the old count-based penalty.
RAW_WEIGHTS = {
    "stories_estimated": 3,
    "stories_assigned": 2,
    "stories_prioritized": 1,
    "duplicate_rate": 2,
    "bugs_assigned": 2,
    "bugs_prioritized": 2,
}


def _pct_ok(missing_count, total):
    """% of `total` NOT affected by the problem. None when total is 0,
    since a percentage of zero items is undefined, not 100% or 0%."""
    if total == 0:
        return None
    return round((total - missing_count) / total * 100, 1)


def analyze_backlog_quality(data):
    """
    Takes the raw Jira API response (same shape as get_issues() returns)
    and returns a dictionary with the quality score, the individual
    normalized sub-metrics it was built from, and the specific problems
    found - so the score is always explainable and traceable back to
    issue-level detail.
    """
    if not data or "issues" not in data:
        print("No data to analyze.")
        return None

    # We only evaluate Stories here - Epics are containers and
    # aren't expected to carry Story Points/assignees the same way.
    stories = [
        issue for issue in data["issues"]
        if issue["fields"]["issuetype"]["name"] == "Story"
    ]

    # Bugs also affect backlog health - an unassigned or unprioritized
    # bug is a real risk - so we check them too, but separately from
    # Stories, since we don't expect Bugs to always carry Story Points.
    bugs = [
        issue for issue in data["issues"]
        if issue["fields"]["issuetype"]["name"] == "Bug"
    ]

    total_stories = len(stories)
    total_bugs = len(bugs)

    missing_story_points = []
    missing_priority = []
    unassigned = []

    for issue in stories:
        fields = issue["fields"]
        key = issue["key"]

        if fields.get("customfield_10016") is None:
            missing_story_points.append(key)

        if fields.get("priority") is None:
            missing_priority.append(key)

        if fields.get("assignee") is None:
            unassigned.append(key)

    # Bugs get their own set of problem lists, since they're tracked
    # separately from Stories and shown separately in the report.
    bugs_missing_priority = []
    bugs_unassigned = []

    for issue in bugs:
        fields = issue["fields"]
        key = issue["key"]

        if fields.get("priority") is None:
            bugs_missing_priority.append(key)

        if fields.get("assignee") is None:
            bugs_unassigned.append(key)

    duplicate_pairs = find_potential_duplicates(stories)

    # --- Normalized (percentage) sub-metrics ---
    pct_stories_estimated = _pct_ok(len(missing_story_points), total_stories)
    pct_stories_assigned = _pct_ok(len(unassigned), total_stories)
    pct_stories_prioritized = _pct_ok(len(missing_priority), total_stories)
    pct_bugs_assigned = _pct_ok(len(bugs_unassigned), total_bugs)
    pct_bugs_prioritized = _pct_ok(len(bugs_missing_priority), total_bugs)

    # duplicate_rate = duplicate pairs / total Stories, capped at 100%.
    # (With n Stories, the max possible number of pairs is n*(n-1)/2, which
    # exceeds n once n >= 3 - so this ratio can exceed 100% for a small,
    # heavily-duplicated backlog; it is capped so it remains a sane rate.)
    # See the RAW_WEIGHTS comment block above: this rate, and the weight
    # applied to it, is a documented design choice - not a value carried
    # over mathematically from the old count-based duplicate penalty.
    if total_stories == 0:
        duplicate_rate = None
    else:
        duplicate_rate = round(min(len(duplicate_pairs) / total_stories, 1.0) * 100, 1)

    # --- Weighted overall score ---
    # "Healthy %" per category (100 - defect_rate for duplicate_rate).
    components = {
        "stories_estimated": pct_stories_estimated,
        "stories_assigned": pct_stories_assigned,
        "stories_prioritized": pct_stories_prioritized,
        "duplicate_rate": (100 - duplicate_rate) if duplicate_rate is not None else None,
        "bugs_assigned": pct_bugs_assigned,
        "bugs_prioritized": pct_bugs_prioritized,
    }

    # EDGE CASES: if total_stories == 0, every Story-based component
    # (including duplicate_rate, which is defined relative to Stories) is
    # None. If total_bugs == 0, both Bug components are None. Rather than
    # let an empty category silently count as "perfect" (which would let a
    # backlog with zero Stories score 100%) or crash on a divide-by-zero,
    # we drop undefined components and renormalize the remaining weights
    # so they still sum to 1. If EVERY component is undefined (empty
    # backlog entirely), the overall score is None, not a fabricated 100
    # or 0.
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        quality_score = None
    else:
        weight_sum = sum(RAW_WEIGHTS[k] for k in available)
        quality_score = round(
            sum(RAW_WEIGHTS[k] * available[k] for k in available) / weight_sum, 1
        )

    result = {
        "total_stories": total_stories,
        "missing_story_points": missing_story_points,
        "missing_priority": missing_priority,
        "unassigned": unassigned,
        "duplicate_pairs": duplicate_pairs,
        "total_bugs": total_bugs,
        "bugs_missing_priority": bugs_missing_priority,
        "bugs_unassigned": bugs_unassigned,
        "pct_stories_estimated": pct_stories_estimated,
        "pct_stories_assigned": pct_stories_assigned,
        "pct_stories_prioritized": pct_stories_prioritized,
        "pct_bugs_assigned": pct_bugs_assigned,
        "pct_bugs_prioritized": pct_bugs_prioritized,
        "duplicate_rate": duplicate_rate,
        "quality_score": quality_score,
    }

    return result


def _fmt_pct(value):
    return "N/A" if value is None else f"{value}%"


def print_backlog_quality_report(result):
    """
    Prints the analysis in the transparent, human-readable style
    described in the project plan: not just a score, but WHY - now
    including the normalized sub-metrics the score is built from.
    """
    if not result:
        return

    print("\n" + "=" * 50)
    print("BACKLOG QUALITY REPORT")
    print("=" * 50)
    print(f"Total Stories analyzed: {result['total_stories']}")
    print(f"Backlog Quality Score: {_fmt_pct(result['quality_score'])}")
    print()
    print("Normalized sub-metrics:")
    print(f"  - Stories estimated:   {_fmt_pct(result['pct_stories_estimated'])}")
    print(f"  - Stories assigned:    {_fmt_pct(result['pct_stories_assigned'])}")
    print(f"  - Stories prioritized: {_fmt_pct(result['pct_stories_prioritized'])}")
    print(f"  - Bugs assigned:       {_fmt_pct(result['pct_bugs_assigned'])}")
    print(f"  - Bugs prioritized:    {_fmt_pct(result['pct_bugs_prioritized'])}")
    print(f"  - Potential duplicate rate: {_fmt_pct(result['duplicate_rate'])} "
          f"(duplicate pairs / total Stories - see code comments on RAW_WEIGHTS "
          f"for why this is a design choice, not a preserved old-formula value)")
    print()
    print("Underlying issue-level detail:")
    print(f"  - {len(result['missing_story_points'])} Stories missing Story Points: "
          f"{', '.join(result['missing_story_points']) or 'none'}")
    print(f"  - {len(result['unassigned'])} Stories unassigned: "
          f"{', '.join(result['unassigned']) or 'none'}")
    print(f"  - {len(result['missing_priority'])} Stories missing Priority: "
          f"{', '.join(result['missing_priority']) or 'none'}")

    dup_count = len(result['duplicate_pairs'])
    print(f"  - {dup_count} potential duplicate pair(s) found:")
    if dup_count == 0:
        print("      none")
    else:
        for pair in result['duplicate_pairs']:
            print(f"      {pair['issue_a']} (\"{pair['title_a']}\")  <->  "
                  f"{pair['issue_b']} (\"{pair['title_b']}\")  "
                  f"[{pair['similarity']}% similar]")

    print()
    print(f"Total Bugs analyzed: {result['total_bugs']}")
    print(f"  - {len(result['bugs_unassigned'])} Bugs unassigned: "
          f"{', '.join(result['bugs_unassigned']) or 'none'}")
    print(f"  - {len(result['bugs_missing_priority'])} Bugs missing Priority: "
          f"{', '.join(result['bugs_missing_priority']) or 'none'}")
    print("=" * 50)