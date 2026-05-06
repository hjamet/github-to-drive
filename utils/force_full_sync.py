#!/usr/bin/env python3
"""
Force Full Sync Utility — Bypasses the event-driven check and forces
a synchronization of all repositories the user has access to.
"""

import logging
import sys
from pathlib import Path

# Add parent directory to path to import sync
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync import (
    CONFIG_FILE, _gh_get, build, get_drive_credentials, get_github_user,
    get_or_create_folder, json, sync_repo
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("force-full-sync")

def main():
    if not CONFIG_FILE.exists():
        log.error("Config not found at %s", CONFIG_FILE)
        sys.exit(1)

    config = json.loads(CONFIG_FILE.read_text())
    github_token = config["github_token"]

    creds = get_drive_credentials()
    drive_service = build("drive", "v3", credentials=creds)
    folder_id = get_or_create_folder(drive_service, "github")

    username = get_github_user(github_token)
    log.info("Fetching ALL repositories for user %s...", username)

    repos = []
    page = 1
    while True:
        try:
            data = _gh_get(
                "/user/repos", github_token, 
                params={"per_page": 100, "page": page, "affiliation": "owner,collaborator,organization_member"}
            ).json()
            if not data:
                break
            repos.extend(data)
            page += 1
        except Exception as e:
            log.error("Failed to fetch repos page %d: %s", page, e)
            break

    log.info("Found %d repositories total. Starting full sync...", len(repos))

    success = 0
    failed = 0

    for idx, repo in enumerate(repos, 1):
        owner = repo["owner"]["login"]
        name = repo["name"]
        default_branch = repo.get("default_branch", "main")
        
        log.info("[%d/%d] Processing %s/%s", idx, len(repos), owner, name)
        try:
            sync_repo(github_token, drive_service, folder_id, owner, name, default_branch)
            success += 1
        except Exception as e:
            log.error("Failed to sync %s/%s: %s", owner, name, e)
            failed += 1

    log.info("Full sync complete! Success: %d, Failed: %d", success, failed)

if __name__ == "__main__":
    main()
