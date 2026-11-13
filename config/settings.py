"""
This file loads our Jira credentials from the .env file
so we never type secrets directly into our code.
"""

import os
from dotenv import load_dotenv

# This line reads the .env file and makes its values available
load_dotenv()

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

# Small safety check: warn us early if something is missing,
# instead of failing later with a confusing error.
if not all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
    print("WARNING: One or more Jira credentials are missing from your .env file.")

# MySQL Configuration
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "agile_intelligence")