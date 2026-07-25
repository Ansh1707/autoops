import base64
import email.mime.multipart
import email.mime.text
import errno
import os
from pathlib import Path

from langchain.tools import tool


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
MAX_EMAIL_CHARS = int(os.getenv("MAX_EMAIL_CHARS", "5000"))


def _credential_path() -> str:
    configured = os.getenv("GMAIL_CREDENTIALS_PATH")
    candidates = [
        configured,
        "credentials.json",
        "Credentials.json",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("Missing Gmail OAuth credentials. Expected credentials.json or Credentials.json.")


def _token_path() -> str:
    return os.getenv("GMAIL_TOKEN_PATH", "token.json")


def _gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_path = _token_path()
    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(_credential_path(), GMAIL_SCOPES)
            auth_host = os.getenv("GMAIL_AUTH_HOST", "localhost")
            bind_addr = os.getenv("GMAIL_AUTH_BIND_ADDR", "0.0.0.0")
            raw_ports = os.getenv("GMAIL_AUTH_PORTS", os.getenv("GMAIL_AUTH_PORT", "8080"))
            auth_ports = [int(port.strip()) for port in raw_ports.split(",") if port.strip()]
            open_browser = os.getenv("GMAIL_OPEN_BROWSER", "false").lower() == "true"
            last_error = None
            for auth_port in auth_ports:
                try:
                    creds = flow.run_local_server(
                        host=auth_host,
                        bind_addr=bind_addr,
                        port=auth_port,
                        open_browser=open_browser,
                        timeout_seconds=300,
                    )
                    break
                except OSError as exc:
                    last_error = exc
                    if exc.errno != errno.EADDRINUSE:
                        raise
            if creds is None:
                raise RuntimeError(f"All Gmail OAuth callback ports are busy: {auth_ports}. Last error: {last_error}")
        Path(token_path).write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _decode_body(payload: dict) -> str:
    body = payload.get("body", {})
    data = body.get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []) or []:
        mime_type = part.get("mimeType", "")
        if mime_type == "text/plain":
            nested = part.get("body", {}).get("data")
            if nested:
                return base64.urlsafe_b64decode(nested).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    return ""


def _raw_message(to: str, subject: str, body: str) -> str:
    message = email.mime.multipart.MIMEMultipart()
    message["to"] = to
    message["subject"] = subject
    message.attach(email.mime.text.MIMEText(body, "plain"))
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


@tool
def fetch_recent_emails(max_results: int = 10, query: str = "in:inbox") -> str:
    """Fetch recent Gmail messages with id, sender, subject, date, and preview."""
    try:
        service = _gmail_service()
        results = service.users().messages().list(
            userId="me",
            maxResults=min(max_results, 25),
            q=query or "in:inbox",
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return "No emails found."

        rows = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = _headers(detail.get("payload", {}))
            rows.append(
                "\n".join([
                    f"ID: {msg['id']}",
                    f"From: {headers.get('from', '?')}",
                    f"Subject: {headers.get('subject', '?')}",
                    f"Date: {headers.get('date', '?')}",
                    f"Preview: {detail.get('snippet', '')[:250]}",
                ])
            )
        return "\n---\n".join(rows)
    except Exception as exc:
        return (
            f"fetch_recent_emails failed: {exc}. "
            "If running in Docker for the first time, open the OAuth URL printed in worker logs, "
            "complete Google consent, then retry."
        )


@tool
def read_full_email(message_id: str) -> str:
    """Read the full plain-text body of a Gmail message by message id."""
    try:
        service = _gmail_service()
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = _headers(msg.get("payload", {}))
        body = _decode_body(msg.get("payload", {})).strip()
        output = "\n".join([
            f"From: {headers.get('from', '?')}",
            f"Subject: {headers.get('subject', '?')}",
            f"Date: {headers.get('date', '?')}",
            "",
            body,
        ])
        return output[:MAX_EMAIL_CHARS]
    except Exception as exc:
        return f"read_full_email failed: {exc}"


@tool
def create_gmail_draft(to: str, subject: str, body: str) -> str:
    """Create a Gmail draft. Use this before sending so the user can review."""
    try:
        service = _gmail_service()
        raw = _raw_message(to, subject, body)
        draft = service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()
        return f"Draft created for {to}. Draft ID: {draft.get('id')}"
    except Exception as exc:
        return f"create_gmail_draft failed: {exc}"


@tool
def send_email(to: str, subject: str, body: str, confirmation: str = "") -> str:
    """Send an email only when confirmation is exactly SEND."""
    try:
        if confirmation != "SEND":
            return "Email not sent. Use create_gmail_draft first, or pass confirmation='SEND' after user approval."
        service = _gmail_service()
        service.users().messages().send(
            userId="me",
            body={"raw": _raw_message(to, subject, body)},
        ).execute()
        return f"Email sent to {to}."
    except Exception as exc:
        return f"send_email failed: {exc}"
