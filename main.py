"""
Main ETL Pipeline - Runs the complete Jira to MySQL process
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "extraction"))
sys.path.append(os.path.join(os.path.dirname(__file__), "transformation"))
sys.path.append(os.path.join(os.path.dirname(__file__), "loading"))
sys.path.append(os.path.join(os.path.dirname(__file__), "analytics"))

import pandas as pd

from jira_client import get_issues
from transform_jira_data import transform_jira_data, detect_duplicate_sprint_names
from mysql_loader import MySQLDatabase
from backlog_quality import analyze_backlog_quality, print_backlog_quality_report
from sprint_planning import SprintPlanningAnalyzer, print_sprint_report
from dependency_risk import DependencyRiskAnalyzer, print_dependency_report
from release_readiness import ReleaseReadinessAnalyzer, print_release_report
from analytics_snapshot import build_analytics_snapshot

def run_pipeline():
    print("=" * 60)
    print("AGILE INTELLIGENCE PLATFORM - ETL PIPELINE")
    print("=" * 60)
    
    # Step 1: Extract
    print("\n[1/4] Extracting data from Jira...")
    raw_data = get_issues("project = MBA ORDER BY created ASC")
    if not raw_data:
        print("❌ Failed to extract data from Jira")
        return
    
    print(f"✅ Extracted {len(raw_data['issues'])} issues from Jira")
    
    # Data quality: flag (never silently merge) sprints that share a name
    # but are distinct Jira entities - each remains its own sprint in the
    # data model; this is surfaced for human review, same treatment
    # find_potential_duplicates() gives Story titles.
    sprint_collisions = detect_duplicate_sprint_names(raw_data["issues"])
    if sprint_collisions:
        print("\n⚠️  DATA QUALITY ALERT: sprint name collisions detected")
        for collision in sprint_collisions:
            ids = ", ".join(collision["sprint_ids"])
            print(f"   '{collision['sprint_name']}' is used by {len(collision['sprint_ids'])} "
                  f"distinct sprint ids: {ids}")
        print("   These are kept as SEPARATE sprints in the data model (not merged).")
        print("   Historical velocity and sprint reports will show each one individually.")
    
    # Step 2: Transform
    print("\n[2/4] Transforming data...")
    transformed_data = transform_jira_data(raw_data)
    if not transformed_data:
        print("❌ Failed to transform data")
        return
    
    print("✅ Data transformed successfully")
    for table_name, df in transformed_data.items():
        if not df.empty:
            print(f"   - {table_name}: {len(df)} rows")
    
    # Step 3: Load
    print("\n[3/4] Loading data to MySQL...")
    db = MySQLDatabase()
    
    dim_tables = ['dim_project', 'dim_user', 'dim_issue_type', 
                  'dim_status', 'dim_priority', 'dim_sprint', 'dim_version']
    # fact_issue_version must load AFTER fact_issue and dim_version (FKs).
    # fact_issue_sprint (Phase 4) must load AFTER fact_issue and dim_sprint -
    # both already satisfied by this point, since all dim_tables load
    # before any fact_tables, and fact_issue loads first in this list.
    fact_tables = ['fact_issue', 'fact_issue_link', 'fact_issue_version', 'fact_issue_sprint']
    
    db.load_all(transformed_data, dim_tables, fact_tables)
    
    print("✅ Data loaded to MySQL successfully")
    
    # Step 4: Run Analytics
    print("\n[4/4] Running Analytics...")
    
    # Module 1: Backlog Quality
    print("\n--- Module 1: Backlog Quality ---")
    backlog_result = analyze_backlog_quality(raw_data)
    print_backlog_quality_report(backlog_result)
    
    # Module 2: Sprint Planning
    print("\n--- Module 2: Sprint Planning ---")
    sprint_analyzer = SprintPlanningAnalyzer()
    sprint_report = sprint_analyzer.generate_report()
    print_sprint_report(sprint_report)
    
    # Module 3: Dependency Risk
    print("\n--- Module 3: Dependency Risk ---")
    dependency_analyzer = DependencyRiskAnalyzer()
    dependency_report = dependency_analyzer.generate_report()
    print_dependency_report(dependency_report)
    
    # Module 4: Release Readiness
    print("\n--- Module 4: Release Readiness ---")
    release_analyzer = ReleaseReadinessAnalyzer()
    release_report = release_analyzer.generate_report()
    print_release_report(release_report)

    # Step 5: Persist a single-row analytics snapshot for Power BI.
    # This does NOT recompute any score - it only flattens the four
    # report dicts above (already fully computed) into one row. See
    # analytics/analytics_snapshot.py for the exact key mapping.
    # if_exists='replace' is intentional: Power BI should always see
    # exactly one current snapshot row, not an accumulating history.
    print("\n[5/5] Persisting analytics snapshot for Power BI...")
    try:
        snapshot_dict = build_analytics_snapshot(
            backlog_result, sprint_report, dependency_report, release_report
        )
        snapshot_df = pd.DataFrame([snapshot_dict])
        db.load_dataframe(snapshot_df, "analytics_snapshot", if_exists="replace")
        print("✅ analytics_snapshot written (1 row):")
        for k, v in snapshot_dict.items():
            print(f"   {k}: {v}")
    except ValueError as e:
        print(f"⚠️  Skipped analytics_snapshot write: {e}")

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_pipeline()