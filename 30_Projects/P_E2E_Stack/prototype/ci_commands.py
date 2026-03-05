#!/usr/bin/env python3
"""Shared command rendering helpers for CI wrappers."""

from __future__ import annotations

import shlex


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def redact_cmd(cmd: list[str], *, sensitive_flags: set[str] | None = None) -> list[str]:
    if not sensitive_flags:
        return list(cmd)

    redacted: list[str] = []
    index = 0
    while index < len(cmd):
        token = cmd[index]
        if "=" in token:
            flag, _value = token.split("=", 1)
            if flag in sensitive_flags:
                redacted.append(f"{flag}=***")
            else:
                redacted.append(token)
            index += 1
            continue

        redacted.append(token)
        if token in sensitive_flags and (index + 1) < len(cmd):
            redacted.append("***")
            index += 2
            continue

        index += 1

    return redacted


def render_cmd(cmd: list[str], *, sensitive_flags: set[str] | None = None) -> str:
    return shell_join(redact_cmd(cmd, sensitive_flags=sensitive_flags))
