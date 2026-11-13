"""
This file is our 'Jira client'.
Its only job is to talk to Jira and bring back raw data.
It does NOT calculate scores or do any analysis - that comes later.
"""

import requests
from requests.auth import HTTPBasicAuth
import sys
import os
import json
from datetime import datetime

# This lets Python find our config/settings.py file
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "config"))
from settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN

# NOTE: this file intentionally does NOT import anything from analytics/.
# Extraction should not depend on analysis - that direction of coupling
# previously existed here and contradicted this module's own purpose.
# If you want a quick end-to-end demo, run main.py instead.

REQUEST_TIMEOUT_SECONDS = 30


def test_connection():
    """
    The simplest possible check: can we successfully talk to Jira at all?
    This calls Jira's 'myself' endpoint, which just returns info
    about whoever owns the API token - it's the standard way
    to test that authentication works.
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/myself"

    response = requests.get(
        url,
        auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS
    )

    print("Status code:", response.status_code)

    if response.status_code == 200:
        data = response.json()
        print("Success! Connected as:", data.get("displayName"))
        print("Email:", data.get("emailAddress"))
    else:
        print("Something went wrong. Jira said:")
        print(response.text)


def list_all_fields():
    """
    Asks Jira for the human-readable name of every field ID,
    including custom fields like Story Points.
    This solves the 'customfield_XXXXX' mystery.
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/field"

    response = requests.get(
        url,
        auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS
    )

    if response.status_code == 200:
        fields = response.json()
        print(f"\nFound {len(fields)} fields. Custom fields:")
        for f in fields:
            if f["id"].startswith("customfield_"):
                print(f"  {f['id']}  ->  {f['name']}")
    else:
        print("Could not fetch field list:", response.text)



def summarize_issues(data):
    """
    Takes the raw Jira response and prints one readable line per issue.
    This turns messy nested JSON into a quick human-readable overview -
    the first small step toward the Backlog Quality Analyzer,
    which needs to know things like 'which issues are missing Story Points'.
    """
    if not data or "issues" not in data:
        print("No issues to summarize.")
        return

    print(f"\n{'KEY':<8} {'TYPE':<10} {'STATUS':<15} {'SP':<6} {'ASSIGNEE'}")
    print("-" * 60)

    for issue in data["issues"]:
        fields = issue["fields"]

        key = issue["key"]
        issue_type = fields["issuetype"]["name"]
        status = fields["status"]["name"]

        story_points = fields.get("customfield_10016")
        story_points = story_points if story_points is not None else "--"

        assignee = fields.get("assignee")
        assignee_name = assignee["displayName"] if assignee else "Unassigned"

        print(f"{key:<8} {issue_type:<10} {status:<15} {str(story_points):<6} {assignee_name}")


def get_issues(jql, page_size=100):
    """
    Fetches ALL issues matching a JQL query, not just the first page.

    Jira's API returns results in pages (e.g. 100 issues at a time) rather
    than everything at once. If we only fetched one page, we would silently
    lose data once the backlog grows past that page size - the script would
    keep running and reporting numbers, just quietly wrong ones.

    This function loops: fetch a page, check if Jira says there's more
    (via 'nextPageToken' / 'isLast'), and if so, fetch the next page too -
    repeating until Jira confirms we have everything. All pages are then
    combined into a single result, so every other function in this file
    (summarize_issues, analyze_backlog_quality, etc.) doesn't need to know
    or care that pagination happened at all.
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"

    all_issues = []
    next_page_token = None
    page_number = 1

    while True:
        params = {
            "jql": jql,
            "maxResults": page_size,
            "fields": "*all"
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        response = requests.get(
            url,
            params=params,
            auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        if response.status_code != 200:
            print("Something went wrong. Jira said:")
            print(response.text)
            return None

        data = response.json()
        page_issues = data.get("issues", [])
        all_issues.extend(page_issues)

        print(f"  Page {page_number}: retrieved {len(page_issues)} issues "
              f"(total so far: {len(all_issues)})")

        # The new Jira search API tells us we're done via isLast=True,
        # or by simply not providing a nextPageToken at all.
        is_last = data.get("isLast", True)
        next_page_token = data.get("nextPageToken")

        if is_last or not next_page_token:
            break

        page_number += 1

    print(f"Status: done. Retrieved {len(all_issues)} issues total across {page_number} page(s).")

    return {"issues": all_issues}


def save_issues_to_file(data, folder="data"):
    """
    Saves the raw Jira data to a JSON file so it persists after the
    script finishes running - instead of only printing to the screen
    and losing it.

    This is the bridge between 'talking to Jira live' and 'loading into
    a database' (Stage 6). The file is timestamped so each run creates
    a new snapshot rather than silently overwriting previous ones -
    useful later for comparing backlog quality over time.
    """
    if not data:
        print("No data to save.")
        return None

    # Build the path to the data/ folder, relative to this file's location,
    # the same way we already do for config/ and analytics/
    folder_path = os.path.join(os.path.dirname(__file__), "..", folder)
    os.makedirs(folder_path, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"jira_issues_{timestamp}.json"
    filepath = os.path.join(folder_path, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(data.get('issues', []))} issues to: {filepath}")
    return filepath


# This part only runs when you run this file directly
# (not when it's imported elsewhere later)
if __name__ == "__main__":
    print("Step 1: Testing connection to Jira...")
    test_connection()

    print("\nStep 1b: Looking up what the custom fields are actually called...")
    list_all_fields()

    print("\nStep 2: Fetching issues from project MBA...")
    result = get_issues("project = MBA ORDER BY created ASC")

    if result:
        print("\nStep 2b: Saving raw data to file...")
        save_issues_to_file(result)

        print("\nFirst issue raw data (for inspection):")
        import json
        print(json.dumps(result["issues"][0], indent=2))

        print("\nStep 3: Summary of all issues...")
        summarize_issues(result)

        print("\nFor analytics on this data, run main.py, which chains "
              "extraction into the analytics layer for you.")