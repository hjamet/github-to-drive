#!/usr/bin/env python3
"""
GitHub to Google Drive Sync — Monitors all GitHub repos and syncs them
as structured Markdown files into a 'github' folder on Google Drive.
"""

import argparse
import io
import json
import logging
import os
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "github-to-drive"
CONFIG_FILE = CONFIG_DIR / "config.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
STATE_FILE = CONFIG_DIR / "state.json"

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
POLL_INTERVAL_SECONDS = 60  # 1 minute

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".tex", ".md", ".html", ".css",
    ".sh", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sql",
    ".r", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".rb",
    ".php", ".swift", ".kt", ".scala", ".lua", ".pl",
}

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".next",
    ".nuxt", "vendor", "target", ".idea", ".vscode", ".cursor", ".agent",
}

EXTENSION_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "jsx", ".tsx": "tsx", ".tex": "latex", ".md": "markdown",
    ".html": "html", ".css": "css", ".sh": "bash", ".yaml": "yaml",
    ".yml": "yaml", ".json": "json", ".toml": "toml", ".sql": "sql",
    ".r": "r", ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".scala": "scala", ".lua": "lua", ".pl": "perl",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("github-to-drive")


# ---------------------------------------------------------------------------
# Google Drive helpers
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


def run_oauth_setup():
    """Run the interactive OAuth2 flow (called during install).

    Supports headless servers: prints an authorization URL that the user
    opens on any machine with a browser.  After granting access the browser
    redirects to ``http://localhost:1/?code=…`` which will fail to load
    (no server listening) — the user simply copies the full URL from the
    address bar and pastes it here.
    """
    if not CREDENTIALS_FILE.exists():
        raise RuntimeError(f"credentials.json not found at {CREDENTIALS_FILE}")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE), SCOPES,
        redirect_uri="http://localhost:1",
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent",
    )

    print("\n" + "=" * 60)
    print("  Open this URL in a browser (any machine):")
    print("=" * 60)
    print(f"\n  {auth_url}\n")
    print("=" * 60)
    print("  After authorizing, the browser will try to redirect to")
    print("  http://localhost:1/?code=…  — the page will fail to load.")
    print("  That's normal! Copy the FULL URL from the address bar")
    print("  and paste it below.")
    print("=" * 60 + "\n")

    redirect_response = input("Paste the full redirect URL here: ").strip()

    # Extract the authorization code from the redirect URL
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(redirect_response)
    qs = parse_qs(parsed.query)
    if "code" not in qs:
        raise RuntimeError(
            "Could not find 'code' parameter in the URL. "
            f"Got: {redirect_response}"
        )
    code = qs["code"][0]

    flow.fetch_token(code=code)
    creds = flow.credentials

    TOKEN_FILE.write_text(creds.to_json())
    os.chmod(str(TOKEN_FILE), 0o600)
    log.info("OAuth2 token saved to %s", TOKEN_FILE)

    # Create the github folder on Drive
    service = build("drive", "v3", credentials=creds)
    folder_id = get_or_create_folder(service)
    log.info("Drive folder 'github' ready (id: %s)", folder_id)
    return folder_id


def get_or_create_folder(service, folder_name="github"):
    """Get or create the 'github' folder at Drive root."""
    query = (
        f"name='{folder_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and 'root' in parents and trashed=false"
    )
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    files = results.get("files", [])

    if files:
        log.info("Found existing folder '%s' (id: %s)", folder_name, files[0]["id"])
        return files[0]["id"]

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    log.info("Created folder '%s' (id: %s)", folder_name, folder["id"])
    return folder["id"]


def upload_or_update_file(service, folder_id, filename, content):
    """Upload or update a Markdown file in the github folder on Drive."""
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id)")
        .execute()
    )
    existing = results.get("files", [])

    media = MediaInMemoryUpload(
        content.encode("utf-8"), mimetype="text/markdown"
    )

    if existing:
        service.files().update(
            fileId=existing[0]["id"], media_body=media
        ).execute()
        log.info("Updated '%s' on Drive", filename)
    else:
        metadata = {"name": filename, "parents": [folder_id]}
        service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()
        log.info("Created '%s' on Drive", filename)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

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
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def get_github_user(token):
    return _gh_get("/user", token).json()["login"]


def list_repos(token):
    repos, page = [], 1
    while True:
        data = _gh_get("/user/repos", token, params={
            "per_page": 100, "page": page,
            "sort": "pushed", "affiliation": "owner",
        }).json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def get_latest_commit_sha(token, owner, repo, branch):
    commits = _gh_get(
        f"/repos/{owner}/{repo}/commits",
        token, params={"sha": branch, "per_page": 1},
    ).json()
    return commits[0]["sha"] if commits else None


def download_repo_files(token, owner, repo, ref="HEAD"):
    """Download repo tarball and extract code files. Returns {path: content}."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/tarball/{ref}",
        headers=headers, stream=True, timeout=120,
    )
    resp.raise_for_status()

    files = {}
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue
            rel = parts[1]

            # Skip ignored directories
            if any(p in IGNORE_DIRS for p in rel.split("/")):
                continue

            ext = os.path.splitext(rel)[1].lower()
            basename = os.path.basename(rel).lower()

            if ext in CODE_EXTENSIONS or basename in {
                "makefile", "dockerfile", "cmakelists.txt"
            }:
                f = tar.extractfile(member)
                if f:
                    try:
                        files[rel] = f.read().decode("utf-8", errors="replace")
                    except Exception as exc:
                        log.warning("Could not read %s: %s", rel, exc)
    return files


def get_open_issues(token, owner, repo, username):
    """Open issues created by *username*, excluding label 'jules'."""
    issues, page = [], 1
    while True:
        data = _gh_get(
            f"/repos/{owner}/{repo}/issues", token,
            params={"state": "open", "creator": username,
                    "per_page": 100, "page": page},
        ).json()
        if not data:
            break
        for issue in data:
            if "pull_request" in issue:
                continue
            labels = [lb["name"].lower() for lb in issue.get("labels", [])]
            if "jules" in labels:
                continue
            issues.append(issue)
        page += 1
    return issues


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def _build_tree(paths):
    """Return a text tree from a sorted list of file paths."""
    tree = {}
    for p in sorted(paths):
        node = tree
        for part in p.split("/"):
            node = node.setdefault(part, {})

    lines = []

    def _render(node, prefix=""):
        items = sorted(node.items())
        for i, (name, children) in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{name}")
            if children:
                _render(children, prefix + ("    " if last else "│   "))

    _render(tree)
    return "\n".join(lines)


def generate_markdown(repo_name, full_name, sha, files, issues):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [
        f"# {full_name}\n",
        f"> Last synced: {now}",
        f"> Commit: `{sha[:7]}`\n",
        "## Repository Tree\n",
        "```",
        _build_tree(files),
        "```\n",
        "## Files\n",
    ]

    for path in sorted(files):
        ext = os.path.splitext(path)[1].lower()
        lang = EXTENSION_TO_LANG.get(ext, "")
        parts.append(f"### {path}\n")
        parts.append(f"```{lang}")
        parts.append(files[path])
        parts.append("```\n")

    if issues:
        parts.append("## Open Issues\n")
        for iss in issues:
            parts.append(f"### #{iss['number']} — {iss['title']}\n")
            if iss.get("body"):
                parts.append(iss["body"])
            parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync_repo(github_token, drive_service, folder_id, owner, repo_name,
              default_branch="main"):
    """Sync a single repo to Google Drive."""
    username = get_github_user(github_token)
    state = load_state()
    full = f"{owner}/{repo_name}"

    sha = get_latest_commit_sha(github_token, owner, repo_name, default_branch)
    if not sha:
        log.info("Skipping %s (no commits)", full)
        return
    if state.get(full) == sha:
        log.info("Skipping %s (already synced %s)", full, sha[:7])
        return

    log.info("Syncing %s (commit %s)", full, sha[:7])
    files = download_repo_files(github_token, owner, repo_name, ref=sha)
    issues = get_open_issues(github_token, owner, repo_name, username)
    md = generate_markdown(repo_name, full, sha, files, issues)
    upload_or_update_file(drive_service, folder_id, f"{repo_name}.md", md)

    state[full] = sha
    save_state(state)
    log.info("Synced %s (%s)", full, sha[:7])


def sync_all(github_token, drive_service, folder_id):
    """Full sync — check all repos for new commits."""
    repos = list_repos(github_token)
    log.info("Checking %d repos for changes", len(repos))
    for repo in repos:
        sync_repo(
            github_token, drive_service, folder_id,
            repo["owner"]["login"], repo["name"],
            repo.get("default_branch", "main"),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GitHub → Drive sync")
    parser.add_argument(
        "--setup", action="store_true",
        help="Run interactive OAuth2 setup (used by install.sh)",
    )
    args = parser.parse_args()

    if args.setup:
        run_oauth_setup()
        return

    if not CONFIG_FILE.exists():
        log.error("Config not found at %s — run install.sh first.", CONFIG_FILE)
        sys.exit(1)

    config = json.loads(CONFIG_FILE.read_text())
    github_token = config["github_token"]

    creds = get_drive_credentials()
    drive_service = build("drive", "v3", credentials=creds)
    folder_id = get_or_create_folder(drive_service)

    log.info("Starting sync loop (every %ds)", POLL_INTERVAL_SECONDS)
    while True:
        sync_all(github_token, drive_service, folder_id)
        log.info("Next check in %ds…", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
