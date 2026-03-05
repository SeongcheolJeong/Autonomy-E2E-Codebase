#!/usr/bin/env python3
"""Shared CI phase constants for structured error details."""

from __future__ import annotations

PHASE_RESOLVE_INPUTS = "resolve_inputs"

PIPELINE_PHASE_RUN_PIPELINE = "run_pipeline"

SUMMARY_PHASE_BUILD_SUMMARY = "build_summary"
SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD = "build_notification_payload"
SUMMARY_PHASE_SEND_NOTIFICATION = "send_notification"
SUMMARY_PHASE_PUBLISH_SUMMARY = "publish_summary"

SHELL_PHASE_DIFF_CHANGED_FILES = "diff_changed_files"
SHELL_PHASE_LOAD_PRECHECK_RULES = "load_precheck_rules"
SHELL_PHASE_EVALUATE_CHANGES = "evaluate_changes"
SHELL_PHASE_PARSE_PROFILE_IDS = "parse_profile_ids"
SHELL_PHASE_LOAD_MATRIX = "load_matrix"
SHELL_PHASE_RUN_PREFLIGHT_PHASE1 = "run_preflight_phase1"
SHELL_PHASE_RUN_PREFLIGHT_PHASE4 = "run_preflight_phase4"
SHELL_PHASE_RUN_PREFLIGHT_VALIDATE = "run_preflight_validate"
SHELL_PHASE_PUBLISH_PREFLIGHT_SUMMARY = "publish_preflight_summary"
SHELL_PHASE_PUBLISH_SKIP_SUMMARY = "publish_skip_summary"
SHELL_PHASE_PUBLISH_MATRIX_SELECTION_SUMMARY = "publish_matrix_selection_summary"
SHELL_PHASE_WRITE_OUTPUTS = "write_outputs"
