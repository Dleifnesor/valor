# *rings a tiny bell* OwO valor/web/notify.py sends notifications! ^w^
# Every notification is saved for the in-app list (the bell in the UI), and also sent to the channels an admin set
# up: email (SMTP), Discord, Slack or Microsoft Teams webhooks. Channel secrets (passwords, webhook URLs) are
# stored encrypted and only opened right before sending *wags tail*
"""Notifications: in-app list plus email (SMTP), Discord, Slack and Microsoft Teams webhooks."""

# Lazy type hints UwU
from __future__ import annotations

# json builds webhook bodies; smtplib/ssl send email securely; threading sends in the background; time stamps
# the notification; urllib.request makes the webhook HTTP calls; EmailMessage builds an email >w<
import json
import smtplib
import ssl
import threading
import time
import urllib.request
from email.message import EmailMessage

# db = the web database helpers; Box = the encryption box for channel secrets OwO
from . import db
from .security import Box

# SECRET_FIELDS: which settings of each channel type are secret (stored sealed): the email password, and the whole
# webhook URL for chat services (anyone with the URL can post to your channel!) ^w^
# PROBLEM_LEVELS: levels that count as "problems", for channels set to only hear about problems UwU
SECRET_FIELDS = {"email": ("password",), "discord": ("url",), "slack": ("url",), "teams": ("url",)}
PROBLEM_LEVELS = ("warning", "error")


# _send_webhook: POST a JSON payload to a webhook URL >w<
def _send_webhook(url: str, payload: dict) -> None:
    # Only https:// URLs, so the message (and the URL's secret) never travel unencrypted *guards with tail*
    if not url.startswith("https://"):
        raise ValueError("webhook URLs must use https://")
    # Build the request: the JSON body, POST, and headers saying "this is JSON, from VALOR" OwO
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "VALOR"})
    # Send it with a 10 s timeout; any answer that isn't a 2xx success counts as a failure ^w^
    with urllib.request.urlopen(req, timeout=10) as resp:    # noqa: S310 - admin-configured https URL
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


# _send_email: send one email. `c` = the email channel's settings (host, port, from, to, username, password,
# security mode) UwU
def _send_email(c: dict, title: str, body: str) -> None:
    # Build the message: "[VALOR] title" as the subject, sender, recipients (a list is joined with commas) and the
    # body (the title when there's no body) >w<
    msg = EmailMessage()
    msg["Subject"] = f"[VALOR] {title}"
    msg["From"] = c["from"]
    msg["To"] = ", ".join(c["to"]) if isinstance(c["to"], list) else c["to"]
    msg.set_content(body or title)
    # A default TLS context = checks the mail server's certificate properly; port 587 (submission) by default OwO
    ctx = ssl.create_default_context()
    port = int(c.get("port") or 587)
    # security = "tls": connect with TLS from the very start (usually port 465) ^w^
    if c.get("security", "starttls") == "tls":
        with smtplib.SMTP_SSL(c["host"], port, timeout=15, context=ctx) as s:
            # Log in only if a username is set (some internal relays need no login), then send UwU
            if c.get("username"):
                s.login(c["username"], c.get("password", ""))
            s.send_message(msg)
    else:
        # Otherwise (the default "starttls"): connect in plain text, then upgrade to TLS with STARTTLS before logging in >w<
        with smtplib.SMTP(c["host"], port, timeout=15) as s:
            s.starttls(context=ctx)
            if c.get("username"):
                s.login(c["username"], c.get("password", ""))
            s.send_message(msg)


# send: deliver one notification to ONE channel (with its secrets already decrypted). Raises on failure, so the
# caller can report which channel failed OwO
def send(channel: dict, level: str, title: str, body: str, link: str = "") -> None:
    """channel: decrypted channel settings. Raises on failure."""
    # The plain text version: title, body and link on separate lines ^w^
    text = f"{title}\n{body}".strip() + (f"\n{link}" if link else "")
    kind, config = channel["type"], channel["config"]
    # Email: the plain text as the body UwU
    if kind == "email":
        _send_email(config, title, text)
    # Discord: max 2000 chars per message (1900 to be safe). allowed_mentions with an empty parse list means nothing
    # in the text can ping @everyone or a role, even if a range is named something sneaky *giggles* >w<
    elif kind == "discord":                 # allowed_mentions: a range name can't ping @everyone
        _send_webhook(config["url"], {"content": text[:1900], "allowed_mentions": {"parse": []}})
    # Slack: a simple "text" message (cut to 3000 chars) OwO
    elif kind == "slack":
        _send_webhook(config["url"], {"text": text[:3000]})
    # Teams: the new "Workflows" webhooks want an Adaptive Card: a bold title block and a wrapping text block ^w^
    elif kind == "teams":   # Teams "Workflows" webhook: an Adaptive Card message
        card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                         {"type": "TextBlock", "text": (body or "")[:2000], "wrap": True}]}
        # The card goes into a "message" with one attachment of the Adaptive Card content type UwU
        _send_webhook(config["url"], {"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})
    # Any other type is a bug or a corrupted setting: say so loudly >w<
    else:
        raise ValueError(f"unknown channel type {kind}")


# channels_plain: the saved channels with their secrets decrypted. It works on a deep copy (JSON round trip), so
# the stored settings are never changed and stay sealed OwO
def channels_plain(stored: list, box: Box) -> list[dict]:
    """The stored channels with their secrets decrypted (a deep copy: the stored settings stay sealed)."""
    out = []
    for stored_channel in stored or []:
        channel = json.loads(json.dumps(stored_channel))
        # Decrypt each secret field that has a value. The context "notify:<channel id>:<field>" must match what it was
        # sealed with, so a secret can't be swapped between channels ^w^
        for field in SECRET_FIELDS.get(channel["type"], ()):
            if channel["config"].get(field):
                channel["config"][field] = box.open_text(channel["config"][field],
                                                         f"notify:{channel['id']}:{field}")
        out.append(channel)
    return out


# _fan_out: send one notification to every channel that wants it, and audit any failures. It opens its OWN
# database connection because it may run in a background thread (SQLite connections must stay in their thread) UwU
def _fan_out(db_path: str, box: Box, level: str, event: str, title: str, body: str, link: str) -> list[str]:
    conn = db.connect(db_path)
    try:
        errors = []
        # For each channel (decrypted)... >w<
        for channel in channels_plain(db.get_setting(conn, "notification_channels", []), box):
            # ...skip disabled ones... OwO
            if not channel.get("enabled", True):
                continue
            # ...and skip "problems only" channels (the default) when this isn't a warning or error ^w^
            if channel.get("events", "problems") == "problems" and level not in PROBLEM_LEVELS:
                continue
            # A failing channel must not stop the others: collect its error ("name: ErrorType: message") and carry on UwU
            try:
                send(channel, level, title, body, link)
            except Exception as e:
                errors.append(f"{channel.get('name', channel['type'])}: {e.__class__.__name__}: {e}")
        # Failures go to the audit log so an admin can see why a channel is quiet *perks ears*
        if errors:
            db.audit(conn, "notify.deliver", "error", target=event, detail="; ".join(errors))
        return errors
    # Always close the connection, even after an error >w<
    finally:
        conn.close()


# emit: the main entry point. Save the notification for the in-app list, then send it to channels ^w^
#   conn = an open database connection; box = the encryption box (None = no channels possible)
#   level = info/success/warning/error; event = what happened (e.g. "job.apply"); title/body/link = the message
#   wait = send right now and return the errors (the worker does this); otherwise send in a background thread so
#          a web request never waits on a slow mail server OwO
def emit(conn, box: Box | None, level: str, event: str, title: str, body: str = "", link: str = "",
         wait: bool = False) -> list[str]:
    """Record an in-app notification and send it to the channels that want it.

    Delivery runs in a background thread unless wait=True (the worker waits; web requests never do)."""
    # Insert into the in-app list; the body is capped at 4000 chars UwU
    conn.execute("INSERT INTO notifications(ts, level, event, title, body, link) VALUES (?, ?, ?, ?, ?, ?)",
                 (time.time(), level, event, title, body[:4000], link))
    # No encryption key = channel secrets can't be opened, so in-app only >w<
    if box is None:
        return []
    # Find the database FILE behind this connection (PRAGMA database_list gives it as the third column), so the
    # background sender can open its own connection ^w^
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    if wait:
        return _fan_out(db_path, box, level, event, title, body, link)
    # daemon=True: the thread won't keep the process alive at shutdown OwO
    threading.Thread(target=_fan_out, args=(db_path, box, level, event, title, body, link), daemon=True).start()
    return []
