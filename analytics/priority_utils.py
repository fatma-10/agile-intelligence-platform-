"""
Configurable, common-name priority-severity mapping (shared by
dependency_risk.py and release_readiness.py for priority-weighted scoring).

WHAT THIS IS AND IS NOT
------------------------
This is NOT a universally portable solution. It is a name-based lookup table
covering the priority names that are common across many Jira instances
(Atlassian's default scheme: Highest/High/Medium/Low/Lowest, and the classic
bug-tracking scheme: Blocker/Critical/Major/Minor/Trivial). Any Jira site
that uses different priority names (custom schemes, localized names, a
different number of priority levels) will not be recognized by this table
and will fall through to the neutral default weight below.

Priority *ids* were deliberately NOT used as the basis for this mapping:
ids are assigned per-scheme and are not reliably ordered by severity across
different Jira instances (on the current training instance id=1 happens to
be "Highest", but that is a property of this instance's scheme, not a
general Jira guarantee).

FALLBACK BEHAVIOR
------------------
An unrecognized or missing priority name is treated as neutral
(DEFAULT_WEIGHT = 1.0, the same weight as "Medium") rather than crashing,
being silently dropped from scoring, or being treated as worst-case. This
is a design choice, not a mathematically-neutral no-op: a genuinely severe
priority under an unrecognized name will be under-weighted relative to its
true severity. Callers should surface `is_known_priority()` counts wherever
this mapping is used for scoring, so an unrecognized-priority population is
visible rather than silently blended into "Medium" - both dependency_risk.py
and release_readiness.py do this (blockers_with_unknown_priority /
open_bugs_unknown_priority).

True general-purpose portability (e.g. deriving severity order dynamically
from each Jira instance's priority scheme configuration via the API) is out
of scope for this phase and is tracked as future work.
"""

PRIORITY_WEIGHTS = {
    "highest": 2.0,
    "blocker": 2.0,
    "critical": 2.0,
    "high": 1.5,
    "major": 1.5,
    "medium": 1.0,
    "normal": 1.0,
    "low": 0.75,
    "minor": 0.75,
    "lowest": 0.5,
    "trivial": 0.5,
}

# Only used to pick a single "most severe name" for display purposes -
# never used as a numeric multiplier.
_SEVERITY_ORDER = [
    "highest", "blocker", "critical", "high", "major",
    "medium", "normal", "low", "minor", "lowest", "trivial",
]

DEFAULT_WEIGHT = 1.0  # unrecognized / missing priority name -> treated as Medium


def priority_weight(priority_name):
    """Return the severity multiplier for a priority name. Unknown or
    missing names fall back to DEFAULT_WEIGHT (Medium) rather than
    erroring or being ignored. See module docstring: this is a common-name
    lookup, not a guaranteed-universal mapping."""
    if not priority_name:
        return DEFAULT_WEIGHT
    return PRIORITY_WEIGHTS.get(priority_name.strip().lower(), DEFAULT_WEIGHT)


def is_known_priority(priority_name):
    if not priority_name:
        return False
    return priority_name.strip().lower() in PRIORITY_WEIGHTS


def most_severe_name(priority_names):
    """Given raw priority-name strings as they appear in the source data,
    return the most severe one according to our known ordering (for a
    single 'headline' display value, e.g. 'Highest-priority blocker').
    Unrecognized names sort as least-severe for THIS comparison only -
    they are still counted correctly wherever counts are grouped by their
    own literal name elsewhere."""
    named = [p for p in priority_names if p]
    if not named:
        return None

    def rank(name):
        key = name.strip().lower()
        try:
            return _SEVERITY_ORDER.index(key)
        except ValueError:
            return len(_SEVERITY_ORDER)

    return sorted(named, key=rank)[0]
