#!/usr/bin/env python3
"""
Migration & Cleaner Utility — Cleans up Google Drive:
1. Trashes all Google Docs in the 'github' folder hierarchy (replaced by raw .md)
2. Trashes any loose files at the root of the 'github' folder
3. Trashes obsolete organization folders (e.g. renamed orgs)
4. Clears state.json to force a full re-sync
"""

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

socket.setdefaulttimeout(300)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "github-to-drive"
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
# Helpers
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


def get_root_folder(service, folder_name="github"):
    """Get the 'github' folder at Drive root."""
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
    if not files:
        raise RuntimeError(f"Folder '{folder_name}' not found on Drive root.")
    return files[0]["id"]


def list_all_files(service, parent_id, mime_filter=None):
    """List all files (non-folder) in a parent, optionally filtered by mimeType."""
    query = f"'{parent_id}' in parents and trashed=false"
    if mime_filter:
        query += f" and mimeType='{mime_filter}'"
    else:
        query += " and mimeType!='application/vnd.google-apps.folder'"

    items = []
    page_token = None
    while True:
        results = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute(num_retries=5)
        items.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return items


def list_subfolders(service, parent_id):
    """List all subfolders in a parent folder."""
    query = (
        f"'{parent_id}' in parents and trashed=false "
        f"and mimeType='application/vnd.google-apps.folder'"
    )
    items = []
    page_token = None
    while True:
        results = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageToken=page_token
        ).execute(num_retries=5)
        items.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return items


def trash_file(service, file_id, file_name, dry_run=False):
    """Trash a file on Drive."""
    if dry_run:
        log.info("[DRY-RUN] Would trash '%s' (id: %s)", file_name, file_id)
        return
    service.files().update(fileId=file_id, body={"trashed": True}).execute(num_retries=5)
    log.info("Trashed '%s' (id: %s)", file_name, file_id)


def main():
    parser = argparse.ArgumentParser(description="Migration & Cleaner Utility")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate actions without making any changes to Drive",
    )
    parser.add_argument(
        "--trash-folders", nargs="*", default=[],
        help="Names of organization folders to trash entirely (e.g. UNIL-Henri)",
    )
    args = parser.parse_args()

    creds = get_drive_credentials()
    service = build("drive", "v3", credentials=creds)

    root_id = get_root_folder(service)
    log.info("Starting migration cleaner in 'github' folder (id: %s, dry-run=%s)",
             root_id, args.dry_run)

    # --- Step 1: Trash obsolete org folders ---
    if args.trash_folders:
        log.info("=== Step 1: Trash obsolete organization folders ===")
        subfolders = list_subfolders(service, root_id)
        for folder in subfolders:
            if folder["name"] in args.trash_folders:
                log.info("Trashing obsolete folder '%s' and all its contents", folder["name"])
                trash_file(service, folder["id"], folder["name"], args.dry_run)

    # --- Step 2: Trash all Google Docs everywhere ---
    log.info("=== Step 2: Trash all Google Docs (replaced by raw .md) ===")
    subfolders = list_subfolders(service, root_id)

    total_trashed = 0
    # Check root level
    root_docs = list_all_files(
        service, root_id,
        mime_filter="application/vnd.google-apps.document"
    )
    for doc in root_docs:
        trash_file(service, doc["id"], f"(root)/{doc['name']}", args.dry_run)
        total_trashed += 1

    # Check each org subfolder
    for folder in subfolders:
        docs = list_all_files(
            service, folder["id"],
            mime_filter="application/vnd.google-apps.document"
        )
        for doc in docs:
            trash_file(service, doc["id"], f"{folder['name']}/{doc['name']}", args.dry_run)
            total_trashed += 1

    log.info("Trashed %d Google Docs total", total_trashed)

    # --- Step 3: Trash loose files at root ---
    log.info("=== Step 3: Trash loose files at root of 'github' ===")
    root_files = list_all_files(service, root_id)
    for f in root_files:
        trash_file(service, f["id"], f"(root)/{f['name']}", args.dry_run)

    if not root_files:
        log.info("No loose files at root.")

    # --- Step 4: Clear state.json to force re-sync ---
    log.info("=== Step 4: Clear state.json ===")
    if args.dry_run:
        log.info("[DRY-RUN] Would clear state.json")
    else:
        if STATE_FILE.exists():
            STATE_FILE.write_text("{}")
            log.info("Cleared state.json — next sync will re-sync all repos")

    log.info("Migration cleaner finished. Restart the service:")
    log.info("  systemctl --user restart github-to-drive")


if __name__ == "__main__":
    main()
