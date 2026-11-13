"""
Transforms raw Jira JSON into structured pandas DataFrames

PHASE 4 ADDITION: alongside the existing fact_issue.sprint_id (the
picked CURRENT/FINAL sprint per issue, produced by _pick_current_sprint()
and unchanged by this phase), this module now also produces
fact_issue_sprint - a bridge table preserving EVERY sprint an issue has
historically been associated with (one row per unique (issue, sprint)
pair). See _sprint_candidates(), the sprint-extraction block inside
transform_jira_data(), and dim_sprint population, all below. No
additional Jira API request is made anywhere in this module or its
callers to support this - it is derived entirely from the sprint-history
array (customfield_10020) already present in the same raw issue JSON
get_issues() was already fetching for _pick_current_sprint().
"""

import pandas as pd
from datetime import datetime, timezone

def safe_str(value):
    """Convert any value to string, handling None and dicts"""
    if value is None:
        return None
    if isinstance(value, dict):
        # Extract id or name from dict
        return value.get('id') or value.get('name') or str(value)
    return str(value) if value else None

def safe_float(value):
    """Convert to float, handling None and invalid values"""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def _parse_jira_datetime(value):
    """Parse a Jira ISO-8601 timestamp into a comparable, timezone-AWARE
    datetime. Returns a tz-aware datetime.min (UTC) for anything
    missing/unparseable, so it always sorts last.

    ROBUSTNESS FIX: this used to return a NAIVE datetime.min as the
    fallback. Jira's API always returns startDate/endDate as a
    timezone-aware ISO-8601 timestamp WHEN a value is present (Jira
    itself never emits a naive timestamp) - but a sprint that hasn't
    been scheduled yet (e.g. a 'future' sprint) can legitimately have
    startDate: null. When _pick_current_sprint() falls through to
    max(..., key=_parse_jira_datetime) over a candidate set mixing a
    sprint with a real (aware) startDate and one with none (naive
    datetime.min fallback), Python's datetime comparison raises
    `TypeError: can't compare offset-naive and offset-aware datetimes`.
    This is a real, reachable case for genuine Jira payloads (NOT
    something specific to the MBA training data) - see
    test_naive_aware_datetime_comparison_does_not_crash().

    FIX SCOPE: only the fallback value's tzinfo changed (now UTC-aware
    instead of naive). The selection logic in _pick_current_sprint()
    (prefer 'active', else latest startDate) is completely unchanged -
    an aware datetime.min still sorts before every real timestamp, so a
    sprint with no startDate still loses to one that has any real date,
    exactly as before this fix. Sprints with no startDate compared only
    against EACH OTHER (also unlikely but possible) are still tied and
    resolved by max()'s existing first-occurrence tie-break, also
    unchanged."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)

def _sprint_candidates(sprint_data):
    """Normalize Jira's customfield_10020 sprint field into a flat list of
    sprint-reference dicts, regardless of whether Jira returned a list, a
    single dict, or nothing.

    This is the single place both _pick_current_sprint() (which selects
    ONE sprint - the current/final one) and the Phase 4 sprint-history
    extraction (which preserves ALL of them) read the raw sprint
    references from, so both consumers always see the exact same
    candidate set. Entries that aren't dicts are silently dropped here
    (nothing sane to extract from them); entries missing an 'id' are kept
    at this stage and filtered out later by the caller that needs an id,
    so each caller can decide how to report that malformed case.
    """
    if isinstance(sprint_data, list):
        return [s for s in sprint_data if isinstance(s, dict)]
    elif isinstance(sprint_data, dict):
        return [sprint_data]
    return []


def _pick_current_sprint(sprint_data):
    """Pick the sprint an issue is CURRENTLY in, given Jira's sprint field.

    Jira's sprint custom field returns every sprint an issue has ever been
    placed in. Array order is USUALLY (but not reliably) chronological -
    some Jira instances contain duplicate sprint entities (same name,
    different id, near-identical dates, e.g. from a board being re-seeded),
    which breaks any "just take the first/last element" assumption. This
    picks, in order of preference: (1) a sprint whose state is 'active',
    (2) otherwise the sprint with the latest startDate.

    BEHAVIORALLY UNCHANGED in Phase 4 for valid Jira sprint payloads: the
    selection rule itself (prefer active, else latest startDate) and
    therefore fact_issue.sprint_id's meaning are not touched. Two
    internal, non-behavioral things changed alongside it: (1) candidate
    extraction was factored out into _sprint_candidates() so the new
    sprint-history extraction reads the exact same candidate set this
    function does, and (2) _parse_jira_datetime()'s fallback value became
    timezone-aware (see its docstring) to fix a pre-existing crash on a
    specific payload shape (a candidate with no startDate compared
    against one that has a real timestamp) - a case that previously
    raised instead of returning a result, so no prior "byte-for-byte"
    output is regressed by fixing it.
    """
    candidates = _sprint_candidates(sprint_data)

    if not candidates:
        return None

    active = [s for s in candidates if s.get("state") == "active"]
    if active:
        return active[0]

    return max(candidates, key=lambda s: _parse_jira_datetime(s.get("startDate")))

def _version_state(version):
    """Jira's fixVersion objects expose 'archived' and 'released' booleans,
    NOT a 'state' string field (that's a Sprint-object concept). Derive an
    equivalent state label so downstream code has something meaningful."""
    if version.get("archived"):
        return "archived"
    if version.get("released"):
        return "released"
    return "unreleased"

def detect_duplicate_sprint_names(issues):
    """Jira's sprint field can contain duplicate sprint ENTITIES: the same
    sprint NAME under multiple different ids (confirmed present on this
    instance - e.g. two separate "MBA Sprint 1" objects with ids 3 and 74).

    This is intentionally NOT resolved by merging the ids - which sprint
    entity an issue actually belongs to in Jira is a fact this code has no
    authority to override, and two same-named sprints may genuinely
    represent different periods of work. Instead, every distinct sprint id
    is kept as its own row (same treatment as find_potential_duplicates()
    gives Story titles: flagged for human review, never auto-merged).

    This scans every sprint reference across all issues and returns the
    groups of ids that share a name, so the collision can be surfaced
    explicitly rather than silently absorbed into the numbers.
    """
    name_to_ids = {}

    def _register(sprint):
        if not isinstance(sprint, dict):
            return
        sid = sprint.get("id")
        name = sprint.get("name")
        if sid is None or name is None:
            return
        name_to_ids.setdefault(name, set()).add(safe_str(sid))

    for issue in issues:
        sprint_data = issue.get("fields", {}).get("customfield_10020")
        if isinstance(sprint_data, list):
            for s in sprint_data:
                _register(s)
        elif isinstance(sprint_data, dict):
            _register(sprint_data)

    return [
        {"sprint_name": name, "sprint_ids": sorted(ids)}
        for name, ids in name_to_ids.items()
        if len(ids) > 1
    ]

def transform_jira_data(raw_data):
    """
    Takes the raw Jira API response and transforms it into
    separate DataFrames for each entity.
    """
    if not raw_data or "issues" not in raw_data:
        return None
    
    issues = raw_data["issues"]
    
    # --- Extract dimension data ---
    
    # Projects
    projects = []
    projects_set = set()
    
    # Users
    users = {}
    
    # Issue Types
    issue_types = {}
    
    # Statuses
    statuses = {}
    
    # Priorities
    priorities = {}
    
    # Sprints
    sprints = {}
    
    # Versions
    versions = {}
    
    # Issues (fact table)
    fact_issues = []
    
    # Issue Links
    issue_links = []
    issue_links_set = set()  # To avoid duplicates
    
    # Issue <-> Version bridge (an issue can have MULTIPLE fixVersions,
    # so this needs its own table rather than a single column on fact_issue)
    issue_versions = []
    issue_versions_set = set()

    # Issue <-> Sprint HISTORY bridge (Phase 4). An issue can have been
    # historically associated with MULTIPLE sprints over its lifetime
    # (Jira's customfield_10020 sprint-history array), so - exactly like
    # fixVersions above - this needs its own bridge table rather than a
    # single column. This is ADDITIONAL to, and does not replace,
    # fact_issue.sprint_id (the existing single "current/final sprint"
    # column produced by _pick_current_sprint() below, unchanged).
    issue_sprints = []
    issue_sprints_set = set()
    malformed_sprint_refs = 0  # sprint reference dicts with no usable id
    
    for issue in issues:
        key = issue["key"]
        fields = issue["fields"]
        
        # Extract project
        project = fields.get("project", {})
        project_key = project.get("key")
        if project_key and project_key not in projects_set:
            projects.append({
                "project_id": safe_str(project.get("id")),
                "project_key": safe_str(project_key),
                "project_name": safe_str(project.get("name"))
            })
            projects_set.add(project_key)
        
        # Extract users
        assignee = fields.get("assignee")
        if assignee and isinstance(assignee, dict):
            account_id = assignee.get("accountId")
            if account_id and account_id not in users:
                users[account_id] = {
                    "user_account_id": safe_str(account_id),
                    "display_name": safe_str(assignee.get("displayName")),
                    "email": safe_str(assignee.get("emailAddress"))
                }
        
        reporter = fields.get("reporter")
        if reporter and isinstance(reporter, dict):
            account_id = reporter.get("accountId")
            if account_id and account_id not in users:
                users[account_id] = {
                    "user_account_id": safe_str(account_id),
                    "display_name": safe_str(reporter.get("displayName")),
                    "email": safe_str(reporter.get("emailAddress"))
                }
        
        # Extract issue type
        issue_type = fields.get("issuetype", {})
        type_id = safe_str(issue_type.get("id"))
        if type_id and type_id not in issue_types:
            issue_types[type_id] = {
                "issue_type_id": type_id,
                "issue_type_name": safe_str(issue_type.get("name"))
            }
        
        # Extract status
        status = fields.get("status", {})
        status_id = safe_str(status.get("id"))
        if status_id and status_id not in statuses:
            statuses[status_id] = {
                "status_id": status_id,
                "status_name": safe_str(status.get("name"))
            }
        
        # Extract priority
        priority = fields.get("priority")
        priority_id = None
        if priority and isinstance(priority, dict):
            priority_id = safe_str(priority.get("id"))
            if priority_id and priority_id not in priorities:
                priorities[priority_id] = {
                    "priority_id": priority_id,
                    "priority_name": safe_str(priority.get("name"))
                }
        
        # Extract sprints (from customfield_10020)
        #
        # CURRENT/FINAL SPRINT (unchanged): _pick_current_sprint() still
        # selects exactly one sprint per issue, and that remains what
        # fact_issue.sprint_id represents. Nothing about this selection
        # changed in Phase 4.
        #
        # ALL SPRINTS (Phase 4, new): separately, EVERY sprint this issue
        # has ever been associated with is now registered into dim_sprint
        # and into the new issue<->sprint history bridge. This matters
        # because, before this change, `sprints` only ever received the
        # ONE picked-current sprint per issue - a sprint that no issue is
        # CURRENTLY sitting in (e.g. every issue that was ever in it has
        # since moved to a later sprint) would never have been added to
        # dim_sprint at all, which would make it impossible to record
        # historical membership in it. Registering every candidate here
        # does not change dim_sprint's columns or grain - only which rows
        # end up in it.
        sprint_data = fields.get("customfield_10020")
        sprint_candidates = _sprint_candidates(sprint_data)

        picked_sprint = _pick_current_sprint(sprint_data)
        sprint_id = safe_str(picked_sprint.get("id")) if picked_sprint else None

        for cand in sprint_candidates:
            cand_id = safe_str(cand.get("id"))
            if not cand_id:
                # A sprint reference with no usable id can't be linked to
                # dim_sprint or the bridge table - it is dropped, not
                # silently merged into another sprint or guessed at, and
                # the count is surfaced in the function's return-adjacent
                # print below so it isn't invisible.
                malformed_sprint_refs += 1
                continue

            if cand_id not in sprints:
                sprints[cand_id] = {
                    "sprint_id": cand_id,
                    "sprint_name": safe_str(cand.get("name")),
                    "sprint_state": safe_str(cand.get("state")),
                    "start_date": cand.get("startDate"),
                    "end_date": cand.get("endDate")
                }

            bridge_key = f"{key}->{cand_id}"
            if bridge_key not in issue_sprints_set:
                issue_sprints.append({
                    "issue_key": safe_str(key),
                    "sprint_id": cand_id
                })
                issue_sprints_set.add(bridge_key)
        
        # Extract versions/fixVersions
        fix_versions = fields.get("fixVersions", [])
        for version in fix_versions:
            if isinstance(version, dict):
                version_id = safe_str(version.get("id"))
                if version_id and version_id not in versions:
                    versions[version_id] = {
                        "version_id": version_id,
                        "version_name": safe_str(version.get("name")),
                        "version_state": _version_state(version),
                        "release_date": version.get("releaseDate")
                    }
                # An issue can belong to MULTIPLE versions, so this is a
                # many-to-many bridge, not a column on fact_issue.
                if version_id:
                    bridge_key = f"{key}->{version_id}"
                    if bridge_key not in issue_versions_set:
                        issue_versions.append({
                            "issue_key": safe_str(key),
                            "version_id": version_id
                        })
                        issue_versions_set.add(bridge_key)
        
        # Extract epic relationship
        epic_key = None
        issue_type_name = safe_str(issue_type.get("name"))
        if issue_type_name == "Sub-task":
            parent = fields.get("parent", {})
            if isinstance(parent, dict):
                epic_key = safe_str(parent.get("key"))
        # For stories, we could extract epic link from customfield_10014
        # but we'll skip for now and add later
        
        # --- Build fact_issue record with safe conversions ---
        assignee_id = safe_str(assignee.get("accountId")) if assignee and isinstance(assignee, dict) else None
        reporter_id = safe_str(reporter.get("accountId")) if reporter and isinstance(reporter, dict) else None
        
        fact_issue = {
            "issue_id": safe_str(issue.get("id")),
            "issue_key": safe_str(key),
            "project_id": safe_str(project.get("id")),
            "issue_type_id": safe_str(type_id),
            "status_id": safe_str(status_id),
            "priority_id": priority_id,
            "assignee_account_id": assignee_id,
            "reporter_account_id": reporter_id,
            "sprint_id": sprint_id,
            "story_points": safe_float(fields.get("customfield_10016")),
            "summary": safe_str(fields.get("summary")),
            "description": safe_str(fields.get("description")),
            "created_date": fields.get("created"),
            "updated_date": fields.get("updated"),
            "resolved_date": fields.get("resolutiondate"),
            "epic_key": epic_key
        }
        fact_issues.append(fact_issue)
        
        # --- Extract issue links ---
        issue_links_data = fields.get("issuelinks", [])
        for link in issue_links_data:
            if not isinstance(link, dict):
                continue
                
            link_type_obj = link.get("type", {})
            link_type = safe_str(link_type_obj.get("name", "Unknown"))
            
            # Check if it's outward (outwardIssue) or inward (inwardIssue)
            outward = link.get("outwardIssue")
            inward = link.get("inwardIssue")
            
            if outward and isinstance(outward, dict):
                link_key = f"{key}->{safe_str(outward.get('key'))}"
                if link_key not in issue_links_set:
                    issue_links.append({
                        "source_issue_key": safe_str(key),
                        "target_issue_key": safe_str(outward.get("key")),
                        "link_type": link_type
                    })
                    issue_links_set.add(link_key)
                    
            if inward and isinstance(inward, dict):
                link_key = f"{safe_str(inward.get('key'))}->{key}"
                if link_key not in issue_links_set:
                    issue_links.append({
                        "source_issue_key": safe_str(inward.get("key")),
                        "target_issue_key": safe_str(key),
                        "link_type": link_type
                    })
                    issue_links_set.add(link_key)
    
    # Convert dictionaries to DataFrames
    df_projects = pd.DataFrame(projects) if projects else pd.DataFrame()
    df_users = pd.DataFrame(list(users.values())) if users else pd.DataFrame()
    df_issue_types = pd.DataFrame(list(issue_types.values())) if issue_types else pd.DataFrame()
    df_statuses = pd.DataFrame(list(statuses.values())) if statuses else pd.DataFrame()
    df_priorities = pd.DataFrame(list(priorities.values())) if priorities else pd.DataFrame()
    df_sprints = pd.DataFrame(list(sprints.values())) if sprints else pd.DataFrame()
    df_versions = pd.DataFrame(list(versions.values())) if versions else pd.DataFrame()
    df_fact_issues = pd.DataFrame(fact_issues) if fact_issues else pd.DataFrame()
    df_issue_links = pd.DataFrame(issue_links) if issue_links else pd.DataFrame()
    df_issue_versions = pd.DataFrame(issue_versions) if issue_versions else pd.DataFrame()
    df_issue_sprints = (
        pd.DataFrame(issue_sprints) if issue_sprints
        else pd.DataFrame(columns=["issue_key", "sprint_id"])
    )

    if malformed_sprint_refs:
        print(f"WARNING: {malformed_sprint_refs} sprint reference(s) in "
              f"customfield_10020 had no usable 'id' and were dropped from "
              f"dim_sprint / fact_issue_sprint (they were NOT silently "
              f"merged into another sprint).")

    return {
        "dim_project": df_projects,
        "dim_user": df_users,
        "dim_issue_type": df_issue_types,
        "dim_status": df_statuses,
        "dim_priority": df_priorities,
        "dim_sprint": df_sprints,
        "dim_version": df_versions,
        "fact_issue": df_fact_issues,
        "fact_issue_link": df_issue_links,
        "fact_issue_version": df_issue_versions,
        "fact_issue_sprint": df_issue_sprints
    }