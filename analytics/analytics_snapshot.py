"""
Analytics Snapshot Builder (Power BI bridge)

Assembles a SINGLE flat dict from the final return values of the four
existing analytics modules, for persistence into a one-row
`analytics_snapshot` MySQL table that Power BI reads directly.

THIS FILE DOES NOT RECOMPUTE, REDERIVE, OR ADJUST ANY SCORE. Every value
below is read verbatim from a key one of the four modules already
produces (backlog_quality.analyze_backlog_quality(),
sprint_planning.SprintPlanningAnalyzer.generate_report(),
dependency_risk.DependencyRiskAnalyzer.generate_report(),
release_readiness.ReleaseReadinessAnalyzer.generate_report()). If a
number here is ever wrong, the bug is in one of those four modules, not
here - this file is a pure re-shaping/flattening step.

KEY-PATH VERIFICATION (done directly against the current source, not
from memory - see chat for the grep-based audit):
  - sprint_report['sprint_completion_pct'] and
    sprint_report['scope_to_velocity_ratio'] are TOP-LEVEL on
    sprint_report, not nested under sprint_report['current_sprint'].
  - release_report['completion_rate'] is TOP-LEVEL on release_report,
    not nested under release_report['release'].
  - sprint_report, dependency_report, and release_report each
    independently use the key name 'risk_level' for THEIR OWN risk
    banding. They are namespaced below (sprint_risk_level /
    dependency_risk_level / release_risk_level) so they don't collide
    once flattened into one row.

FAILURE MODES HANDLED: sprint_report can be exactly {'error': '...'}
(no active/future sprint, or <1 closed sprint of history) and
release_report can be exactly {'error': '...'} (no version/release data
mapped via fact_issue_version). Both are detected via .get('error') and
raise ValueError with the underlying message, rather than raising a
confusing KeyError deeper in this function or silently writing a
snapshot full of Nones.
"""

from datetime import datetime


def build_analytics_snapshot(backlog_result, sprint_report, dependency_report, release_report):
    """Pure function (no DB access, no Jira access) - takes the four
    report objects exactly as main.py already produces them and returns
    a flat dict representing one analytics_snapshot row. Raises
    ValueError (rather than returning a partial/garbage row) if any
    module didn't produce usable data this run.
    """
    if backlog_result is None:
        raise ValueError("backlog_result is None - backlog_quality.analyze_backlog_quality() "
                          "returned nothing (no 'issues' in raw Jira data). Cannot build snapshot.")

    if sprint_report is None:
        raise ValueError("sprint_report is None - cannot build snapshot.")
    if sprint_report.get('error'):
        raise ValueError(f"sprint_report has no usable data: {sprint_report['error']}")

    if dependency_report is None:
        raise ValueError("dependency_report is None - cannot build snapshot.")

    if release_report is None:
        raise ValueError("release_report is None - cannot build snapshot.")
    if release_report.get('error'):
        raise ValueError(f"release_report has no usable data: {release_report['error']}")

    current_sprint = sprint_report['current_sprint']
    historical_velocity = sprint_report['historical_velocity']
    spillover = sprint_report['spillover']
    release = release_report['release']

    return {
        "run_ts": datetime.now(),

        # --- Module 1: Backlog Quality ---
        "backlog_quality_score": backlog_result.get('quality_score'),
        "backlog_total_stories": backlog_result.get('total_stories'),

        # --- Module 2: Sprint Planning ---
        "historical_velocity_avg": historical_velocity.get('average_velocity'),
        "current_sprint_name": current_sprint.get('sprint_name'),
        "planned_sp": current_sprint.get('planned_story_points'),
        "completed_sp": current_sprint.get('completed_story_points'),
        "sprint_completion_pct": sprint_report.get('sprint_completion_pct'),  # top-level, can be None
        "scope_to_velocity_ratio": sprint_report.get('scope_to_velocity_ratio'),
        "sprint_risk_level": sprint_report.get('risk_level'),
        "spillover_count": spillover.get('spillover_count'),
        "spillover_sp": spillover.get('spillover_story_points'),

        # --- Module 3: Dependency Risk ---
        "dependency_risk_score": dependency_report.get('risk_score'),
        "dependency_risk_level": dependency_report.get('risk_level'),
        "blockers_count": dependency_report.get('blockers_count'),
        "blocked_sp": dependency_report.get('blocked_story_points'),
        "high_impact_count": dependency_report.get('high_impact_count'),

        # --- Module 4: Release Readiness ---
        "release_version_name": release.get('version_name'),
        "release_completion_pct": release_report.get('completion_rate'),  # top-level, not release['completion_rate']
        "release_readiness_score": release_report.get('readiness_score'),
        "release_risk_level": release_report.get('risk_level'),
        "release_status": release_report.get('status'),
        "open_bugs": release.get('open_bugs'),
        "remaining_sp": release.get('remaining_story_points'),
        "release_scoped_blocked_sp": release_report.get('release_scoped_blocked_story_points'),
    }
