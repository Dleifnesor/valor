"""Notifications: in-app list plus email (SMTP), Discord, Slack and Microsoft Teams webhooks."""

from __future__ import annotations

import json
import smtplib
import ssl
import threading
import time
import urllib.request
from email.message import EmailMessage

from . import db
from .security import Box

SECRET_FIELDS = {"email": ("password",), "discord": ("url",), "slack": ("url",), "teams": ("url",)}
PROBLEM_LEVELS = ("warning", "error")


def _send_webhook(url: str, payload: dict) -> None:
    if not url.startswith("https://"):
        raise ValueError("webhook URLs must use https://")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "VALOR"})
    with urllib.request.urlopen(req, timeout=10) as resp:    # noqa: S310 - admin-configured https URL
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def _send_email(c: dict, title: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[VALOR] {title}"
    msg["From"] = c["from"]
    msg["To"] = ", ".join(c["to"]) if isinstance(c["to"], list) else c["to"]
    msg.set_content(body or title)
    ctx = ssl.create_default_context()
    port = int(c.get("port") or 587)
    if c.get("security", "starttls") == "tls":
        with smtplib.SMTP_SSL(c["host"], port, timeout=15, context=ctx) as s:
            if c.get("username"):
                s.login(c["username"], c.get("password", ""))
            s.send_message(msg)
    else:
        with smtplib.SMTP(c["host"], port, timeout=15) as s:
            s.starttls(context=ctx)
            if c.get("username"):
                s.login(c["username"], c.get("password", ""))
            s.send_message(msg)


def send(channel: dict, level: str, title: str, body: str, link: str = "") -> None:
    """channel: decrypted channel settings. Raises on failure."""
    text = f"{title}\n{body}".strip() + (f"\n{link}" if link else "")
    kind, config = channel["type"], channel["config"]
    if kind == "email":
        _send_email(config, title, text)
    elif kind == "discord":                 # allowed_mentions: a range name can't ping @everyone
        _send_webhook(config["url"], {"content": text[:1900], "allowed_mentions": {"parse": []}})
    elif kind == "slack":
        _send_webhook(config["url"], {"text": text[:3000]})
    elif kind == "teams":   # Teams "Workflows" webhook: an Adaptive Card message
        card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                         {"type": "TextBlock", "text": (body or "")[:2000], "wrap": True}]}
        _send_webhook(config["url"], {"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})
    else:
        raise ValueError(f"unknown channel type {kind}")


def channels_plain(stored: list, box: Box) -> list[dict]:
    """The stored channels with their secrets decrypted (a deep copy: the stored settings stay sealed)."""
    out = []
    for stored_channel in stored or []:
        channel = json.loads(json.dumps(stored_channel))
        for field in SECRET_FIELDS.get(channel["type"], ()):
            if channel["config"].get(field):
                channel["config"][field] = box.open_text(channel["config"][field],
                                                         f"notify:{channel['id']}:{field}")
        out.append(channel)
    return out


def _fan_out(db_path: str, box: Box, level: str, event: str, title: str, body: str, link: str) -> list[str]:
    conn = db.connect(db_path)
    try:
        errors = []
        for channel in channels_plain(db.get_setting(conn, "notification_channels", []), box):
            if not channel.get("enabled", True):
                continue
            if channel.get("events", "problems") == "problems" and level not in PROBLEM_LEVELS:
                continue
            try:
                send(channel, level, title, body, link)
            except Exception as e:
                errors.append(f"{channel.get('name', channel['type'])}: {e.__class__.__name__}: {e}")
        if errors:
            db.audit(conn, "notify.deliver", "error", target=event, detail="; ".join(errors))
        return errors
    finally:
        conn.close()


def emit(conn, box: Box | None, level: str, event: str, title: str, body: str = "", link: str = "",
         wait: bool = False) -> list[str]:
    """Record an in-app notification and send it to the channels that want it.

    Delivery runs in a background thread unless wait=True (the worker waits; web requests never do)."""
    conn.execute("INSERT INTO notifications(ts, level, event, title, body, link) VALUES (?, ?, ?, ?, ?, ?)",
                 (time.time(), level, event, title, body[:4000], link))
    if box is None:
        return []
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    if wait:
        return _fan_out(db_path, box, level, event, title, body, link)
    threading.Thread(target=_fan_out, args=(db_path, box, level, event, title, body, link), daemon=True).start()
    return []
