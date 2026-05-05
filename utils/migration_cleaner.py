#!/usr/bin/env python3
"""
Migration & Cleaner Utility — Cleans up the root of the 'github' Drive folder
by moving files into organization-based subfolders and converting raw .md
files into native Google Docs.
"""

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

import requests

socket.setdefaulttimeout(300)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "github-to-drive"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
STATE_FILE = CONFIG_DIR / "state.json"

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("migration-cleaner")


# ---------------------------------------------------------------------------
# Helpers (adapted from sync.py)
# ---------------------------------------------------------------------------

def get_drive_credentials():
    """Load or refresh Google Drive OAuth2 credentials."""
    if not TOKEN_FILE.exists():
        raise RuntimeError(
            f"Token file not found at {TOKEN_FILE}. Run install.sh first."
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
        return creds

    raise RuntimeError(
        "Credentials expired and no refresh token available. "
        "Run install.sh again."
    )


def _gh_get(endpoint, token, params=None):
    """Authenticated GET to the GitHub API. Raises on failure."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.get(
        f"https://api.github.com{endpoint}",
        headers=headers,
        params=params,
        timeout=60,
    )
    resp.raise_for_status()
    return resp


_USER_CACHE = {}

def get_github_user(token):
    if token not in _USER_CACHE:
        _USER_CACHE[token] = _gh_get("/user", token).json()["login"]
    return _USER_CACHE[token]


def get_or_create_subfolder(service, parent_id, folder_name):
    """Get or create a subfolder within a parent folder."""
    query = (
        f"name='{folder_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed=false"
    )
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute(num_retries=5)
    )
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    folder = service.files().create(body=metadata, fields="id").execute(num_retries=5)
    log.info("Created organization folder '%s' (id: %s)", folder_name, folder["id"])
    return folder["id"]


def migrate_md_file(service, file_id, file_name, target_folder_id, dry_run=False):
    """
    1. Download raw .md content.
    2. Create a Google Doc in the target folder.
    3. Trash the original file.
    """
    doc_title = file_name[:-3] if file_name.endswith(".md") else file_name

    if dry_run:
        log.info("[DRY-RUN] Would convert '%s' (.md) to Google Doc in folder %s and trash original",
                 file_name, target_folder_id)
        return

    # 1. Download
    content = service.files().get_media(fileId=file_id).execute(num_retries=5).decode("utf-8")

    if len(content) > 1_000_000:
        log.warning("File '%s' exceeds Google Docs 1M characters limit (%d chars). Moving raw .md instead.", file_name, len(content))
        move_google_doc(service, file_id, file_name, target_folder_id, dry_run)
        return

    # 2. Create Google Doc
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/markdown")
    metadata = {
        "name": doc_title,
        "parents": [target_folder_id],
        "mimeType": "application/vnd.google-apps.document"
    }
    new_file = service.files().create(body=metadata, media_body=media, fields="id").execute(num_retries=5)
    log.info("Converted '%s' to Google Doc (id: %s)", file_name, new_file["id"])

    # 3. Trash original
    service.files().update(fileId=file_id, body={"trashed": True}).execute(num_retries=5)
    log.info("Trashed original .md file '%s'", file_name)


def move_google_doc(service, file_id, file_name, target_folder_id, dry_run=False):
    """Move an existing file (Google Doc or raw file) to the target folder."""
    if dry_run:
        log.info("[DRY-RUN] Would move file '%s' to folder %s", file_name, target_folder_id)
        return

    # Retrieve current parents to remove them
    file = service.files().get(fileId=file_id, fields="parents").execute(num_retries=5)
    previous_parents = ",".join(file.get("parents", []))

    # Move
    service.files().update(
        fileId=file_id,
        addParents=target_folder_id,
        removeParents=previous_parents,
        fields="id, parents"
    ).execute(num_retries=5)
    log.info("Moved file '%s' to organization folder", file_name)


def get_repo_owner(repo_name, github_token, state):
    """
    Resolve the owner (user or organization) of a repository.
    1. Search in state.json (keys are 'owner/repo').
    2. Fallback: Search GitHub API.
    """
    # 1. Search in state.json
    for full_name in state.keys():
        if full_name.endswith(f"/{repo_name}"):
            owner = full_name.split("/")[0]
            log.debug("Found owner '%s' for repo '%s' in state.json", owner, repo_name)
            return owner

    # 2. Fallback: GitHub API search
    log.info("Repo '%s' not found in state.json, searching GitHub API...", repo_name)
    try:
        # We try to find the repo among the authenticated user's repos first
        # since it's the most likely case.
        username = get_github_user(github_token)

        # Search for repositories with this name owned by the user
        # Note: We use the search API for flexibility and include the username for precision
        params = {"q": f"{repo_name} user:{username} in:name"}
        search_results = _gh_get("/search/repositories", github_token, params=params).json()

        for item in search_results.get("items", []):
            if item["name"].lower() == repo_name.lower():
                owner = item["owner"]["login"]
                log.info("Found owner '%s' for repo '%s' via GitHub API", owner, repo_name)
                return owner

    except Exception as e:
        log.error("Error resolving owner for '%s': %s", repo_name, e)

    return None


def get_or_create_root_folder(service, folder_name="github"):
    """Get or create the 'github' folder at Drive root (copied from sync.py)."""
    query = (
        f"name='{folder_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and 'root' in parents and trashed=false"
    )
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute(num_retries=5)
    )
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=metadata, fields="id").execute(num_retries=5)
    return folder["id"]


def main():
    parser = argparse.ArgumentParser(description="Migration & Cleaner Utility")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate actions without making any changes to Drive",
    )
    args = parser.parse_args()

    if not CONFIG_FILE.exists():
        log.error("Config not found at %s. Run install.sh first.", CONFIG_FILE)
        sys.exit(1)

    config = json.loads(CONFIG_FILE.read_text())
    github_token = config["github_token"]

    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {}

    creds = get_drive_credentials()
    service = build("drive", "v3", credentials=creds)

    root_id = get_or_create_root_folder(service)
    log.info("Starting migration cleaner in 'github' folder (id: %s, dry-run=%s)", root_id, args.dry_run)

    # List all files at the root of 'github' with pagination
    query = f"'{root_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'"
    files = []
    page_token = None

    while True:
        results = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute(num_retries=5)
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    if not files:
        log.info("No files found at the root of 'github' folder.")
        return

    log.info("Found %d files to process", len(files))

    for f in files:
        file_id = f["id"]
        file_name = f["name"]
        mime_type = f["mimeType"]

        # Determine repo name (strip .md if present)
        repo_name = file_name[:-3] if file_name.endswith(".md") else file_name

        # Resolve owner
        owner = get_repo_owner(repo_name, github_token, state)
        if not owner:
            log.warning("Could not resolve owner for repo '%s'. Skipping file '%s'.", repo_name, file_name)
            continue

        # Get/create org folder
        org_folder_id = get_or_create_subfolder(service, root_id, owner)

        # Perform action
        try:
            if mime_type == "application/vnd.google-apps.document":
                move_google_doc(service, file_id, file_name, org_folder_id, args.dry_run)
            elif file_name.endswith(".md"):
                migrate_md_file(service, file_id, file_name, org_folder_id, args.dry_run)
            else:
                log.info("Skipping file '%s' (unsupported mimeType/extension: %s)", file_name, mime_type)
        except Exception as e:
            log.error("Failed to migrate '%s': %s", file_name, e)

    log.info("Migration cleaner finished.")

if __name__ == "__main__":
    main()
