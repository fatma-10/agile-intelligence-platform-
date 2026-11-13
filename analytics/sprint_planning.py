"""
Sprint Planning Optimizer (Module 2)

Analyzes sprint capacity vs historical velocity

PHASE 4 ADDITIONS (sprint-history analytics - informational, do NOT alter
sprint_completion_pct, scope_to_velocity_ratio, or their risk bands, all
of which are computed exactly as before):

1. Spillover detection - uses the new fact_issue_sprint bridge table
   (historical sprint membership) together with the existing
   fact_issue.sprint_id (current/final sprint) to flag issues that were
   historically in an earlier sprint before landing in their current one.
   See _compute_spillover() for the exact definition and the chronology
   method used.

2. Historical Sprint Completion Rate (snapshot approximation) - see
   _compute_historical_completion_rate() for exactly what this does and
   does NOT measure. This is explicitly NOT a "commitment accuracy" or
   "commitment reliability" metric: the current Jira extraction has no
   changelog/scope-at-sprint-start data, so this reports a snapshot of
   TODAY's completion status for issues historically tied to a sprint,
   not the sprint's own original outcome.

CHRONOLOGY METHOD (both features above): ordering between sprints is
determined ONLY by dim_sprint.start_date, the one reliable chronology
attribute the schema provides. Sprint id is technical identity only (not
chronology - ids are not guaranteed contiguous or issued in time order).
Sprint name is descriptive only (this schema/dataset has confirmed
duplicate sprint names under different ids - see
transform_jira_data.py::detect_duplicate_sprint_names() - so name can
never be used to infer either identity or order).

INHERITED LIMITATION (not introduced by Phase 4): "done" is still
identified by the localized status name 'Terminé', matching how
get_historical_velocity() and analyze_current_sprint() already defined
completion before this phase. Replacing this with a portable
statusCategory-based check is tracked as future portability work (see
project handoff notes) and is out of scope for Phase 4, which is about
sprint-history preservation, not Jira status-model portability.
"""

import pandas as pd
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))

from sqlalchemy import create_engine, text
from settings import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

# Same hardcoded localized status name already used elsewhere in this
# file's SQL (get_historical_velocity, analyze_current_sprint). Not a
# new assumption - just named as a constant here so the two new Phase 4
# functions below share it explicitly instead of re-hardcoding it.
DONE_STATUS_NAME = 'Terminé'


class SprintPlanningAnalyzer:
    def __init__(self):
        connection_string = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}/{MYSQL_DATABASE}"
        self.engine = create_engine(connection_string)
    
    def get_historical_velocity(self, sprint_count=4):
        """Calculate average story points ACTUALLY COMPLETED in last N closed sprints.

        Previously this summed every SP attributed to a closed sprint,
        regardless of whether the issue was ever finished - i.e. it measured
        "SP whose final sprint pointer is sprint X," not "SP delivered in
        sprint X." Those differ whenever an issue was left in a closed
        sprint without reaching Terminé (carried over, abandoned, etc.),
        and the gap is especially relevant here given the duplicate-sprint
        entities already present in this data.

        Filter scope: this counts completed SP across ALL issue types
        (Story, Bug, Task, ...), not just Stories. The query never filtered
        by issue type before this fix, and velocity as a capacity metric
        should reflect everything the team actually delivered, not just
        one issue type - narrowing to Stories only would be a separate,
        unrequested behavior change bundled into this fix.
        """
        query = f"""
        SELECT 
            ds.sprint_name,
            ds.sprint_state,
            COALESCE(SUM(CASE WHEN fi.status_id IN (
                SELECT status_id FROM dim_status WHERE status_name = 'Terminé'
            ) THEN fi.story_points ELSE 0 END), 0) as total_story_points
        FROM fact_issue fi
        JOIN dim_sprint ds ON fi.sprint_id = ds.sprint_id
        WHERE ds.sprint_state = 'closed'
        GROUP BY ds.sprint_id, ds.sprint_name, ds.sprint_state
        ORDER BY ds.sprint_id DESC
        LIMIT {sprint_count}
        """
        
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return None
        
        avg_velocity = df['total_story_points'].mean()
        
        return {
            'average_velocity': round(avg_velocity, 1),
            'sprint_count': len(df),
            'sprints': df.to_dict('records')
        }
    
    def analyze_current_sprint(self):
        """Analyze the current/active sprint.

        Explicitly prefers a sprint with state='active' over one with
        state='future' when both exist (a normal situation - the current
        sprint plus a next sprint already being planned). Without the
        ORDER BY, this previously had no guarantee of picking the active
        one over the future one.
        """
        query = """
        SELECT 
            ds.sprint_id,
            ds.sprint_name,
            ds.sprint_state,
            COALESCE(SUM(fi.story_points), 0) as planned_story_points,
            COUNT(DISTINCT fi.issue_id) as issue_count,
            SUM(CASE WHEN fi.status_id IN (SELECT status_id FROM dim_status WHERE status_name = 'Terminé') 
                THEN fi.story_points ELSE 0 END) as completed_story_points
        FROM fact_issue fi
        JOIN dim_sprint ds ON fi.sprint_id = ds.sprint_id
        WHERE ds.sprint_state IN ('active', 'future')
        GROUP BY ds.sprint_id, ds.sprint_name, ds.sprint_state
        ORDER BY FIELD(ds.sprint_state, 'active', 'future')
        """
        
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return None
        
        return df.iloc[0].to_dict()

    def get_issue_sprint_history(self):
        """Phase 4: every HISTORICAL (issue, sprint) association from the
        new fact_issue_sprint bridge, joined with:
          - that historical sprint's own name/start_date (for chronology
            and display),
          - the issue's CURRENT/FINAL sprint (fact_issue.sprint_id,
            unchanged) and that current sprint's name/start_date,
          - the issue's status and story points.

        One row per (issue, historical sprint) pair - i.e. an issue that
        has been in 2 sprints historically produces 2 rows here, each
        carrying the SAME current-sprint columns (since fact_issue.sprint_id
        is one value per issue). This is the shape _compute_spillover()
        and _compute_historical_completion_rate() are written against.

        LEFT JOIN on the current sprint: an issue's fact_issue.sprint_id
        can be NULL (e.g. backlog items never assigned to a sprint), and
        that must not drop the issue's historical rows - it just means
        spillover can't be evaluated for it (see _compute_spillover).
        """
        query = """
        SELECT
            fis.issue_key,
            fis.sprint_id AS historical_sprint_id,
            hs.sprint_name AS historical_sprint_name,
            hs.start_date AS historical_start_date,
            fi.sprint_id AS current_sprint_id,
            cs.sprint_name AS current_sprint_name,
            cs.start_date AS current_start_date,
            fi.status_id,
            st.status_name,
            fi.story_points
        FROM fact_issue_sprint fis
        JOIN fact_issue fi ON fi.issue_key = fis.issue_key
        JOIN dim_sprint hs ON hs.sprint_id = fis.sprint_id
        LEFT JOIN dim_sprint cs ON cs.sprint_id = fi.sprint_id
        LEFT JOIN dim_status st ON st.status_id = fi.status_id
        """
        try:
            return pd.read_sql(query, self.engine)
        except Exception as e:
            print(f"Error getting issue sprint history: {e}")
            return pd.DataFrame()

    @staticmethod
    def _compute_spillover(history_df):
        """Pure function (no DB access) so this can be unit-tested with
        synthetic data - see tests/test_phase4_sprint_history.py.

        DEFINITION: an issue is spillover when it has at least one
        HISTORICAL sprint association (a fact_issue_sprint row) whose
        sprint start_date is chronologically EARLIER than the start_date
        of the issue's CURRENT/FINAL sprint (fact_issue.sprint_id).

        - Chronology comes ONLY from dim_sprint.start_date. Sprint id and
          sprint name are never used to infer order (see module
          docstring).
        - An issue with only ONE historical sprint association can never
          be spillover: there is no earlier association to compare
          against (Scenario B in the test suite).
        - Duplicate sprint NAMES never cause spillover by themselves -
          only an actual earlier start_date does (Scenario C/D).
        - An issue whose current sprint is NULL, or whose current sprint
          has no start_date, cannot be evaluated (chronology undefined
          for it) - it is excluded from both the spillover and
          not-spillover counts and reported separately as
          'unevaluable_issues', rather than silently defaulting to
          not-spillover.
        """
        empty_result = {
            "spillover_count": 0,
            "spillover_story_points": 0.0,
            "spillover_issues": [],
            "unevaluable_issues": [],
        }
        if history_df is None or history_df.empty:
            return empty_result

        spillover_issues = {}
        unevaluable = set()

        for issue_key, group in history_df.groupby("issue_key"):
            current_sprint_id = group["current_sprint_id"].iloc[0]
            current_start = group["current_start_date"].iloc[0]

            if pd.isna(current_sprint_id) or pd.isna(current_start):
                unevaluable.add(issue_key)
                continue

            earlier = group[
                (group["historical_sprint_id"] != current_sprint_id)
                & group["historical_start_date"].notna()
                & (group["historical_start_date"] < current_start)
            ]

            if not earlier.empty:
                sp = group["story_points"].iloc[0]
                spillover_issues[issue_key] = {
                    "issue_key": issue_key,
                    "current_sprint": group["current_sprint_name"].iloc[0],
                    "earlier_sprints": sorted(set(earlier["historical_sprint_name"].dropna().tolist())),
                    "story_points": None if pd.isna(sp) else float(sp),
                }

        records = list(spillover_issues.values())
        sp_values = [r["story_points"] for r in records if r["story_points"] is not None]

        return {
            "spillover_count": len(records),
            "spillover_story_points": round(sum(sp_values), 1) if sp_values else 0.0,
            "spillover_issues": records,
            "unevaluable_issues": sorted(unevaluable),
        }

    @staticmethod
    def _compute_historical_completion_rate(history_df, done_status_name=DONE_STATUS_NAME):
        """Pure function (no DB access).

        Historical Sprint Completion Rate (snapshot approximation).

        Definition: the percentage of unique issues HISTORICALLY
        associated with a sprint (per fact_issue_sprint) that are
        CURRENTLY sitting in a completed/done status.

            completed_issues_now / total_historically_associated_issues * 100

        WHAT THIS DOES NOT MEASURE: this is not the sprint's original
        commitment-vs-delivery outcome. It does not know what was
        committed at sprint start, what was added mid-sprint, or what was
        removed - none of that is available without Jira changelog/
        history data, which this extraction does not fetch. It is a
        snapshot of where those issues stand TODAY, so do not label or
        treat this as "Commitment Accuracy" or "Commitment Reliability".

        Grain: computed per historical sprint (grouping by
        historical_sprint_id), deduplicated by issue_key within that
        group in case of any join fan-out, since fact_issue_sprint is
        already unique per (issue, sprint) but this keeps the function
        correct even given an arbitrary/duplicated history_df.
        """
        if history_df is None or history_df.empty:
            return {}

        result = {}
        for sprint_id, group in history_df.groupby("historical_sprint_id"):
            unique_issues = group.drop_duplicates(subset=["issue_key"])
            total = len(unique_issues)
            completed = int((unique_issues["status_name"] == done_status_name).sum())
            rate = round(completed / total * 100, 1) if total > 0 else None
            result[sprint_id] = {
                "sprint_name": group["historical_sprint_name"].iloc[0],
                "total_issues": total,
                "completed_issues": completed,
                "completion_rate_snapshot": rate,
            }
        return result

    def generate_report(self):
        """Generate complete sprint planning report"""
        report = {}
        
        # Get historical velocity
        velocity = self.get_historical_velocity()
        if not velocity:
            report['error'] = 'Not enough historical data'
            return report
        
        report['historical_velocity'] = velocity
        
        # Get current sprint
        current = self.analyze_current_sprint()
        if not current:
            report['error'] = 'No active or future sprint found'
            return report
        
        report['current_sprint'] = current
        
        planned = current.get('planned_story_points', 0)
        completed = current.get('completed_story_points', 0)
        
        # Sprint Completion % = how much of THIS sprint's own planned scope
        # is done so far. This is deliberately a different question from
        # Scope-to-Velocity Ratio below: completion looks inward (planned
        # vs completed within the same sprint), while scope-to-velocity
        # looks outward (this sprint's planned scope vs historical
        # throughput from OTHER sprints). Keeping them separate numbers
        # avoids blending "are we on track" with "did we commit to too
        # much" into one black-box score.
        #
        # planned_story_points = 0 is a real, expected state (e.g. a
        # 'future' sprint that hasn't had stories pointed into it yet).
        # 0/0 is mathematically undefined, not "0% done" - reporting 0.0%
        # there would misleadingly suggest work exists and none of it is
        # finished, when actually no completion percentage is meaningful
        # yet. So this reports None or completion, and print_sprint_report
        # renders None as an explicit "N/A (no story points planned)"
        # rather than silently showing 0.0%.
        if planned and planned > 0:
            sprint_completion_pct = round((completed / planned) * 100, 1)
        else:
            sprint_completion_pct = None
        
        report['sprint_completion_pct'] = sprint_completion_pct
        
        # This ratio compares PLANNED SCOPE to HISTORICAL THROUGHPUT.
        # It is deliberately NOT called "capacity utilization" - that term
        # implies knowledge of individual team member availability (PTO,
        # part-time work, etc.), which this data does not contain. Calling
        # it Scope-to-Velocity Ratio is the accurate description of what's
        # actually being measured.
        avg = velocity['average_velocity']
        
        if avg > 0:
            scope_to_velocity_ratio = (planned / avg) * 100
        else:
            scope_to_velocity_ratio = 0
        
        report['scope_to_velocity_ratio'] = round(scope_to_velocity_ratio, 1)
        
        # Determine risk level
        if scope_to_velocity_ratio > 130:
            report['risk_level'] = 'HIGH'
            report['risk_color'] = '🔴'
            report['recommendation'] = 'Sprint scope significantly exceeds historical velocity. Consider moving some stories to next sprint.'
        elif scope_to_velocity_ratio > 110:
            report['risk_level'] = 'MEDIUM'
            report['risk_color'] = '🟡'
            report['recommendation'] = 'Sprint scope somewhat exceeds historical velocity. Review scope and dependencies.'
        elif scope_to_velocity_ratio < 70:
            report['risk_level'] = 'LOW'
            report['risk_color'] = '🟢'
            report['recommendation'] = 'Sprint scope is well under historical velocity. Consider adding more work.'
        else:
            report['risk_level'] = 'OPTIMAL'
            report['risk_color'] = '🟢'
            report['recommendation'] = 'Sprint scope is well balanced relative to historical velocity.'

        # --- Phase 4: sprint-history context (informational only - does
        # NOT feed into sprint_completion_pct, scope_to_velocity_ratio, or
        # risk_level/recommendation above, all of which are unchanged) ---
        history_df = self.get_issue_sprint_history()
        report['spillover'] = self._compute_spillover(history_df)
        report['historical_sprint_completion'] = self._compute_historical_completion_rate(history_df)

        return report

def print_sprint_report(report):
    """Print the sprint planning report"""
    if not report or report.get('error'):
        print(f"❌ {report.get('error', 'Error generating report')}")
        return
    
    print("\n" + "=" * 60)
    print("SPRINT PLANNING REPORT")
    print("=" * 60)
    
    # Historical velocity
    v = report['historical_velocity']
    print(f"\n📊 Historical Velocity (Last {v['sprint_count']} Sprints):")
    print(f"   Average: {v['average_velocity']} Story Points")
    print("   Sprint details:")
    for sprint in v['sprints']:
        print(f"     - {sprint['sprint_name']}: {sprint['total_story_points']} SP")
    
    # Current sprint
    c = report['current_sprint']
    print(f"\n📋 Current Sprint: {c['sprint_name']}")
    print(f"   Planned Story Points: {c['planned_story_points']}")
    print(f"   Issues: {c['issue_count']}")
    print(f"   Completed: {c['completed_story_points']} SP")
    
    completion_pct = report.get('sprint_completion_pct')
    if completion_pct is None:
        print(f"   Sprint Completion: N/A (no story points planned)")
    else:
        print(f"   Sprint Completion: {completion_pct}%")
    
    # Risk assessment
    print(f"\n⚠️  Planning Risk Assessment:")
    print(f"   Scope-to-Velocity Ratio: {report['scope_to_velocity_ratio']}%")
    print(f"   Risk Level: {report['risk_color']} {report['risk_level']}")
    print(f"   Recommendation: {report['recommendation']}")

    # --- Phase 4: sprint-history context ---
    spillover = report.get('spillover')
    if spillover is not None:
        print(f"\n🔁 Spillover (issues carried from an earlier sprint into "
              f"their current/final sprint, by dim_sprint.start_date):")
        print(f"   Spillover issues: {spillover['spillover_count']}")
        print(f"   Spillover Story Points: {spillover['spillover_story_points']}")
        if spillover['spillover_issues']:
            for item in spillover['spillover_issues']:
                sp_display = 'N/A' if item['story_points'] is None else item['story_points']
                earlier = ', '.join(item['earlier_sprints'])
                print(f"     - {item['issue_key']} ({sp_display} SP): "
                      f"carried from [{earlier}] into {item['current_sprint']}")
        if spillover['unevaluable_issues']:
            print(f"   ⚠️  {len(spillover['unevaluable_issues'])} issue(s) with sprint "
                  f"history could not be evaluated for spillover (no current sprint, "
                  f"or current sprint has no start_date): "
                  f"{', '.join(spillover['unevaluable_issues'])}")

    completion = report.get('historical_sprint_completion')
    if completion:
        print(f"\n📈 Historical Sprint Completion Rate (snapshot approximation - "
              f"see code comments: this reflects issues' CURRENT status, not "
              f"original sprint commitment/outcome):")
        for sprint_id, data in completion.items():
            rate_display = 'N/A' if data['completion_rate_snapshot'] is None else f"{data['completion_rate_snapshot']}%"
            print(f"     - {data['sprint_name']}: {data['completed_issues']}/{data['total_issues']} "
                  f"issues currently done ({rate_display})")

    print("\n" + "=" * 60)