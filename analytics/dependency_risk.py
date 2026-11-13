"""
Dependency Intelligence & Sprint Risk Analyzer (Module 3)

Analyzes issue dependencies and identifies blockers.

Risk scoring (see generate_report()):
  OLD: a 3-bucket step function (100 / 70 / 30) based on blocker count,
       high-impact count, and blocked SP crossing fixed thresholds.
  NEW: an additive penalty model (100 - capped_penalty_relationships -
       capped_penalty_sp), where each BLOCKING RELATIONSHIP is weighted
       by the priority of the blocking issue in that relationship. See
       generate_report() for the full derivation, and priority_utils.py
       for the priority -> severity-weight mapping.

SCORING GRAIN (important): fact_issue_link, and therefore blockers_df, is a
table of blocking RELATIONSHIPS (one row per blocker->blocked edge), not a
table of distinct blocker issues. If a single blocker issue blocks three
separate target issues, it appears as three rows/relationships, and its
priority weight is added to the penalty three times - once per relationship.
This is intentional, not a bug: an issue that is blocking three things is
arguably creating more total risk than one blocking a single thing, and the
relationship-count-based penalty already reflected "more blocking edges =
more penalty" in the old formula too (raw blockers_count = row count, not
distinct-blocker count). The wording throughout this module says "blocking
relationships" rather than "blocker issues" specifically to keep this grain
explicit rather than implying deduplication that doesn't happen. The
existing get_high_impact_blockers() query, which groups by blocker key to
identify a distinct issue blocking multiple targets, is unchanged.
"""

import pandas as pd
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))

from sqlalchemy import create_engine, text
from settings import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
from priority_utils import priority_weight, most_severe_name, is_known_priority

# --- Additive risk-score tuning constants -------------------------------
# These replace the old 100/70/30 step function. Both are simple per-unit
# point costs, each capped independently so neither factor alone can drive
# the score below 0, and so the two concepts (relationship severity vs.
# amount of blocked work) stay visibly separate rather than being folded
# into one opaque coefficient.
BASE_POINTS_PER_RELATIONSHIP = 5   # cost of one Medium-priority blocking relationship
MAX_RELATIONSHIP_PENALTY = 50      # blocking relationships alone can cost at most 50 pts
POINTS_PER_BLOCKED_SP = 1.5        # cost per Story Point sitting behind a blocker
MAX_SP_PENALTY = 40                # blocked SP alone can cost at most 40 pts

# New risk-level bands over the continuous 0-100 score (replaces the old
# fixed 100/70/30 output values, which were also used as the band labels).
RISK_BAND_LOW_MIN = 80
RISK_BAND_MEDIUM_MIN = 50


class DependencyRiskAnalyzer:
    def __init__(self):
        connection_string = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}/{MYSQL_DATABASE}"
        self.engine = create_engine(connection_string)

    def get_blockers(self):
        """Get all blocking RELATIONSHIPS (one row per blocker->blocked
        edge - see module docstring on scoring grain), including the
        blocking issue's priority name for that relationship (needed for
        priority-weighted scoring). Priority is LEFT JOINed since a
        blocker may have no priority set."""
        query = """
        SELECT 
            fil.source_issue_key as blocker_key,
            fil.target_issue_key as blocked_key,
            fil.link_type,
            blocker.summary as blocker_summary,
            blocked.summary as blocked_summary,
            blocked.story_points as blocked_story_points,
            blocker.status_id as blocker_status_id,
            bs.status_name as blocker_status,
            bp.priority_name as blocker_priority
        FROM fact_issue_link fil
        JOIN fact_issue blocker ON fil.source_issue_key = blocker.issue_key
        JOIN fact_issue blocked ON fil.target_issue_key = blocked.issue_key
        JOIN dim_status bs ON blocker.status_id = bs.status_id
        LEFT JOIN dim_priority bp ON blocker.priority_id = bp.priority_id
        WHERE fil.link_type LIKE '%%block%%'
           OR fil.link_type LIKE '%%Block%%'
        """

        try:
            df = pd.read_sql(query, self.engine)
            return df
        except Exception as e:
            print(f"Error getting blockers: {e}")
            # Fallback query without LIKE (priority join preserved so the
            # rest of generate_report() keeps working even on this path)
            fallback_query = """
            SELECT 
                fil.source_issue_key as blocker_key,
                fil.target_issue_key as blocked_key,
                fil.link_type,
                blocker.summary as blocker_summary,
                blocked.summary as blocked_summary,
                blocked.story_points as blocked_story_points,
                blocker.status_id as blocker_status_id,
                bs.status_name as blocker_status,
                bp.priority_name as blocker_priority
            FROM fact_issue_link fil
            JOIN fact_issue blocker ON fil.source_issue_key = blocker.issue_key
            JOIN fact_issue blocked ON fil.target_issue_key = blocked.issue_key
            JOIN dim_status bs ON blocker.status_id = bs.status_id
            LEFT JOIN dim_priority bp ON blocker.priority_id = bp.priority_id
            """
            return pd.read_sql(fallback_query, self.engine)

    def get_high_impact_blockers(self):
        """Find blocker ISSUES affecting MULTIPLE target issues (distinct
        from get_blockers(), which returns one row per relationship - this
        method groups by blocker key specifically to identify a single
        issue responsible for multiple blocking relationships).

        The HAVING clause is load-bearing: without it, this returns every
        blocking relationship regardless of how many issues it actually
        affects, which silently redefines "high impact" as "is a blocker at
        all" - that was a real bug here (confirmed: 9 of 11 total blocking
        relationships were being counted as "high impact" even though most
        blocked only 1 issue). Unchanged this session.
        """
        query = """
        SELECT 
            fil.source_issue_key as blocker_key,
            blocker.summary as blocker_summary,
            COUNT(fil.target_issue_key) as affected_count,
            SUM(blocked.story_points) as total_affected_sp,
            GROUP_CONCAT(fil.target_issue_key) as affected_issues
        FROM fact_issue_link fil
        JOIN fact_issue blocker ON fil.source_issue_key = blocker.issue_key
        JOIN fact_issue blocked ON fil.target_issue_key = blocked.issue_key
        WHERE fil.link_type LIKE '%%block%%'
           OR fil.link_type LIKE '%%Block%%'
        GROUP BY fil.source_issue_key, blocker.summary
        HAVING COUNT(fil.target_issue_key) > 1
        ORDER BY affected_count DESC
        """

        try:
            df = pd.read_sql(query, self.engine)
            return df
        except Exception as e:
            print(f"Error getting high impact blockers: {e}")
            return pd.DataFrame()

    def get_blocked_in_sprint(self, sprint_name=None):
        """Get blocked issues in current or specific sprint"""
        if sprint_name:
            query = text("""
            SELECT 
                fil.target_issue_key as blocked_key,
                blocked.summary as blocked_summary,
                blocked.story_points,
                fil.source_issue_key as blocker_key,
                blocker.summary as blocker_summary,
                ds.sprint_name,
                ds.sprint_state
            FROM fact_issue_link fil
            JOIN fact_issue blocked ON fil.target_issue_key = blocked.issue_key
            JOIN fact_issue blocker ON fil.source_issue_key = blocker.issue_key
            JOIN dim_sprint ds ON blocked.sprint_id = ds.sprint_id
            WHERE (fil.link_type LIKE '%block%' OR fil.link_type LIKE '%Block%')
            AND ds.sprint_name = :sprint_name
            """)
            try:
                return pd.read_sql(query, self.engine, params={"sprint_name": sprint_name})
            except Exception as e:
                print(f"Error getting blocked in sprint: {e}")
                return pd.DataFrame()
        else:
            query = """
            SELECT 
                fil.target_issue_key as blocked_key,
                blocked.summary as blocked_summary,
                blocked.story_points,
                fil.source_issue_key as blocker_key,
                blocker.summary as blocker_summary,
                ds.sprint_name,
                ds.sprint_state
            FROM fact_issue_link fil
            JOIN fact_issue blocked ON fil.target_issue_key = blocked.issue_key
            JOIN fact_issue blocker ON fil.source_issue_key = blocker.issue_key
            JOIN dim_sprint ds ON blocked.sprint_id = ds.sprint_id
            WHERE (fil.link_type LIKE '%%block%%' OR fil.link_type LIKE '%%Block%%')
            AND ds.sprint_state = 'active'
            """

        try:
            df = pd.read_sql(query, self.engine)
            return df
        except Exception as e:
            print(f"Error getting blocked in sprint: {e}")
            return pd.DataFrame()

    @staticmethod
    def _compute_risk_score(blockers_df, blocked_sp):
        """Pure function (no DB access) so this formula can be unit-tested
        with synthetic data without a live MySQL connection - see
        test_dependency_risk.py.

        OLD (step function):
            if blockers_count == 0: 100 (LOW)
            elif blockers_count <= 3 and high_impact_count == 0
                 and blocked_sp < 10: 70 (MEDIUM)
            else: 30 (HIGH)
        This produced a cliff: 3 blocking relationships -> 70, but a 4th
        identical relationship (or 1 extra Story Point) could suddenly jump
        the score to 30, a 40-point drop from a single incremental change.

        NEW (additive, priority-weighted, relationship-based):
            penalty_relationships = min(
                sum(BASE_POINTS_PER_RELATIONSHIP * priority_weight(blocker priority)
                    for each blocking RELATIONSHIP row in blockers_df),
                MAX_RELATIONSHIP_PENALTY
            )
            penalty_sp = min(POINTS_PER_BLOCKED_SP * blocked_sp, MAX_SP_PENALTY)
            score = max(100 - penalty_relationships - penalty_sp, 0)

        GRAIN NOTE: blockers_df has one row per blocking relationship
        (blocker->blocked edge), not one row per distinct blocker issue. A
        blocker issue involved in 3 relationships contributes its priority
        weight 3 times. This mirrors how blockers_count itself was already
        counted in the old formula (relationship rows, not distinct
        issues) - see module docstring for the full rationale.

        Both penalty components are monotonic non-decreasing in their
        input and independently capped, so score is monotonic
        non-increasing in both "more/higher-priority blocking
        relationships" and "more blocked SP" - it can never improve by
        making either worse.
        """
        if blockers_df is None or blockers_df.empty:
            weighted_points = 0.0
            unknown_priority_count = 0
            most_severe = None
        else:
            weights = blockers_df['blocker_priority'].apply(priority_weight)
            weighted_points = float((weights * BASE_POINTS_PER_RELATIONSHIP).sum())
            unknown_priority_count = int(
                (~blockers_df['blocker_priority'].apply(is_known_priority)).sum()
            )
            most_severe = most_severe_name(blockers_df['blocker_priority'].tolist())

        penalty_relationships = min(weighted_points, MAX_RELATIONSHIP_PENALTY)
        penalty_sp = min(POINTS_PER_BLOCKED_SP * (blocked_sp or 0), MAX_SP_PENALTY)
        score = max(100 - penalty_relationships - penalty_sp, 0)
        score = round(score, 1)

        if score >= RISK_BAND_LOW_MIN:
            level, color = 'LOW', '🟢'
        elif score >= RISK_BAND_MEDIUM_MIN:
            level, color = 'MEDIUM', '🟡'
        else:
            level, color = 'HIGH', '🔴'

        return {
            "risk_score": score,
            "risk_level": level,
            "risk_color": color,
            "penalty_relationships": round(penalty_relationships, 1),
            "penalty_sp": round(penalty_sp, 1),
            "highest_priority_blocker": most_severe,
            "blockers_with_unknown_priority": unknown_priority_count,
        }

    def generate_report(self):
        """Generate complete dependency risk report"""
        report = {}

        # Get all blocking relationships (one row per blocker->blocked edge)
        blockers = self.get_blockers()
        report['blockers_count'] = len(blockers)

        # Get high-impact blocker ISSUES (distinct blocker keys affecting
        # multiple targets - see get_high_impact_blockers())
        high_impact = self.get_high_impact_blockers()
        report['high_impact'] = high_impact.to_dict('records') if not high_impact.empty else []
        report['high_impact_count'] = len(high_impact)

        # Get blocked in active sprints
        blocked_in_sprint = self.get_blocked_in_sprint()
        report['blocked_in_active_sprint'] = len(blocked_in_sprint)

        # Total Story Points sitting behind a blocker, deduplicated by
        # blocked issue (an issue blocked by 2 things shouldn't double-count
        # its SP). This is the number that actually answers "how much
        # planned work is at risk" - raw relationship count alone does not.
        blocked_sp = 0
        if not blockers.empty:
            blocked_sp = (
                blockers.drop_duplicates(subset=['blocked_key'])['blocked_story_points']
                .fillna(0).sum()
            )
        report['blocked_story_points'] = round(float(blocked_sp), 1)

        # --- Additive, priority-weighted risk score (replaces the old
        # 100/70/30 step function - see _compute_risk_score for the full
        # old-vs-new derivation and the relationship-grain note) ---
        risk = self._compute_risk_score(blockers, blocked_sp)
        report['risk_score'] = risk['risk_score']
        report['risk_level'] = risk['risk_level']
        report['risk_color'] = risk['risk_color']
        report['risk_penalty_relationships'] = risk['penalty_relationships']
        report['risk_penalty_blocked_sp'] = risk['penalty_sp']
        report['highest_priority_blocker'] = risk['highest_priority_blocker']
        report['blockers_with_unknown_priority'] = risk['blockers_with_unknown_priority']

        if report['high_impact']:
            top_blocker = report['high_impact'][0]
            report['critical_blocker'] = f"{top_blocker['blocker_key']}: {top_blocker['blocker_summary']}"
            report['critical_affected'] = top_blocker['affected_count']
        else:
            report['critical_blocker'] = 'None'
            report['critical_affected'] = 0

        return report

def print_dependency_report(report):
    """Print the dependency risk report"""
    print("\n" + "=" * 60)
    print("DEPENDENCY & SPRINT RISK REPORT")
    print("=" * 60)

    print(f"\n🔗 Total Blocking Relationships: {report['blockers_count']}")
    print(f"📊 High-Impact Blocker Issues: {report['high_impact_count']}")
    print(f"📦 Blocked Story Points: {report['blocked_story_points']}")
    print(f"⚠️  Blocked Issues in Active Sprint: {report['blocked_in_active_sprint']}")
    if report.get('highest_priority_blocker'):
        print(f"🔺 Highest-priority blocker: {report['highest_priority_blocker']}")
    if report.get('blockers_with_unknown_priority'):
        print(f"❔ Blocking relationships with unrecognized/missing priority "
              f"(treated as Medium): {report['blockers_with_unknown_priority']}")

    if report['high_impact_count'] > 0:
        print(f"\n🚨 Critical Blocker:")
        print(f"   {report['critical_blocker']}")
        print(f"   Affects: {report['critical_affected']} other issues")
    else:
        print("\n✅ No critical blockers detected")

    print(f"\n🎯 Dependency Risk Score: {report['risk_score']}%")
    print(f"   (100 - {report['risk_penalty_relationships']} blocking-relationship penalty "
          f"- {report['risk_penalty_blocked_sp']} blocked-SP penalty)")
    print(f"   Risk Level: {report['risk_color']} {report['risk_level']}")

    print("\n" + "=" * 60)