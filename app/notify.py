"""
notify.py — optional Slack-webhook / email (SMTP) notifier. Dormant until configured
in settings.yaml (`notify:`) or via env vars. Never raises — best-effort, returns the
channels it reached. Used by scripts/scheduled_pull.py to ping you with the digest.
"""
import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage


def _cfg(cfg: dict) -> dict:
    return (cfg or {}).get("notify", {}) or {}


def available(cfg: dict) -> bool:
    """True when at least one channel (Slack webhook or SMTP+recipient) is configured."""
    n = _cfg(cfg)
    slack = n.get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL")
    smtp = (n.get("smtp_host") or os.getenv("SMTP_HOST")) and (n.get("email_to") or os.getenv("EMAIL_TO"))
    return bool(slack or smtp)


def send(cfg: dict, subject: str, body: str) -> tuple:
    """Send `subject`/`body` to every configured channel. Returns (ok, channels_reached).
    Best-effort — a failing channel is skipped, never raised."""
    n = _cfg(cfg)
    reached = []

    hook = n.get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL")
    if hook:
        try:
            req = urllib.request.Request(
                hook, data=json.dumps({"text": f"*{subject}*\n{body}"}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            reached.append("slack")
        except Exception:
            pass

    host = n.get("smtp_host") or os.getenv("SMTP_HOST")
    to = n.get("email_to") or os.getenv("EMAIL_TO")
    if host and to:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = n.get("email_from") or os.getenv("SMTP_USER") or to
            msg["To"] = to
            msg.set_content(body)
            port = int(n.get("smtp_port") or os.getenv("SMTP_PORT") or 587)
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                user = n.get("smtp_user") or os.getenv("SMTP_USER")
                pwd = n.get("smtp_pass") or os.getenv("SMTP_PASS")
                if user and pwd:
                    s.login(user, pwd)
                s.send_message(msg)
            reached.append("email")
        except Exception:
            pass

    return (bool(reached), reached)
