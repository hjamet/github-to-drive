import json
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

CONFIG_DIR = Path.home() / ".config" / "github-to-drive"
TOKEN_FILE = CONFIG_DIR / "token.json"

def get_creds():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), ["https://www.googleapis.com/auth/drive.file"])
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds

def test():
    creds = get_creds()
    service = build("drive", "v3", credentials=creds)
    
    query = "name='github' and mimeType='application/vnd.google-apps.folder'"
    results = service.files().list(q=query).execute()
    folder_id = results.get("files", [])[0]["id"]

    filename = "Test_Document"
    content = "# Hello World\n\nThis is a test document."
    
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/markdown")
    metadata = {
        "name": filename, 
        "parents": [folder_id],
        "mimeType": "application/vnd.google-apps.document"
    }
    
    file = service.files().create(body=metadata, media_body=media, fields="id").execute()
    print("Created:", file["id"])
    
    # Try updating
    content2 = "# Hello World\n\nThis is an updated test document."
    media2 = MediaInMemoryUpload(content2.encode("utf-8"), mimetype="text/markdown")
    service.files().update(fileId=file["id"], media_body=media2).execute()
    print("Updated")

test()
