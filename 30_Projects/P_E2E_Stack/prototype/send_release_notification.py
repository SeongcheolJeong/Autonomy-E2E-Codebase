#!/usr/bin/env python3
"""Send release notification payload to webhook endpoint with policy controls."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from ci_input_parsing import parse_non_negative_float, parse_non_negative_int, parse_positive_float
from ci_phases import SUMMARY_PHASE_SEND_NOTIFICATION
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_subprocess import compact_failure_detail
from ci_sync_utils import load_json_object


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send release notification payload to webhook")
    parser.add_argument("--payload-json", required=True, help="Path to notification payload JSON")
    parser.add_argument("--webhook-url", default="", help="Webhook URL (empty => skip)")
    parser.add_argument("--format", choices=["slack", "raw"], default="slack")
    parser.add_argument(
        "--notify-on",
        choices=["always", "hold", "warn", "hold_warn", "pass", "never"],
        default="always",
    )
    parser.add_argument("--timeout-sec", default="10")
    parser.add_argument("--max-retries", default="2", help="Retry count on transient failures")
    parser.add_argument("--retry-backoff-sec", default="2")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _should_send(status: str, notify_on: str) -> tuple[bool, str]:
    normalized = status.upper()
    if notify_on == "never":
        return False, "notify_on=never"
    if notify_on == "always":
        return True, "notify_on=always"
    if notify_on == "hold":
        return (normalized == "HOLD"), f"status={normalized} notify_on=hold"
    if notify_on == "warn":
        return (normalized == "WARN"), f"status={normalized} notify_on=warn"
    if notify_on == "hold_warn":
        return (normalized in {"HOLD", "WARN"}), f"status={normalized} notify_on=hold_warn"
    if notify_on == "pass":
        return (normalized == "PASS"), f"status={normalized} notify_on=pass"
    return True, "default"


def _build_message(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode == "raw":
        return payload

    slack_payload = payload.get("slack")
    if isinstance(slack_payload, dict) and (
        isinstance(slack_payload.get("text"), str) or isinstance(slack_payload.get("blocks"), list)
    ):
        return slack_payload

    text = str(payload.get("message_text", "")).strip()
    if not text:
        text = f"[{payload.get('status', 'INFO')}] {payload.get('release_prefix', 'release')}"
    return {"text": text}


def _redact_webhook_url(url: str) -> str:
    normalized = str(url).strip()
    if not normalized:
        return ""
    return "***"


def _is_retriable_http_status(status_code: int) -> bool:
    if status_code >= 500:
        return True
    return status_code in {408, 429}


def _compact_text(raw: Any, *, max_chars: int = 240) -> str:
    return compact_failure_detail(str(raw), max_chars=max_chars)


def _parse_retry_after_seconds(raw_value: str | None, *, now_utc: datetime | None = None) -> float | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None

    try:
        seconds = float(text)
        return 0.0 if seconds < 0 else seconds
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    reference_now = now_utc or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    delay = (parsed - reference_now).total_seconds()
    return 0.0 if delay < 0 else delay


def _send_json(
    *,
    url: str,
    payload: dict[str, Any],
    timeout_sec: float,
    max_retries: int,
    retry_backoff_sec: float,
) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    attempt = 0
    max_attempts = max_retries + 1
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                status_code = int(resp.status)
                resp_body = resp.read().decode("utf-8", errors="replace")
                return status_code, resp_body
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            resp_body = exc.read().decode("utf-8", errors="replace")
            retriable = _is_retriable_http_status(status_code)
            retry_after_header = exc.headers.get("Retry-After") if exc.headers is not None else None
            retry_after_seconds = _parse_retry_after_seconds(retry_after_header)
            if retriable and attempt <= max_retries:
                delay_sec = retry_backoff_sec if retry_after_seconds is None else retry_after_seconds
                print(
                    f"[warn] retrying notification after http_error status={status_code} "
                    f"attempts={attempt}/{max_attempts} delay_sec={delay_sec}",
                    file=sys.stderr,
                )
                time.sleep(delay_sec)
                continue
            body_for_log = _compact_text(resp_body)
            raise RuntimeError(
                f"http_error status={status_code} attempts={attempt}/{max_attempts} body={body_for_log}"
            ) from exc
        except urllib.error.URLError as exc:
            if attempt <= max_retries:
                print(
                    f"[warn] retrying notification after url_error reason={_compact_text(exc.reason)} "
                    f"attempts={attempt}/{max_attempts} delay_sec={retry_backoff_sec}",
                    file=sys.stderr,
                )
                time.sleep(retry_backoff_sec)
                continue
            raise RuntimeError(
                f"url_error reason={_compact_text(exc.reason)} attempts={attempt}/{max_attempts}"
            ) from exc


def main() -> int:
    args = parse_args()
    timeout_sec = parse_positive_float(str(args.timeout_sec), default=10.0, field="timeout-sec")
    max_retries = parse_non_negative_int(str(args.max_retries), default=2, field="max-retries")
    retry_backoff_sec = parse_non_negative_float(
        str(args.retry_backoff_sec),
        default=2.0,
        field="retry-backoff-sec",
    )

    payload_path = Path(args.payload_json).resolve()
    payload = load_json_object(payload_path, subject="payload json")

    status = str(payload.get("status", "")).strip()
    if not status:
        status = "INFO"

    should_send, reason = _should_send(status, args.notify_on)
    if not should_send:
        print(f"[skip] notification suppressed ({reason})")
        return 0

    webhook_url = str(args.webhook_url).strip()
    if not webhook_url:
        print("[skip] webhook_url is empty")
        return 0

    message = _build_message(payload, args.format)
    if args.dry_run:
        print(f"[dry-run] url={_redact_webhook_url(webhook_url)} format={args.format} status={status}")
        print(json.dumps(message, indent=2, ensure_ascii=True))
        return 0

    status_code, response_body = _send_json(
        url=webhook_url,
        payload=message,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        retry_backoff_sec=retry_backoff_sec,
    )
    print(f"[ok] sent status={status_code} format={args.format}")
    if response_body.strip():
        print(f"[ok] response={response_body.strip()}")
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="send_release_notification.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=SUMMARY_PHASE_SEND_NOTIFICATION,
        )
    )
