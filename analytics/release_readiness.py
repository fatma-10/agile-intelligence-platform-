"""
Release Readiness Analyzer (Module 4)

Evaluates if a release is ready to go.

Bug-penalty scoring (see generate_report()):
  OLD: every open Bug cost a flat 5 points, capped at 40 total, regardless
       of severity.
  NEW: every open Bug costs 5 points * priority_weight(that bug's
       priority), capped at 40 total - so a release blocked by one
       Highest-priority bug scores worse than one blocked by one
       Low-priority bug, instead of identically. See generate_report()
       and priority_utils.py for the full derivation and the priority-name
       mapping.

CONSISTENCY CHECK: the aggregate `open_bugs` count (from get_release_data())
and the per-bug detail rows used for scoring (from get_open_bugs_detail())
come from two separate queries against two different join paths. They are
expected to agree, but nothing enforces that at the SQL level, so
generate_report() explicitly checks len(bug_detail) against
release['open_bugs'] and emits a warning if they differ, rather than
silently scoring against a population that doesn't match what's reported
as the bug count.

THIS SESSION (Phase 3, two independent additions - neither touches
readiness_score):

1. RELEASE DATE: dim_version.release_date was already extracted by the ETL
   but was never selected by get_release_data(). It is now included in the
   SELECT, and generate_report() derives a null-safe days_until_release in
   Python (_compute_days_until_release()). Informational only.

2. RELEASE-SCOPED DEPENDENCY CONTEXT: a new query,
   get_release_blocked_story_points(), answers "of THIS release's
   remaining (not-done) work, how much Story-Pointed work is currently
   sitting behind a blocking relationship?"

   VERIFIED SEMANTICS (Phase-3 verification pass): fact_issue_link uses
   source_issue_key = the BLOCKING issue and target_issue_key = the
   BLOCKED issue - confirmed directly from dependency_risk.py::get_blockers()
   (`fil.source_issue_key as blocker_key`, `fil.target_issue_key as
   blocked_key`). This query joins on `fil.target_issue_key = fi.issue_key`,
   i.e. the release's issue is required to be the BLOCKED (target) side -
   which is what "how much of this release's work is blocked" requires.
   This matches Module 3's own convention and needed no correction.

   GRAIN (corrected docstring - see get_release_blocked_story_points() for
   the full note): the SELECT list only contains issue_key and
   story_points - no blocker-identifying column - so `SELECT DISTINCT`
   already collapses multiple blocking relationships against the same
   issue into a single row AT THE SQL LEVEL. This differs from Module 3's
   own blockers_df (which retains blocker_key/link_type and therefore
   genuinely has one row per relationship, requiring the Python-side
   drop_duplicates() to do the collapsing). The Python-side dedup in
   _sum_blocked_story_points() is still applied here for defensive
   consistency with the project-wide code path and because relying on
   SQL DISTINCT semantics implicitly assumes story_points never differs
   for the same issue_key across rows (true here, since it's a property
   of the issue) - but it is not what performs the deduplication in this
   query; the SQL already does. This is a documentation correction only;
   the analytical result (SP per blocked issue, counted once) is
   unchanged.

   Release-scoped number is clearly labeled "release-scoped" everywhere
   it's shown so it can't be confused with Module 3's project-wide figure,
   and generate_report() runs a sanity check confirming
   release_scoped <= project_wide. NOT folded into readiness_score.
"""

from datetime import datetime
import pandas as pd
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))

from sqlalchemy import create_engine, text
from settings import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
from priority_utils import priority_weight, is_known_priority
from dependency_risk import DependencyRiskAnalyzer

BASE_POINTS_PER_OPEN_BUG = 5
MAX_BUG_PENALTY = 40


class ReleaseReadinessAnalyzer:
    def __init__(self):
        connection_string = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}/{MYSQL_DATABASE}"
        self.engine = create_engine(connection_string)

    def get_release_data(self, version_name=None):
        base_query = """
        SELECT 
            dv.version_id,
            dv.version_name,
            dv.version_state,
            dv.release_date,
            COUNT(fi.issue_id) as total_issues,
            COALESCE(SUM(fi.story_points), 0) as total_story_points,
            COALESCE(SUM(CASE WHEN fi.status_id IN (SELECT status_id FROM dim_status WHERE status_name = 'Terminé') 
                THEN fi.story_points ELSE 0 END), 0) as completed_story_points,
            COALESCE(SUM(CASE WHEN fi.issue_type_id IN (SELECT issue_type_id FROM dim_issue_type WHERE issue_type_name = 'Bug') 
                THEN 1 ELSE 0 END), 0) as total_bugs,
            COALESCE(SUM(CASE WHEN fi.issue_type_id IN (SELECT issue_type_id FROM dim_issue_type WHERE issue_type_name = 'Bug') 
                AND fi.status_id NOT IN (SELECT status_id FROM dim_status WHERE status_name = 'Terminé')
                THEN 1 ELSE 0 END), 0) as open_bugs,
            COALESCE(SUM(CASE WHEN fi.status_id NOT IN (SELECT status_id FROM dim_status WHERE status_name = 'Terminé')
                THEN fi.story_points ELSE 0 END), 0) as remaining_story_points
        FROM dim_version dv
        LEFT JOIN fact_issue_version fiv ON fiv.version_id = dv.version_id
        LEFT JOIN fact_issue fi ON fi.issue_key = fiv.issue_key
        {where_clause}
        GROUP BY dv.version_id, dv.version_name, dv.version_state, dv.release_date
        {order_limit}
        """

        try:
            if version_name:
                query = text(base_query.format(
                    where_clause="WHERE dv.version_name = :version_name",
                    order_limit="LIMIT 1"
                ))
                df = pd.read_sql(query, self.engine, params={"version_name": version_name})
            else:
                query = text(base_query.format(
                    where_clause="WHERE dv.version_name IS NOT NULL",
                    order_limit="ORDER BY dv.version_id DESC LIMIT 1"
                ))
                df = pd.read_sql(query, self.engine)

            if df.empty:
                return None

            result = df.iloc[0].to_dict()
            if result.get("total_issues", 0) == 0:
                print(f"WARNING: version '{result.get('version_name')}' has "
                      f"0 issues mapped via fact_issue_version - readiness "
                      f"numbers for it are not meaningful.")
            return result
        except Exception as e:
            print(f"Error getting release data: {e}")
            return None

    def get_open_bugs_detail(self, version_id):
        query = text("""
        SELECT
            fi.issue_key,
            dp.priority_name
        FROM fact_issue_version fiv
        JOIN fact_issue fi ON fi.issue_key = fiv.issue_key
        LEFT JOIN dim_priority dp ON fi.priority_id = dp.priority_id
        WHERE fiv.version_id = :version_id
          AND fi.issue_type_id IN (SELECT issue_type_id FROM dim_issue_type WHERE issue_type_name = 'Bug')
          AND fi.status_id NOT IN (SELECT status_id FROM dim_status WHERE status_name = 'Terminé')
        """)
        try:
            return pd.read_sql(query, self.engine, params={"version_id": version_id})
        except Exception as e:
            print(f"Error getting open bug detail: {e}")
            return pd.DataFrame(columns=["issue_key", "priority_name"])

    def get_release_blocked_story_points(self, version_id):
        """Release-scoped dependency context. See module docstring
        "GRAIN (corrected docstring)" note above for the verified SQL
        grain: because the SELECT list here is only (issue_key,
        story_points), SELECT DISTINCT already produces one row per
        distinct blocked issue in this release, not one row per blocking
        relationship - unlike Module 3's blockers_df, which retains
        blocker-identifying columns and genuinely has relationship grain.

        JOIN PATH: fact_issue_version scoped to :version_id is the
        starting scope (guarantees only this release's issues can ever
        appear). Joined to fact_issue for status/story points, then to
        fact_issue_link on fil.target_issue_key = fi.issue_key - i.e.
        this release's issue must be the BLOCKED (target) side of the
        relationship. Verified against dependency_risk.py::get_blockers(),
        which defines target_issue_key as blocked_key.
        """
        query = text("""
        SELECT DISTINCT
            fi.issue_key,
            fi.story_points
        FROM fact_issue_version fiv
        JOIN fact_issue fi ON fi.issue_key = fiv.issue_key
        JOIN fact_issue_link fil ON fil.target_issue_key = fi.issue_key
        WHERE fiv.version_id = :version_id
          AND fi.status_id NOT IN (SELECT status_id FROM dim_status WHERE status_name = 'Terminé')
          AND (fil.link_type LIKE '%block%' OR fil.link_type LIKE '%Block%')
        """)
        try:
            return pd.read_sql(query, self.engine, params={"version_id": version_id})
        except Exception as e:
            print(f"Error getting release-scoped blocked story points: {e}")
            return pd.DataFrame(columns=["issue_key", "story_points"])

    def get_project_wide_blocked_story_points(self):
        """Recompute Module 3's project-wide blocked_story_points number,
        for the sanity check in generate_report() only. Reuses
        DependencyRiskAnalyzer.get_blockers() directly (verified: returns
        blocker_key/blocked_key/blocked_story_points columns) rather than
        its full generate_report()."""
        dep_analyzer = DependencyRiskAnalyzer()
        blockers_df = dep_analyzer.get_blockers()
        return self._sum_blocked_story_points(blockers_df, key_col='blocked_key', sp_col='blocked_story_points')

    @staticmethod
    def _sum_blocked_story_points(blocked_df, key_col, sp_col):
        """Pure function (no DB access): dedupe blocked_df by key_col and
        sum sp_col, treating missing SP as 0. VERIFIED to mirror
        dependency_risk.py::generate_report()'s own dedup line exactly:
            blockers.drop_duplicates(subset=['blocked_key'])['blocked_story_points'].fillna(0).sum()
        Shared by both the release-scoped and project-wide-recompute code
        paths so the same dedup rule is applied identically in both
        places."""
        if blocked_df is None or blocked_df.empty:
            return 0.0
        return round(
            float(blocked_df.drop_duplicates(subset=[key_col])[sp_col].fillna(0).sum()),
            1
        )

    @staticmethod
    def _check_release_scope_sanity(release_scoped_sp, project_wide_sp):
        consistent = (project_wide_sp is None) or (release_scoped_sp <= project_wide_sp)
        return {
            "consistent": consistent,
            "release_scoped_blocked_sp": release_scoped_sp,
            "project_wide_blocked_sp": project_wide_sp,
        }

    @staticmethod
    def _compute_days_until_release(release_date_raw):
        if release_date_raw is None or release_date_raw == "":
            return None, None
        try:
            if pd.isna(release_date_raw):
                return None, None
        except (TypeError, ValueError):
            pass

        rd = None
        if isinstance(release_date_raw, str):
            try:
                rd = datetime.fromisoformat(release_date_raw.replace("Z", "+00:00")).date()
            except ValueError:
                return None, None
        elif hasattr(release_date_raw, "date") and callable(getattr(release_date_raw, "date")):
            rd = release_date_raw.date()
        elif hasattr(release_date_raw, "isoformat"):
            rd = release_date_raw
        else:
            return None, None

        days = (rd - datetime.now().date()).days
        return rd.isoformat(), days

    @staticmethod
    def _compute_bug_penalty(open_bugs_df):
        if open_bugs_df is None or open_bugs_df.empty:
            return {
                "penalty": 0.0,
                "priority_breakdown": [],
                "unknown_priority_count": 0,
            }

        names = open_bugs_df["priority_name"].fillna("No Priority")
        weights = names.apply(lambda n: priority_weight(None if n == "No Priority" else n))
        weighted_points = float((weights * BASE_POINTS_PER_OPEN_BUG).sum())
        penalty = min(weighted_points, MAX_BUG_PENALTY)

        counts = names.value_counts()
        breakdown = sorted(
            [{"priority": name, "count": int(count),
              "weight": priority_weight(None if name == "No Priority" else name)}
             for name, count in counts.items()],
            key=lambda d: -d["weight"],
        )

        unknown_count = int(sum(
            1 for n in names
            if n != "No Priority" and not is_known_priority(n)
        ))

        return {
            "penalty": round(penalty, 1),
            "priority_breakdown": breakdown,
            "unknown_priority_count": unknown_count,
        }

    @staticmethod
    def _check_open_bug_count_consistency(reported_open_bugs, bug_detail_df):
        scored_count = 0 if bug_detail_df is None else len(bug_detail_df)
        reported_count = int(reported_open_bugs or 0)
        consistent = (scored_count == reported_count)
        return {
            "consistent": consistent,
            "reported_open_bugs": reported_count,
            "scored_open_bugs": scored_count,
        }

    def generate_report(self):
        """Generate release readiness report"""
        report = {}

        release = self.get_release_data()
        if not release:
            report['error'] = 'No release/version data found'
            return report

        report['release'] = release

        # --- Release date (surfaced only - NOT part of readiness_score) ---
        release_date_display, days_until_release = self._compute_days_until_release(
            release.get('release_date')
        )
        report['release_date'] = release_date_display
        report['days_until_release'] = days_until_release

        # --- Release-scoped dependency context (surfaced only - NOT part
        # of readiness_score) ---
        release_blocked_df = self.get_release_blocked_story_points(release['version_id'])
        release_scoped_blocked_sp = self._sum_blocked_story_points(
            release_blocked_df, key_col='issue_key', sp_col='story_points'
        )
        project_wide_blocked_sp = self.get_project_wide_blocked_story_points()

        scope_sanity = self._check_release_scope_sanity(
            release_scoped_blocked_sp, project_wide_blocked_sp
        )
        report['release_scoped_blocked_story_points'] = release_scoped_blocked_sp
        report['project_wide_blocked_story_points'] = project_wide_blocked_sp
        report['release_scope_sanity_check'] = scope_sanity
        if not scope_sanity['consistent']:
            print(f"WARNING: release-scoped blocked SP "
                  f"({release_scoped_blocked_sp}) exceeds project-wide blocked SP "
                  f"({project_wide_blocked_sp}) for version "
                  f"'{release.get('version_name')}' - a release is a subset of "
                  f"the project, so this should never happen. Investigate "
                  f"get_release_blocked_story_points() for a join-path bug "
                  f"before trusting this number.")

        total = release['total_story_points']
        completed = release['completed_story_points']

        if total > 0:
            completion_rate = (completed / total) * 100
        else:
            completion_rate = 0

        report['completion_rate'] = round(completion_rate, 1)

        score = 100

        remaining = release['remaining_story_points']
        if remaining > 0:
            score -= min(remaining * 2, 30)

        open_bugs = release['open_bugs']
        bug_detail = self.get_open_bugs_detail(release['version_id'])

        consistency = self._check_open_bug_count_consistency(open_bugs, bug_detail)
        report['open_bug_count_consistency'] = consistency
        if not consistency["consistent"]:
            print(f"WARNING: open bug count mismatch for version "
                  f"'{release.get('version_name')}' - aggregate query reports "
                  f"{consistency['reported_open_bugs']} open bugs, but "
                  f"{consistency['scored_open_bugs']} rows were returned for "
                  f"priority-weighted scoring.")

        bug_penalty_info = self._compute_bug_penalty(bug_detail)
        score -= bug_penalty_info["penalty"]

        report['open_bug_priority_breakdown'] = bug_penalty_info["priority_breakdown"]
        report['open_bugs_unknown_priority'] = bug_penalty_info["unknown_priority_count"]
        report['open_bug_penalty'] = bug_penalty_info["penalty"]

        score = max(score, 0)
        report['readiness_score'] = round(score)

        if score >= 80:
            report['risk_level'] = 'LOW'
            report['risk_color'] = '🟢'
            report['status'] = 'Ready to Release'
        elif score >= 60:
            report['risk_level'] = 'MEDIUM'
            report['risk_color'] = '🟡'
            report['status'] = 'Proceed with Caution'
        else:
            report['risk_level'] = 'HIGH'
            report['risk_color'] = '🔴'
            report['status'] = 'Not Ready - Fix Critical Issues'

        blockers = []
        if open_bugs > 0:
            blockers.append(f"{open_bugs} unresolved bugs")
        if remaining > 0:
            blockers.append(f"{remaining} SP remaining")

        report['blockers'] = blockers

        return report

def print_release_report(report):
    """Print the release readiness report"""
    if not report or report.get('error'):
        print(f"❌ {report.get('error', 'Error generating report')}")
        return

    r = report['release']

    print("\n" + "=" * 60)
    print("RELEASE READINESS REPORT")
    print("=" * 60)

    print(f"\n📦 Release: {r['version_name']} ({r['version_state']})")

    release_date = report.get('release_date')
    days_until = report.get('days_until_release')
    if release_date is None:
        print(f"   Release date: No target date set")
        print(f"   Days until release: N/A")
    else:
        print(f"   Release date: {release_date}")
        if days_until is not None and days_until < 0:
            print(f"   Days until release: {abs(days_until)} days overdue")
        else:
            print(f"   Days until release: {days_until} days")

    print(f"\n📊 Completion: {report['completion_rate']}%")
    print(f"   Total Story Points: {r['total_story_points']}")
    print(f"   Completed: {r['completed_story_points']}")
    print(f"   Remaining: {r['remaining_story_points']}")

    print(f"\n🐛 Bugs:")
    print(f"   Total Bugs: {r['total_bugs']}")
    print(f"   Open Bugs: {r['open_bugs']}")
    consistency = report.get('open_bug_count_consistency')
    if consistency and not consistency['consistent']:
        print(f"   ⚠️  INCONSISTENT: {consistency['scored_open_bugs']} rows were used "
              f"for scoring vs {consistency['reported_open_bugs']} reported above")
    for tier in report.get('open_bug_priority_breakdown', []):
        print(f"     - {tier['priority']}-priority open bugs: {tier['count']}")
    if report.get('open_bugs_unknown_priority'):
        print(f"     ({report['open_bugs_unknown_priority']} of the above had an "
              f"unrecognized priority name and were treated as Medium severity)")
    print(f"   Open-bug penalty applied: -{report.get('open_bug_penalty', 0)} pts")

    print(f"\n🔗 Dependency Context (release-scoped, informational only - "
          f"not part of Readiness Score, distinct from Module 3's "
          f"project-wide Dependency Risk numbers):")
    print(f"   Remaining SP blocked (this release): "
          f"{report.get('release_scoped_blocked_story_points', 0)}")
    scope_sanity = report.get('release_scope_sanity_check')
    if scope_sanity and not scope_sanity['consistent']:
        print(f"   ⚠️  INCONSISTENT: release-scoped ({scope_sanity['release_scoped_blocked_sp']}) "
              f"exceeds project-wide ({scope_sanity['project_wide_blocked_sp']}) - see warning above")

    print(f"\n🎯 Readiness Score: {report['readiness_score']}%")
    print(f"   Status: {report['risk_color']} {report['status']}")

    if report['blockers']:
        print(f"\n🚫 Blockers:")
        for blocker in report['blockers']:
            print(f"   - {blocker}")

    print("\n" + "=" * 60)