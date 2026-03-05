#!/usr/bin/env python3
"""Build chat/webhook-friendly notification payload from release summary JSON."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from ci_input_parsing import (
    parse_non_negative_float,
    parse_non_negative_int,
    parse_phase4_secondary_module_warn_thresholds,
    parse_positive_int,
)
from ci_phases import SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import load_json_object, utc_now_iso
from phase2_sensor_fidelity_summary_formatter import (
    format_phase2_sensor_fidelity_summary,
)
from phase4_linkage_contract import PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build release notification payload JSON")
    parser.add_argument("--summary-json", required=True, help="Path to release summary JSON")
    parser.add_argument("--out-json", required=True, help="Output notification payload JSON path")
    parser.add_argument("--workflow-name", default="", help="Optional workflow display name")
    parser.add_argument("--run-url", default="", help="Optional CI run URL")
    parser.add_argument("--max-codes", default="", help="Max reason codes to include in text (>0)")
    parser.add_argument(
        "--timing-total-warn-ms",
        default="",
        help="Warn threshold for timing_ms.total (0 disables threshold)",
    )
    parser.add_argument(
        "--timing-regression-baseline-ms",
        default="",
        help="Optional baseline ms for timing_total regression checks (0 disables)",
    )
    parser.add_argument(
        "--timing-regression-warn-ratio",
        default="",
        help="Warn when (current-baseline)/baseline >= ratio (0 disables)",
    )
    parser.add_argument(
        "--timing-regression-history-window",
        default="",
        help="Use latest N *_release_summary.json files to derive baseline median (0 disables)",
    )
    parser.add_argument(
        "--timing-regression-history-dir",
        default="",
        help="Optional directory for timing regression history scan (defaults to summary-json parent)",
    )
    parser.add_argument(
        "--timing-regression-history-outlier-method",
        default="none",
        choices=("none", "iqr"),
        help="Optional outlier filter for history totals before baseline selection",
    )
    parser.add_argument(
        "--timing-regression-history-trim-ratio",
        default="",
        help="Optional symmetric trim ratio [0, 0.5) applied to history totals before baseline selection",
    )
    parser.add_argument(
        "--phase4-primary-warn-ratio",
        default="",
        help="Warn when phase4 primary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--phase4-primary-hold-ratio",
        default="",
        help="Hold when phase4 primary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--phase4-primary-module-warn-thresholds",
        default="",
        help=(
            "Optional per-module primary coverage warn thresholds in module=ratio CSV "
            f"(modules: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT})"
        ),
    )
    parser.add_argument(
        "--phase4-primary-module-hold-thresholds",
        default="",
        help=(
            "Optional per-module primary coverage hold thresholds in module=ratio CSV "
            f"(modules: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT})"
        ),
    )
    parser.add_argument(
        "--phase4-secondary-warn-ratio",
        default="",
        help="Warn when phase4 secondary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--phase4-secondary-hold-ratio",
        default="",
        help="Hold when phase4 secondary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--phase4-secondary-warn-min-modules",
        default="1",
        help="Minimum secondary-module count required to evaluate phase4 secondary coverage warning (>0)",
    )
    parser.add_argument(
        "--phase4-secondary-module-warn-thresholds",
        default="",
        help=(
            "Optional per-module secondary coverage warn thresholds in module=ratio CSV "
            f"(modules: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT})"
        ),
    )
    parser.add_argument(
        "--phase4-secondary-module-hold-thresholds",
        default="",
        help=(
            "Optional per-module secondary coverage hold thresholds in module=ratio CSV "
            f"(modules: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT})"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-final-speed-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max final speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-final-speed-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max final speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-final-position-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max final position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-final-position-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max final position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-speed-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max delta speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-speed-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max delta speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-position-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max delta position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-position-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max delta position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-final-heading-abs-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute final heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-final-heading-abs-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute final heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-final-lateral-position-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute final lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-final-lateral-position-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute final lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-delta-heading-abs-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute delta heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-heading-abs-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute delta heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-lateral-position-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute delta lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-delta-lateral-position-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute delta lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-yaw-rate-abs-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute yaw rate exceeds this value in rad/s (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-yaw-rate-abs-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute yaw rate exceeds this value in rad/s (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-delta-yaw-rate-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute delta yaw rate exceeds this value in rad/s "
            "(computed from final-initial yaw rate, 0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-delta-yaw-rate-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute delta yaw rate exceeds this value in rad/s "
            "(computed from final-initial yaw rate, 0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-velocity-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral velocity (trace peak) exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-velocity-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral velocity (trace peak) exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-accel-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute longitudinal acceleration (trace peak) exceeds this "
            "value in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-accel-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute longitudinal acceleration (trace peak) exceeds this "
            "value in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-accel-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral acceleration (trace peak) exceeds this value "
            "in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-accel-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral acceleration (trace peak) exceeds this value "
            "in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-yaw-accel-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute yaw acceleration (trace peak) exceeds this value in "
            "rad/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-yaw-accel-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute yaw acceleration (trace peak) exceeds this value in "
            "rad/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-jerk-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute longitudinal jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-jerk-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute longitudinal jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-jerk-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-jerk-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-yaw-jerk-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute yaw jerk (trace peak) exceeds this value in "
            "rad/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-yaw-jerk-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute yaw jerk (trace peak) exceeds this value in "
            "rad/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-position-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral position (trace peak) exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-lateral-position-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral position (trace peak) exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-road-grade-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute road grade exceeds this value in % "
            "(computed from min/max road grade, 0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-road-grade-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute road grade exceeds this value in % "
            "(computed from min/max road grade, 0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-grade-force-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute grade force exceeds this value in N (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-grade-force-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute grade force exceeds this value in N (0 disables)",
    )
    parser.add_argument(
        "--phase3-vehicle-control-overlap-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics throttle-brake overlap ratio exceeds this value "
            "in range [0, 1] (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-control-overlap-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics throttle-brake overlap ratio exceeds this value "
            "in range [0, 1] (0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-control-steering-rate-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute steering rate exceeds this value in deg/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-control-steering-rate-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute steering rate exceeds this value in deg/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-control-throttle-plus-brake-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max throttle+brake command sum exceeds this value "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-control-throttle-plus-brake-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max throttle+brake command sum exceeds this value "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-speed-tracking-error-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-speed-tracking-error-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-speed-tracking-error-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-vehicle-speed-tracking-error-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-core-sim-min-ttc-same-lane-warn-min",
        default="0",
        help="Warn when phase3 core sim min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-min-ttc-same-lane-hold-min",
        default="0",
        help="Hold when phase3 core sim min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-min-ttc-any-lane-warn-min",
        default="0",
        help="Warn when phase3 core sim min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-min-ttc-any-lane-hold-min",
        default="0",
        help="Hold when phase3 core sim min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-collision-warn-max",
        default="0",
        help="Warn when phase3 core sim collision manifest count exceeds this value",
    )
    parser.add_argument(
        "--phase3-core-sim-collision-hold-max",
        default="0",
        help="Hold when phase3 core sim collision manifest count exceeds this value",
    )
    parser.add_argument(
        "--phase3-core-sim-timeout-warn-max",
        default="0",
        help="Warn when phase3 core sim timeout manifest count exceeds this value",
    )
    parser.add_argument(
        "--phase3-core-sim-timeout-hold-max",
        default="0",
        help="Hold when phase3 core sim timeout manifest count exceeds this value",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-hold-warn-max",
        default="0",
        help="Warn when phase3 core sim gate HOLD manifest count exceeds this value",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-hold-hold-max",
        default="0",
        help="Hold when phase3 core sim gate HOLD manifest count exceeds this value",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-min-ttc-same-lane-warn-min",
        default="0",
        help="Warn when phase3 core sim matrix min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-min-ttc-same-lane-hold-min",
        default="0",
        help="Hold when phase3 core sim matrix min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-min-ttc-any-lane-warn-min",
        default="0",
        help="Warn when phase3 core sim matrix min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-min-ttc-any-lane-hold-min",
        default="0",
        help="Hold when phase3 core sim matrix min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-failed-cases-warn-max",
        default="0",
        help="Warn when phase3 core sim matrix failed case total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-failed-cases-hold-max",
        default="0",
        help="Hold when phase3 core sim matrix failed case total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-collision-cases-warn-max",
        default="0",
        help="Warn when phase3 core sim matrix collision case total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-collision-cases-hold-max",
        default="0",
        help="Hold when phase3 core sim matrix collision case total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-timeout-cases-warn-max",
        default="0",
        help="Warn when phase3 core sim matrix timeout case total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-timeout-cases-hold-max",
        default="0",
        help="Hold when phase3 core sim matrix timeout case total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-min-ttc-same-lane-warn-min",
        default="0",
        help="Warn when phase3 lane risk min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-min-ttc-same-lane-hold-min",
        default="0",
        help="Hold when phase3 lane risk min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-min-ttc-adjacent-lane-warn-min",
        default="0",
        help="Warn when phase3 lane risk min TTC (adjacent lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-min-ttc-adjacent-lane-hold-min",
        default="0",
        help="Hold when phase3 lane risk min TTC (adjacent lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-min-ttc-any-lane-warn-min",
        default="0",
        help="Warn when phase3 lane risk min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-min-ttc-any-lane-hold-min",
        default="0",
        help="Hold when phase3 lane risk min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-same-lane-warn-max",
        default="0",
        help="Warn when phase3 lane risk ttc_under_3s same-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-same-lane-hold-max",
        default="0",
        help="Hold when phase3 lane risk ttc_under_3s same-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-warn-max",
        default="0",
        help="Warn when phase3 lane risk ttc_under_3s adjacent-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-hold-max",
        default="0",
        help="Hold when phase3 lane risk ttc_under_3s adjacent-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-any-lane-warn-max",
        default="0",
        help="Warn when phase3 lane risk ttc_under_3s any-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-any-lane-hold-max",
        default="0",
        help="Hold when phase3 lane risk ttc_under_3s any-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 lane risk ttc_under_3s same-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 lane risk ttc_under_3s same-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 lane risk ttc_under_3s adjacent-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 lane risk ttc_under_3s adjacent-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 lane risk ttc_under_3s any-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 lane risk ttc_under_3s any-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-run-summary-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic run-summary count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-run-summary-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic run-summary count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-profile-count-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic unique profile count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-profile-count-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic unique profile count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-actor-pattern-count-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic unique actor-pattern count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-actor-pattern-count-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic unique actor-pattern count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-avg-npc-count-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic average NPC count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase3-dataset-traffic-avg-npc-count-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic average NPC count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-lane-execution-warn-min-exec-rows",
        default="0",
        help=(
            "Warn when runtime lane execution exec lane row count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-lane-execution-hold-min-exec-rows",
        default="0",
        help=(
            "Hold when runtime lane execution exec lane row count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-compare-warn-min-artifacts-with-diffs",
        default="0",
        help=(
            "Warn when runtime evidence compare artifacts_with_diffs_count is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-compare-hold-min-artifacts-with-diffs",
        default="0",
        help=(
            "Hold when runtime evidence compare artifacts_with_diffs_count is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-compare-warn-min-interop-import-mode-diff-count",
        default="0",
        help=(
            "Warn when runtime evidence compare interop import mode diff count total is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-compare-hold-min-interop-import-mode-diff-count",
        default="0",
        help=(
            "Hold when runtime evidence compare interop import mode diff count total is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-interop-contract-checked-warn-min",
        default="0",
        help=(
            "Warn when runtime evidence interop contract checked count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-interop-contract-checked-hold-min",
        default="0",
        help=(
            "Hold when runtime evidence interop contract checked count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-interop-contract-fail-warn-max",
        default="0",
        help=(
            "Warn when runtime evidence interop contract fail/non-pass count exceeds this maximum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--runtime-evidence-interop-contract-fail-hold-max",
        default="0",
        help=(
            "Hold when runtime evidence interop contract fail/non-pass count exceeds this maximum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--phase2-map-routing-unreachable-lanes-warn-max",
        default="0",
        help="Warn when phase2 map routing unreachable lane count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-map-routing-unreachable-lanes-hold-max",
        default="0",
        help="Hold when phase2 map routing unreachable lane count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-map-routing-non-reciprocal-links-warn-max",
        default="0",
        help="Warn when phase2 map routing non-reciprocal link count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-map-routing-non-reciprocal-links-hold-max",
        default="0",
        help="Hold when phase2 map routing non-reciprocal link count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-map-routing-continuity-gap-warn-max",
        default="0",
        help="Warn when phase2 map routing continuity-gap warning count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-map-routing-continuity-gap-hold-max",
        default="0",
        help="Hold when phase2 map routing continuity-gap warning count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-fidelity-score-avg-warn-min",
        default="0",
        help="Warn when phase2 sensor fidelity score average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-fidelity-score-avg-hold-min",
        default="0",
        help="Hold when phase2 sensor fidelity score average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-frame-count-avg-warn-min",
        default="0",
        help="Warn when phase2 sensor frame-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-frame-count-avg-hold-min",
        default="0",
        help="Hold when phase2 sensor frame-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-camera-noise-stddev-px-avg-warn-max",
        default="0",
        help="Warn when phase2 camera noise average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-camera-noise-stddev-px-avg-hold-max",
        default="0",
        help="Hold when phase2 camera noise average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-lidar-point-count-avg-warn-min",
        default="0",
        help="Warn when phase2 lidar point-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-lidar-point-count-avg-hold-min",
        default="0",
        help="Hold when phase2 lidar point-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-radar-false-positive-rate-avg-warn-max",
        default="0",
        help="Warn when phase2 radar false-positive-rate average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--phase2-sensor-radar-false-positive-rate-avg-hold-max",
        default="0",
        help="Hold when phase2 radar false-positive-rate average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--phase2-log-replay-fail-warn-max",
        default="0",
        help="Warn when phase2 log replay fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-log-replay-fail-hold-max",
        default="0",
        help="Hold when phase2 log replay fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-log-replay-missing-summary-warn-max",
        default="0",
        help="Warn when phase2 log replay missing-summary count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--phase2-log-replay-missing-summary-hold-max",
        default="0",
        help="Hold when phase2 log replay missing-summary count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--runtime-native-smoke-fail-warn-max",
        default="0",
        help="Warn when runtime native smoke fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--runtime-native-smoke-fail-hold-max",
        default="0",
        help="Hold when runtime native smoke fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--runtime-native-smoke-partial-warn-max",
        default="0",
        help="Warn when runtime native smoke partial-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--runtime-native-smoke-partial-hold-max",
        default="0",
        help="Hold when runtime native smoke partial-count exceeds this value (0 disables)",
    )
    return parser.parse_args()


def _as_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, raw in value.items():
        try:
            counts[str(key)] = int(raw)
        except (TypeError, ValueError):
            continue
    return counts


def _as_non_negative_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, int] = {}
    for key, raw in value.items():
        try:
            parsed[str(key)] = max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return parsed


def _as_non_negative_int_nested_map(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, dict[str, int]] = {}
    for raw_outer_key, raw_inner in value.items():
        outer_key = str(raw_outer_key).strip()
        if not outer_key:
            continue
        if not isinstance(raw_inner, dict):
            continue
        inner_parsed: dict[str, int] = {}
        for raw_inner_key, raw_inner_value in raw_inner.items():
            inner_key = str(raw_inner_key).strip()
            if not inner_key:
                continue
            try:
                inner_parsed[inner_key] = max(0, int(raw_inner_value))
            except (TypeError, ValueError):
                continue
        if inner_parsed:
            parsed[outer_key] = inner_parsed
    return parsed


def _as_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    text = str(value).strip().lower()
    if not text:
        return False
    return text in {"1", "true", "yes", "y", "on"}


def _threshold_sort_key(raw_key: str) -> tuple[int, int | str]:
    key_text = str(raw_key).strip()
    try:
        return (0, int(key_text))
    except (TypeError, ValueError):
        return (1, key_text)


def _format_threshold_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "n/a"
    parts: list[str] = []
    for key in sorted(counts.keys(), key=_threshold_sort_key):
        key_text = str(key).strip()
        if not key_text:
            continue
        try:
            value = int(counts[key])
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        parts.append(f"{key_text}:{value}")
    return ",".join(parts) if parts else "n/a"


def _format_float_counts(values: dict[str, float], *, decimals: int = 6) -> str:
    if not values:
        return "n/a"
    parts: list[str] = []
    for key in sorted(values.keys()):
        key_text = str(key).strip()
        if not key_text:
            continue
        raw_value = values.get(key_text, values.get(key))
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        parts.append(f"{key_text}:{value:.{decimals}f}")
    return ",".join(parts) if parts else "n/a"


def _format_float_nested_counts(values: dict[str, dict[str, float]], *, decimals: int = 6) -> str:
    if not values:
        return "n/a"
    parts: list[str] = []
    for outer_key in sorted(values.keys()):
        outer_key_text = str(outer_key).strip()
        if not outer_key_text:
            continue
        inner_values = values.get(outer_key, {})
        if not isinstance(inner_values, dict):
            continue
        for inner_key in sorted(inner_values.keys()):
            inner_key_text = str(inner_key).strip()
            if not inner_key_text:
                continue
            raw_value = inner_values.get(inner_key)
            if raw_value is None or isinstance(raw_value, bool):
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            parts.append(f"{outer_key_text}|{inner_key_text}:{value:.{decimals}f}")
    return ",".join(parts) if parts else "n/a"


def _format_non_negative_int_counts(values: dict[str, int]) -> str:
    if not values:
        return "n/a"
    parts: list[str] = []
    for key in sorted(values.keys()):
        key_text = str(key).strip()
        if not key_text:
            continue
        raw_value = values.get(key, values.get(key_text))
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        parts.append(f"{key_text}:{value}")
    return ",".join(parts) if parts else "n/a"


def _format_non_negative_int_nested_counts(values: dict[str, dict[str, int]]) -> str:
    if not values:
        return "n/a"
    parts: list[str] = []
    for outer_key in sorted(values.keys()):
        outer_key_text = str(outer_key).strip()
        if not outer_key_text:
            continue
        inner_values = values.get(outer_key, {})
        if not isinstance(inner_values, dict):
            continue
        for inner_key in sorted(inner_values.keys()):
            inner_key_text = str(inner_key).strip()
            if not inner_key_text:
                continue
            raw_value = inner_values.get(inner_key)
            if raw_value is None or isinstance(raw_value, bool):
                continue
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            parts.append(f"{outer_key_text}|{inner_key_text}:{value}")
    return ",".join(parts) if parts else "n/a"


def _threshold_count_mismatch(counts: dict[str, int], expected_threshold: int) -> bool:
    if not counts:
        return False
    expected_key = str(max(0, int(expected_threshold)))
    if int(counts.get(expected_key, 0) or 0) <= 0:
        return True
    for key, raw_value in counts.items():
        key_text = str(key).strip()
        if not key_text or key_text == expected_key:
            continue
        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return True
    return False


def _threshold_float_count_mismatch(
    counts: dict[str, int],
    expected_threshold: float,
    *,
    abs_tol: float = 1e-6,
) -> bool:
    if not counts:
        return False
    expected_value = max(0.0, float(expected_threshold))
    expected_found = False
    observed_positive = False
    for key, raw_value in counts.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        try:
            count = int(raw_value)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        observed_positive = True
        try:
            observed_threshold = float(key_text)
        except (TypeError, ValueError):
            return True
        if math.isclose(observed_threshold, expected_value, rel_tol=0.0, abs_tol=abs_tol):
            expected_found = True
            continue
        return True
    if not observed_positive:
        return False
    return not expected_found


def _as_float_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, raw in value.items():
        name = str(key).strip().lower()
        if not name:
            continue
        try:
            parsed[name] = float(raw)
        except (TypeError, ValueError):
            parsed[name] = 0.0
    return parsed


def _as_float_nested_map(value: Any) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, dict[str, float]] = {}
    for outer_key, raw_inner in value.items():
        outer_name = str(outer_key).strip().lower()
        if not outer_name:
            continue
        if not isinstance(raw_inner, dict):
            continue
        parsed.setdefault(outer_name, {})
        for inner_key, raw_value in raw_inner.items():
            inner_name = str(inner_key).strip().lower()
            if not inner_name:
                continue
            try:
                parsed[outer_name][inner_name] = float(raw_value)
            except (TypeError, ValueError):
                parsed[outer_name][inner_name] = 0.0
    return parsed


def _status_from_counts(
    final_counts: dict[str, int],
    pipeline_overall_counts: dict[str, int],
    pipeline_trend_counts: dict[str, int],
) -> str:
    if (
        final_counts.get("HOLD", 0) > 0
        or pipeline_overall_counts.get("HOLD", 0) > 0
        or pipeline_trend_counts.get("HOLD", 0) > 0
    ):
        return "HOLD"
    if (
        final_counts.get("PASS", 0) > 0
        or pipeline_overall_counts.get("PASS", 0) > 0
        or pipeline_trend_counts.get("PASS", 0) > 0
    ):
        return "PASS"

    for counts in (final_counts, pipeline_overall_counts, pipeline_trend_counts):
        for key, value in counts.items():
            if value <= 0:
                continue
            if key not in {"PASS", "HOLD", "N/A"}:
                return "WARN"

    if final_counts:
        return "WARN"
    return "INFO"


def _truncate_list(values: list[str], max_items: int) -> list[str]:
    if len(values) <= max_items:
        return values
    return values[:max_items] + [f"... (+{len(values) - max_items} more)"]


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text or text.lower() == "n/a":
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _count_string_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _extract_threshold_drift_hold_policy_reason_keys_from_failures(failures: list[str]) -> list[str]:
    extracted: list[str] = []
    for failure in failures:
        extracted.extend(_extract_threshold_drift_hold_policy_reason_keys_from_failure(str(failure)))
    return extracted


def _extract_threshold_drift_hold_policy_reason_keys_from_failure(failure: str) -> list[str]:
    text = str(failure).strip()
    if "reason_keys=" not in text:
        return []
    tail = text.split("reason_keys=", 1)[1].strip()
    if not tail or tail.lower() == "n/a":
        return []
    return [part.strip() for part in tail.split(",") if part.strip()]


def _extract_threshold_drift_hold_policy_scope_from_failure(failure: str) -> str:
    text = str(failure).strip()
    if not text:
        return "unknown"
    marker = " threshold drift hold policy failed"
    marker_idx = text.lower().find(marker)
    if marker_idx <= 0:
        return "unknown"
    scope_text = text[:marker_idx].strip().lower()
    if not scope_text:
        return "unknown"
    return scope_text.replace(" ", "_")


def _extract_threshold_drift_hold_policy_scopes_from_failures(failures: list[str]) -> list[str]:
    extracted: list[str] = []
    for failure in failures:
        extracted.append(_extract_threshold_drift_hold_policy_scope_from_failure(str(failure)))
    return extracted


def _extract_threshold_drift_hold_policy_scope_reason_key_counts_from_failures(
    failures: list[str],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for failure in failures:
        scope_key = _extract_threshold_drift_hold_policy_scope_from_failure(str(failure))
        reason_keys = _extract_threshold_drift_hold_policy_reason_keys_from_failure(str(failure))
        if not reason_keys:
            continue
        scope_reason_counts = counts.setdefault(scope_key, {})
        for reason_key in reason_keys:
            scope_reason_counts[reason_key] = scope_reason_counts.get(reason_key, 0) + 1
    return counts


def _format_string_list(values: Any, *, max_items: int = 20) -> str:
    if not isinstance(values, list):
        return "n/a"
    normalized: list[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            normalized.append(text)
    if not normalized:
        return "n/a"
    return ",".join(_truncate_list(normalized, max(1, int(max_items))))


def _slowest_timing_stages(timing_ms: dict[str, int], *, limit: int = 3) -> list[dict[str, int | str]]:
    if not timing_ms:
        return []
    pairs: list[tuple[str, int]] = []
    for stage, value in timing_ms.items():
        if str(stage) == "total":
            continue
        pairs.append((str(stage), int(value)))
    pairs.sort(key=lambda item: (-item[1], item[0]))
    selected = pairs[: max(1, int(limit))]
    return [{"stage": stage, "ms": value} for stage, value in selected]


def _format_slowest_timing(stages: list[dict[str, int | str]]) -> str:
    if not stages:
        return "n/a"
    parts: list[str] = []
    for item in stages:
        stage = str(item.get("stage", "")).strip()
        if not stage:
            continue
        try:
            ms = int(item.get("ms", 0))
        except (TypeError, ValueError):
            continue
        parts.append(f"{stage}:{ms}")
    return ", ".join(parts) if parts else "n/a"


def _summarize_phase4_module_violation_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        module_name = str(row.get("module", "")).strip().lower()
        if not module_name:
            continue
        batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
        try:
            coverage_ratio = float(row.get("coverage_ratio", 0.0))
        except (TypeError, ValueError):
            coverage_ratio = 0.0
        try:
            threshold = float(row.get("threshold", 0.0))
        except (TypeError, ValueError):
            threshold = 0.0

        current = summary.get(module_name)
        if current is None:
            summary[module_name] = {
                "violation_count": 1,
                "threshold": threshold,
                "min_coverage_ratio": coverage_ratio,
                "min_batch_id": batch_id,
            }
            continue

        current["violation_count"] = int(current.get("violation_count", 0)) + 1
        current["threshold"] = threshold
        current_min_coverage = float(current.get("min_coverage_ratio", 0.0))
        current_min_batch = str(current.get("min_batch_id", "")).strip() or "batch_unknown"
        if coverage_ratio < current_min_coverage or (
            coverage_ratio == current_min_coverage and batch_id < current_min_batch
        ):
            current["min_coverage_ratio"] = coverage_ratio
            current["min_batch_id"] = batch_id

    return {module_name: summary[module_name] for module_name in sorted(summary.keys())}


def _format_phase4_module_violation_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict) or not summary:
        return "n/a"
    parts: list[str] = []
    for module_name in sorted(summary.keys()):
        row = summary.get(module_name)
        if not isinstance(row, dict):
            continue
        violation_count = int(row.get("violation_count", 0))
        threshold = float(row.get("threshold", 0.0))
        min_coverage_ratio = float(row.get("min_coverage_ratio", 0.0))
        min_batch_id = str(row.get("min_batch_id", "")).strip() or "batch_unknown"
        parts.append(
            f"{module_name}:count={violation_count},min_cov={min_coverage_ratio:.3f},"
            f"threshold={threshold:.3f},batch={min_batch_id}"
        )
    return "; ".join(parts) if parts else "n/a"


def _summarize_phase4_secondary_module_violation_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _summarize_phase4_module_violation_rows(rows)


def _format_phase4_secondary_module_violation_summary(summary: dict[str, Any]) -> str:
    return _format_phase4_module_violation_summary(summary)


def _format_phase3_vehicle_dynamics_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"

    models_raw = summary.get("models", [])
    models_text = (
        ",".join(str(item).strip() for item in models_raw if str(item).strip())
        if isinstance(models_raw, list)
        else ""
    ) or "n/a"
    try:
        dynamic_enabled_count = int(summary.get("dynamic_enabled_manifest_count", 0))
    except (TypeError, ValueError):
        dynamic_enabled_count = 0

    try:
        min_speed = float(summary.get("min_final_speed_mps", 0.0))
    except (TypeError, ValueError):
        min_speed = 0.0
    try:
        avg_speed = float(summary.get("avg_final_speed_mps", 0.0))
    except (TypeError, ValueError):
        avg_speed = 0.0
    try:
        max_speed = float(summary.get("max_final_speed_mps", 0.0))
    except (TypeError, ValueError):
        max_speed = 0.0
    try:
        min_position = float(summary.get("min_final_position_m", 0.0))
    except (TypeError, ValueError):
        min_position = 0.0
    try:
        avg_position = float(summary.get("avg_final_position_m", 0.0))
    except (TypeError, ValueError):
        avg_position = 0.0
    try:
        max_position = float(summary.get("max_final_position_m", 0.0))
    except (TypeError, ValueError):
        max_position = 0.0
    try:
        min_delta_speed = float(summary.get("min_delta_speed_mps", 0.0))
    except (TypeError, ValueError):
        min_delta_speed = 0.0
    try:
        avg_delta_speed = float(summary.get("avg_delta_speed_mps", 0.0))
    except (TypeError, ValueError):
        avg_delta_speed = 0.0
    try:
        max_delta_speed = float(summary.get("max_delta_speed_mps", 0.0))
    except (TypeError, ValueError):
        max_delta_speed = 0.0
    try:
        min_delta_position = float(summary.get("min_delta_position_m", 0.0))
    except (TypeError, ValueError):
        min_delta_position = 0.0
    try:
        avg_delta_position = float(summary.get("avg_delta_position_m", 0.0))
    except (TypeError, ValueError):
        avg_delta_position = 0.0
    try:
        max_delta_position = float(summary.get("max_delta_position_m", 0.0))
    except (TypeError, ValueError):
        max_delta_position = 0.0
    try:
        min_heading = float(summary.get("min_final_heading_deg", 0.0))
    except (TypeError, ValueError):
        min_heading = 0.0
    try:
        avg_heading = float(summary.get("avg_final_heading_deg", 0.0))
    except (TypeError, ValueError):
        avg_heading = 0.0
    try:
        max_heading = float(summary.get("max_final_heading_deg", 0.0))
    except (TypeError, ValueError):
        max_heading = 0.0
    try:
        min_lateral_position = float(summary.get("min_final_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        min_lateral_position = 0.0
    try:
        avg_lateral_position = float(summary.get("avg_final_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        avg_lateral_position = 0.0
    try:
        max_lateral_position = float(summary.get("max_final_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        max_lateral_position = 0.0
    try:
        min_lateral_velocity = float(summary.get("min_final_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        min_lateral_velocity = 0.0
    try:
        avg_lateral_velocity = float(summary.get("avg_final_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        avg_lateral_velocity = 0.0
    try:
        max_lateral_velocity = float(summary.get("max_final_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        max_lateral_velocity = 0.0
    try:
        min_yaw_rate_final = float(summary.get("min_final_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        min_yaw_rate_final = 0.0
    try:
        avg_yaw_rate_final = float(summary.get("avg_final_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        avg_yaw_rate_final = 0.0
    try:
        max_yaw_rate_final = float(summary.get("max_final_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        max_yaw_rate_final = 0.0
    try:
        min_delta_heading = float(summary.get("min_delta_heading_deg", 0.0))
    except (TypeError, ValueError):
        min_delta_heading = 0.0
    try:
        avg_delta_heading = float(summary.get("avg_delta_heading_deg", 0.0))
    except (TypeError, ValueError):
        avg_delta_heading = 0.0
    try:
        max_delta_heading = float(summary.get("max_delta_heading_deg", 0.0))
    except (TypeError, ValueError):
        max_delta_heading = 0.0
    try:
        min_delta_lateral_position = float(summary.get("min_delta_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        min_delta_lateral_position = 0.0
    try:
        avg_delta_lateral_position = float(summary.get("avg_delta_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        avg_delta_lateral_position = 0.0
    try:
        max_delta_lateral_position = float(summary.get("max_delta_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        max_delta_lateral_position = 0.0
    try:
        min_delta_lateral_velocity = float(summary.get("min_delta_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        min_delta_lateral_velocity = 0.0
    try:
        avg_delta_lateral_velocity = float(summary.get("avg_delta_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        avg_delta_lateral_velocity = 0.0
    try:
        max_delta_lateral_velocity = float(summary.get("max_delta_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        max_delta_lateral_velocity = 0.0
    try:
        min_delta_yaw_rate = float(summary.get("min_delta_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        min_delta_yaw_rate = 0.0
    try:
        avg_delta_yaw_rate = float(summary.get("avg_delta_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        avg_delta_yaw_rate = 0.0
    try:
        max_delta_yaw_rate = float(summary.get("max_delta_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        max_delta_yaw_rate = 0.0
    try:
        max_abs_yaw_rate = float(summary.get("max_abs_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        max_abs_yaw_rate = 0.0
    try:
        max_abs_lateral_velocity = float(summary.get("max_abs_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_velocity = 0.0
    try:
        max_abs_accel = float(summary.get("max_abs_accel_mps2", 0.0))
    except (TypeError, ValueError):
        max_abs_accel = 0.0
    try:
        max_abs_lateral_accel = float(summary.get("max_abs_lateral_accel_mps2", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_accel = 0.0
    try:
        max_abs_yaw_accel = float(summary.get("max_abs_yaw_accel_rps2", 0.0))
    except (TypeError, ValueError):
        max_abs_yaw_accel = 0.0
    try:
        max_abs_jerk = float(summary.get("max_abs_jerk_mps3", 0.0))
    except (TypeError, ValueError):
        max_abs_jerk = 0.0
    try:
        max_abs_lateral_jerk = float(summary.get("max_abs_lateral_jerk_mps3", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_jerk = 0.0
    try:
        max_abs_yaw_jerk = float(summary.get("max_abs_yaw_jerk_rps3", 0.0))
    except (TypeError, ValueError):
        max_abs_yaw_jerk = 0.0
    try:
        max_abs_lateral_position = float(summary.get("max_abs_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_position = 0.0
    try:
        min_road_grade = float(summary.get("min_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        min_road_grade = 0.0
    try:
        avg_road_grade = float(summary.get("avg_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        avg_road_grade = 0.0
    try:
        max_road_grade = float(summary.get("max_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        max_road_grade = 0.0
    try:
        max_abs_grade_force = float(summary.get("max_abs_grade_force_n", 0.0))
    except (TypeError, ValueError):
        max_abs_grade_force = 0.0
    try:
        control_command_manifest_count = int(summary.get("control_command_manifest_count", 0))
    except (TypeError, ValueError):
        control_command_manifest_count = 0
    try:
        control_command_step_count_total = int(summary.get("control_command_step_count_total", 0))
    except (TypeError, ValueError):
        control_command_step_count_total = 0
    try:
        control_overlap_step_count_total = int(summary.get("control_throttle_brake_overlap_step_count_total", 0))
    except (TypeError, ValueError):
        control_overlap_step_count_total = 0
    try:
        control_overlap_ratio_avg = float(summary.get("control_throttle_brake_overlap_ratio_avg", 0.0))
    except (TypeError, ValueError):
        control_overlap_ratio_avg = 0.0
    try:
        control_overlap_ratio_max = float(summary.get("control_throttle_brake_overlap_ratio_max", 0.0))
    except (TypeError, ValueError):
        control_overlap_ratio_max = 0.0
    try:
        control_steering_rate_avg = float(summary.get("control_max_abs_steering_rate_degps_avg", 0.0))
    except (TypeError, ValueError):
        control_steering_rate_avg = 0.0
    try:
        control_steering_rate_max = float(summary.get("control_max_abs_steering_rate_degps_max", 0.0))
    except (TypeError, ValueError):
        control_steering_rate_max = 0.0
    try:
        control_throttle_rate_avg = float(summary.get("control_max_abs_throttle_rate_per_sec_avg", 0.0))
    except (TypeError, ValueError):
        control_throttle_rate_avg = 0.0
    try:
        control_throttle_rate_max = float(summary.get("control_max_abs_throttle_rate_per_sec_max", 0.0))
    except (TypeError, ValueError):
        control_throttle_rate_max = 0.0
    try:
        control_brake_rate_avg = float(summary.get("control_max_abs_brake_rate_per_sec_avg", 0.0))
    except (TypeError, ValueError):
        control_brake_rate_avg = 0.0
    try:
        control_brake_rate_max = float(summary.get("control_max_abs_brake_rate_per_sec_max", 0.0))
    except (TypeError, ValueError):
        control_brake_rate_max = 0.0
    try:
        control_throttle_plus_brake_avg = float(summary.get("control_max_throttle_plus_brake_avg", 0.0))
    except (TypeError, ValueError):
        control_throttle_plus_brake_avg = 0.0
    try:
        control_throttle_plus_brake_max = float(summary.get("control_max_throttle_plus_brake_max", 0.0))
    except (TypeError, ValueError):
        control_throttle_plus_brake_max = 0.0
    try:
        speed_tracking_manifest_count = int(summary.get("speed_tracking_manifest_count", 0))
    except (TypeError, ValueError):
        speed_tracking_manifest_count = 0
    try:
        speed_tracking_target_step_count_total = int(summary.get("speed_tracking_target_step_count_total", 0))
    except (TypeError, ValueError):
        speed_tracking_target_step_count_total = 0
    try:
        min_speed_tracking_error = float(summary.get("min_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        min_speed_tracking_error = 0.0
    try:
        avg_speed_tracking_error = float(summary.get("avg_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        avg_speed_tracking_error = 0.0
    try:
        max_speed_tracking_error = float(summary.get("max_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        max_speed_tracking_error = 0.0
    try:
        avg_abs_speed_tracking_error = float(summary.get("avg_abs_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        avg_abs_speed_tracking_error = 0.0
    try:
        max_abs_speed_tracking_error = float(summary.get("max_abs_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        max_abs_speed_tracking_error = 0.0

    lowest_speed_batch = str(summary.get("lowest_speed_batch_id", "")).strip() or "n/a"
    highest_speed_batch = str(summary.get("highest_speed_batch_id", "")).strip() or "n/a"
    lowest_position_batch = str(summary.get("lowest_position_batch_id", "")).strip() or "n/a"
    highest_position_batch = str(summary.get("highest_position_batch_id", "")).strip() or "n/a"
    lowest_delta_speed_batch = str(summary.get("lowest_delta_speed_batch_id", "")).strip() or "n/a"
    highest_delta_speed_batch = str(summary.get("highest_delta_speed_batch_id", "")).strip() or "n/a"
    lowest_delta_position_batch = str(summary.get("lowest_delta_position_batch_id", "")).strip() or "n/a"
    highest_delta_position_batch = str(summary.get("highest_delta_position_batch_id", "")).strip() or "n/a"
    lowest_heading_batch = str(summary.get("lowest_heading_batch_id", "")).strip() or "n/a"
    highest_heading_batch = str(summary.get("highest_heading_batch_id", "")).strip() or "n/a"
    lowest_lateral_position_batch = str(summary.get("lowest_lateral_position_batch_id", "")).strip() or "n/a"
    highest_lateral_position_batch = str(summary.get("highest_lateral_position_batch_id", "")).strip() or "n/a"
    lowest_lateral_velocity_batch = str(summary.get("lowest_lateral_velocity_batch_id", "")).strip() or "n/a"
    highest_lateral_velocity_batch = str(summary.get("highest_lateral_velocity_batch_id", "")).strip() or "n/a"
    lowest_yaw_rate_batch = str(summary.get("lowest_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_yaw_rate_batch = str(summary.get("highest_yaw_rate_batch_id", "")).strip() or "n/a"
    lowest_delta_heading_batch = str(summary.get("lowest_delta_heading_batch_id", "")).strip() or "n/a"
    highest_delta_heading_batch = str(summary.get("highest_delta_heading_batch_id", "")).strip() or "n/a"
    lowest_delta_lateral_position_batch = (
        str(summary.get("lowest_delta_lateral_position_batch_id", "")).strip() or "n/a"
    )
    highest_delta_lateral_position_batch = (
        str(summary.get("highest_delta_lateral_position_batch_id", "")).strip() or "n/a"
    )
    lowest_delta_lateral_velocity_batch = (
        str(summary.get("lowest_delta_lateral_velocity_batch_id", "")).strip() or "n/a"
    )
    highest_delta_lateral_velocity_batch = (
        str(summary.get("highest_delta_lateral_velocity_batch_id", "")).strip() or "n/a"
    )
    lowest_delta_yaw_rate_batch = str(summary.get("lowest_delta_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_delta_yaw_rate_batch = str(summary.get("highest_delta_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_abs_yaw_rate_batch = str(summary.get("highest_abs_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_velocity_batch = (
        str(summary.get("highest_abs_lateral_velocity_batch_id", "")).strip() or "n/a"
    )
    highest_abs_accel_batch = str(summary.get("highest_abs_accel_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_accel_batch = (
        str(summary.get("highest_abs_lateral_accel_batch_id", "")).strip() or "n/a"
    )
    highest_abs_yaw_accel_batch = str(summary.get("highest_abs_yaw_accel_batch_id", "")).strip() or "n/a"
    highest_abs_jerk_batch = str(summary.get("highest_abs_jerk_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_jerk_batch = (
        str(summary.get("highest_abs_lateral_jerk_batch_id", "")).strip() or "n/a"
    )
    highest_abs_yaw_jerk_batch = str(summary.get("highest_abs_yaw_jerk_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_position_batch = (
        str(summary.get("highest_abs_lateral_position_batch_id", "")).strip() or "n/a"
    )
    lowest_road_grade_batch = str(summary.get("lowest_road_grade_batch_id", "")).strip() or "n/a"
    highest_road_grade_batch = str(summary.get("highest_road_grade_batch_id", "")).strip() or "n/a"
    highest_abs_grade_force_batch = str(summary.get("highest_abs_grade_force_batch_id", "")).strip() or "n/a"
    highest_control_overlap_batch = str(summary.get("highest_control_overlap_ratio_batch_id", "")).strip() or "n/a"
    highest_control_steering_rate_batch = (
        str(summary.get("highest_control_steering_rate_batch_id", "")).strip() or "n/a"
    )
    highest_control_throttle_rate_batch = (
        str(summary.get("highest_control_throttle_rate_batch_id", "")).strip() or "n/a"
    )
    highest_control_brake_rate_batch = str(summary.get("highest_control_brake_rate_batch_id", "")).strip() or "n/a"
    highest_control_throttle_plus_brake_batch = (
        str(summary.get("highest_control_throttle_plus_brake_batch_id", "")).strip() or "n/a"
    )
    lowest_speed_tracking_error_batch = (
        str(summary.get("lowest_speed_tracking_error_batch_id", "")).strip() or "n/a"
    )
    highest_speed_tracking_error_batch = (
        str(summary.get("highest_speed_tracking_error_batch_id", "")).strip() or "n/a"
    )
    highest_abs_speed_tracking_error_batch = (
        str(summary.get("highest_abs_speed_tracking_error_batch_id", "")).strip() or "n/a"
    )

    return (
        f"evaluated={evaluated_count},dynamic_enabled={dynamic_enabled_count},models={models_text},"
        f"speed:min={min_speed:.3f}({lowest_speed_batch}),avg={avg_speed:.3f},max={max_speed:.3f}({highest_speed_batch}),"
        f"position:min={min_position:.3f}({lowest_position_batch}),avg={avg_position:.3f},max={max_position:.3f}({highest_position_batch}),"
        f"delta_speed:min={min_delta_speed:.3f}({lowest_delta_speed_batch}),avg={avg_delta_speed:.3f},max={max_delta_speed:.3f}({highest_delta_speed_batch}),"
        f"delta_position:min={min_delta_position:.3f}({lowest_delta_position_batch}),avg={avg_delta_position:.3f},max={max_delta_position:.3f}({highest_delta_position_batch}),"
        f"heading:min={min_heading:.3f}({lowest_heading_batch}),avg={avg_heading:.3f},max={max_heading:.3f}({highest_heading_batch}),"
        f"lateral_position:min={min_lateral_position:.3f}({lowest_lateral_position_batch}),avg={avg_lateral_position:.3f},max={max_lateral_position:.3f}({highest_lateral_position_batch}),"
        f"lateral_velocity:min={min_lateral_velocity:.3f}({lowest_lateral_velocity_batch}),avg={avg_lateral_velocity:.3f},max={max_lateral_velocity:.3f}({highest_lateral_velocity_batch}),"
        f"yaw_rate_final:min={min_yaw_rate_final:.3f}({lowest_yaw_rate_batch}),avg={avg_yaw_rate_final:.3f},max={max_yaw_rate_final:.3f}({highest_yaw_rate_batch}),"
        f"delta_heading:min={min_delta_heading:.3f}({lowest_delta_heading_batch}),avg={avg_delta_heading:.3f},max={max_delta_heading:.3f}({highest_delta_heading_batch}),"
        f"delta_lateral_position:min={min_delta_lateral_position:.3f}({lowest_delta_lateral_position_batch}),avg={avg_delta_lateral_position:.3f},max={max_delta_lateral_position:.3f}({highest_delta_lateral_position_batch}),"
        f"delta_lateral_velocity:min={min_delta_lateral_velocity:.3f}({lowest_delta_lateral_velocity_batch}),avg={avg_delta_lateral_velocity:.3f},max={max_delta_lateral_velocity:.3f}({highest_delta_lateral_velocity_batch}),"
        f"delta_yaw_rate:min={min_delta_yaw_rate:.3f}({lowest_delta_yaw_rate_batch}),avg={avg_delta_yaw_rate:.3f},max={max_delta_yaw_rate:.3f}({highest_delta_yaw_rate_batch}),"
        f"yaw_rate:max_abs={max_abs_yaw_rate:.3f}({highest_abs_yaw_rate_batch}),"
        f"lateral_velocity:max_abs={max_abs_lateral_velocity:.3f}({highest_abs_lateral_velocity_batch}),"
        f"accel:max_abs={max_abs_accel:.3f}({highest_abs_accel_batch}),"
        f"lateral_accel:max_abs={max_abs_lateral_accel:.3f}({highest_abs_lateral_accel_batch}),"
        f"yaw_accel:max_abs={max_abs_yaw_accel:.3f}({highest_abs_yaw_accel_batch}),"
        f"jerk:max_abs={max_abs_jerk:.3f}({highest_abs_jerk_batch}),"
        f"lateral_jerk:max_abs={max_abs_lateral_jerk:.3f}({highest_abs_lateral_jerk_batch}),"
        f"yaw_jerk:max_abs={max_abs_yaw_jerk:.3f}({highest_abs_yaw_jerk_batch}),"
        f"lateral_abs:max={max_abs_lateral_position:.3f}({highest_abs_lateral_position_batch}),"
        f"road_grade:min={min_road_grade:.3f}({lowest_road_grade_batch}),avg={avg_road_grade:.3f},max={max_road_grade:.3f}({highest_road_grade_batch}),"
        f"grade_force:max_abs={max_abs_grade_force:.3f}({highest_abs_grade_force_batch}),"
        f"control_input:manifests={control_command_manifest_count},steps={control_command_step_count_total},"
        f"overlap_steps={control_overlap_step_count_total},"
        f"overlap_ratio_avg={control_overlap_ratio_avg:.3f},"
        f"overlap_ratio_max={control_overlap_ratio_max:.3f}({highest_control_overlap_batch}),"
        f"steering_rate_avg={control_steering_rate_avg:.3f},"
        f"steering_rate_max={control_steering_rate_max:.3f}({highest_control_steering_rate_batch}),"
        f"throttle_rate_avg={control_throttle_rate_avg:.3f},"
        f"throttle_rate_max={control_throttle_rate_max:.3f}({highest_control_throttle_rate_batch}),"
        f"brake_rate_avg={control_brake_rate_avg:.3f},"
        f"brake_rate_max={control_brake_rate_max:.3f}({highest_control_brake_rate_batch}),"
        f"throttle_plus_brake_avg={control_throttle_plus_brake_avg:.3f},"
        f"throttle_plus_brake_max={control_throttle_plus_brake_max:.3f}"
        f"({highest_control_throttle_plus_brake_batch}),"
        f"speed_tracking:manifests={speed_tracking_manifest_count},"
        f"target_steps={speed_tracking_target_step_count_total},"
        f"error_min={min_speed_tracking_error:.3f}({lowest_speed_tracking_error_batch}),"
        f"error_avg={avg_speed_tracking_error:.3f},"
        f"error_max={max_speed_tracking_error:.3f}({highest_speed_tracking_error_batch}),"
        f"error_abs_avg={avg_abs_speed_tracking_error:.3f},"
        f"error_abs_max={max_abs_speed_tracking_error:.3f}"
        f"({highest_abs_speed_tracking_error_batch})"
    )


def _format_phase3_core_sim_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    status_counts = _as_non_negative_int_map(summary.get("status_counts", {}))
    status_counts_text = (
        ",".join(
            f"{key}:{status_counts[key]}"
            for key in sorted(status_counts.keys())
        )
        if status_counts
        else "n/a"
    )
    gate_result_counts = _as_non_negative_int_map(summary.get("gate_result_counts", {}))
    gate_result_counts_text = (
        ",".join(
            f"{key}:{gate_result_counts[key]}"
            for key in sorted(gate_result_counts.keys())
        )
        if gate_result_counts
        else "n/a"
    )
    try:
        gate_reason_count_total = int(summary.get("gate_reason_count_total", 0))
    except (TypeError, ValueError):
        gate_reason_count_total = 0
    try:
        require_success_enabled_count = int(summary.get("gate_require_success_enabled_count", 0))
    except (TypeError, ValueError):
        require_success_enabled_count = 0
    try:
        success_manifest_count = int(summary.get("success_manifest_count", 0))
    except (TypeError, ValueError):
        success_manifest_count = 0
    try:
        collision_manifest_count = int(summary.get("collision_manifest_count", 0))
    except (TypeError, ValueError):
        collision_manifest_count = 0
    try:
        timeout_manifest_count = int(summary.get("timeout_manifest_count", 0))
    except (TypeError, ValueError):
        timeout_manifest_count = 0

    def _fmt_ttc(value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "n/a"

    min_ttc_same_lane_text = _fmt_ttc(summary.get("min_ttc_same_lane_sec"))
    min_ttc_any_lane_text = _fmt_ttc(summary.get("min_ttc_any_lane_sec"))
    lowest_same_lane_batch = str(summary.get("lowest_same_lane_batch_id", "")).strip() or "n/a"
    lowest_any_lane_batch = str(summary.get("lowest_any_lane_batch_id", "")).strip() or "n/a"
    try:
        avoidance_enabled_manifest_count = int(summary.get("avoidance_enabled_manifest_count", 0))
    except (TypeError, ValueError):
        avoidance_enabled_manifest_count = 0
    try:
        avoidance_brake_event_count_total = int(summary.get("ego_avoidance_brake_event_count_total", 0))
    except (TypeError, ValueError):
        avoidance_brake_event_count_total = 0
    try:
        max_avoidance_brake = float(summary.get("max_ego_avoidance_applied_brake_mps2", 0.0))
    except (TypeError, ValueError):
        max_avoidance_brake = 0.0
    highest_avoidance_brake_batch = (
        str(summary.get("highest_ego_avoidance_applied_brake_batch_id", "")).strip() or "n/a"
    )
    try:
        avg_tire_friction = float(summary.get("avg_tire_friction_coeff", 0.0))
    except (TypeError, ValueError):
        avg_tire_friction = 0.0
    try:
        avg_surface_friction = float(summary.get("avg_surface_friction_scale", 0.0))
    except (TypeError, ValueError):
        avg_surface_friction = 0.0
    return (
        f"evaluated={evaluated_count},statuses={status_counts_text},gate_results={gate_result_counts_text},"
        f"gate_reasons_total={gate_reason_count_total},require_success_enabled={require_success_enabled_count},"
        f"success={success_manifest_count},collision={collision_manifest_count},timeout={timeout_manifest_count},"
        f"min_ttc_same_lane={min_ttc_same_lane_text}({lowest_same_lane_batch}),"
        f"min_ttc_any_lane={min_ttc_any_lane_text}({lowest_any_lane_batch}),"
        f"avoidance_enabled={avoidance_enabled_manifest_count},"
        f"avoidance_brake_events_total={avoidance_brake_event_count_total},"
        f"avoidance_brake_applied_max={max_avoidance_brake:.3f}({highest_avoidance_brake_batch}),"
        f"tire_friction_avg={avg_tire_friction:.3f},surface_friction_avg={avg_surface_friction:.3f}"
    )


def _format_phase3_core_sim_matrix_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    try:
        enabled_manifest_count = int(summary.get("enabled_manifest_count", 0))
    except (TypeError, ValueError):
        enabled_manifest_count = 0
    try:
        case_count_total = int(summary.get("case_count_total", 0))
    except (TypeError, ValueError):
        case_count_total = 0
    try:
        success_case_count_total = int(summary.get("success_case_count_total", 0))
    except (TypeError, ValueError):
        success_case_count_total = 0
    try:
        failed_case_count_total = int(summary.get("failed_case_count_total", 0))
    except (TypeError, ValueError):
        failed_case_count_total = 0
    try:
        all_cases_success_manifest_count = int(summary.get("all_cases_success_manifest_count", 0))
    except (TypeError, ValueError):
        all_cases_success_manifest_count = 0
    try:
        collision_case_count_total = int(summary.get("collision_case_count_total", 0))
    except (TypeError, ValueError):
        collision_case_count_total = 0
    try:
        timeout_case_count_total = int(summary.get("timeout_case_count_total", 0))
    except (TypeError, ValueError):
        timeout_case_count_total = 0
    status_counts = _as_non_negative_int_map(summary.get("status_counts", {}))
    status_counts_text = (
        ",".join(
            f"{key}:{status_counts[key]}"
            for key in sorted(status_counts.keys())
        )
        if status_counts
        else "n/a"
    )
    returncode_counts = _as_non_negative_int_map(summary.get("returncode_counts", {}))
    returncode_counts_text = (
        ",".join(
            f"{key}:{returncode_counts[key]}"
            for key in sorted(
                returncode_counts.keys(),
                key=lambda raw_key: (
                    0,
                    int(str(raw_key).strip()),
                )
                if str(raw_key).strip().lstrip("-").isdigit()
                else (
                    1,
                    str(raw_key).strip(),
                ),
            )
        )
        if returncode_counts
        else "n/a"
    )

    def _fmt_ttc(value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "n/a"

    min_ttc_same_lane_text = _fmt_ttc(summary.get("min_ttc_same_lane_sec_min"))
    min_ttc_any_lane_text = _fmt_ttc(summary.get("min_ttc_any_lane_sec_min"))
    lowest_same_lane_batch = str(summary.get("lowest_ttc_same_lane_batch_id", "")).strip() or "n/a"
    lowest_same_lane_run = str(summary.get("lowest_ttc_same_lane_run_id", "")).strip() or "n/a"
    lowest_any_lane_batch = str(summary.get("lowest_ttc_any_lane_batch_id", "")).strip() or "n/a"
    lowest_any_lane_run = str(summary.get("lowest_ttc_any_lane_run_id", "")).strip() or "n/a"
    return (
        f"evaluated={evaluated_count},enabled_manifests={enabled_manifest_count},"
        f"cases_total={case_count_total},success_cases_total={success_case_count_total},"
        f"failed_cases_total={failed_case_count_total},"
        f"all_cases_success_manifests={all_cases_success_manifest_count},"
        f"collision_cases_total={collision_case_count_total},timeout_cases_total={timeout_case_count_total},"
        f"statuses={status_counts_text},returncodes={returncode_counts_text},"
        f"min_ttc_same_lane={min_ttc_same_lane_text}({lowest_same_lane_batch}|{lowest_same_lane_run}),"
        f"min_ttc_any_lane={min_ttc_any_lane_text}({lowest_any_lane_batch}|{lowest_any_lane_run})"
    )


def _format_phase3_lane_risk_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    try:
        run_count_total = int(summary.get("lane_risk_summary_run_count_total", 0))
    except (TypeError, ValueError):
        run_count_total = 0
    gate_result_counts = _as_non_negative_int_map(summary.get("gate_result_counts", {}))
    gate_result_counts_text = (
        ",".join(
            f"{key}:{gate_result_counts[key]}"
            for key in sorted(gate_result_counts.keys())
        )
        if gate_result_counts
        else "n/a"
    )
    try:
        gate_reason_count_total = int(summary.get("gate_reason_count_total", 0))
    except (TypeError, ValueError):
        gate_reason_count_total = 0

    def _fmt_ttc(value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "n/a"

    min_ttc_same_lane_text = _fmt_ttc(summary.get("min_ttc_same_lane_sec"))
    min_ttc_adjacent_lane_text = _fmt_ttc(summary.get("min_ttc_adjacent_lane_sec"))
    min_ttc_any_lane_text = _fmt_ttc(summary.get("min_ttc_any_lane_sec"))
    lowest_same_lane_batch = str(summary.get("lowest_same_lane_batch_id", "")).strip() or "n/a"
    lowest_adjacent_lane_batch = str(summary.get("lowest_adjacent_lane_batch_id", "")).strip() or "n/a"
    lowest_any_lane_batch = str(summary.get("lowest_any_lane_batch_id", "")).strip() or "n/a"
    try:
        ttc_under_3s_same_lane_total = int(summary.get("ttc_under_3s_same_lane_total", 0))
    except (TypeError, ValueError):
        ttc_under_3s_same_lane_total = 0
    try:
        ttc_under_3s_adjacent_lane_total = int(summary.get("ttc_under_3s_adjacent_lane_total", 0))
    except (TypeError, ValueError):
        ttc_under_3s_adjacent_lane_total = 0
    try:
        same_lane_rows_total = int(summary.get("same_lane_rows_total", 0))
    except (TypeError, ValueError):
        same_lane_rows_total = 0
    try:
        adjacent_lane_rows_total = int(summary.get("adjacent_lane_rows_total", 0))
    except (TypeError, ValueError):
        adjacent_lane_rows_total = 0
    try:
        other_lane_rows_total = int(summary.get("other_lane_rows_total", 0))
    except (TypeError, ValueError):
        other_lane_rows_total = 0
    return (
        f"evaluated={evaluated_count},runs={run_count_total},"
        f"gate_results={gate_result_counts_text},"
        f"gate_reasons_total={gate_reason_count_total},"
        f"min_ttc_same_lane={min_ttc_same_lane_text}({lowest_same_lane_batch}),"
        f"min_ttc_adjacent_lane={min_ttc_adjacent_lane_text}({lowest_adjacent_lane_batch}),"
        f"min_ttc_any_lane={min_ttc_any_lane_text}({lowest_any_lane_batch}),"
        f"ttc_under_3s_same_lane_total={ttc_under_3s_same_lane_total},"
        f"ttc_under_3s_adjacent_lane_total={ttc_under_3s_adjacent_lane_total},"
        f"rows=same:{same_lane_rows_total},adjacent:{adjacent_lane_rows_total},other:{other_lane_rows_total}"
    )


def _format_phase3_dataset_traffic_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    gate_result_counts = _as_non_negative_int_map(summary.get("gate_result_counts", {}))
    gate_result_counts_text = (
        ",".join(
            f"{key}:{gate_result_counts[key]}"
            for key in sorted(gate_result_counts.keys())
        )
        if gate_result_counts
        else "n/a"
    )
    try:
        gate_reason_count_total = int(summary.get("gate_reason_count_total", 0))
    except (TypeError, ValueError):
        gate_reason_count_total = 0
    try:
        run_summary_count_total = int(summary.get("run_summary_count_total", 0))
    except (TypeError, ValueError):
        run_summary_count_total = 0
    run_status_counts = _as_non_negative_int_map(summary.get("run_status_counts", {}))
    run_status_counts_text = (
        ",".join(
            f"{key}:{run_status_counts[key]}"
            for key in sorted(run_status_counts.keys())
        )
        if run_status_counts
        else "n/a"
    )
    try:
        profile_unique_count = int(summary.get("traffic_profile_unique_count", 0))
    except (TypeError, ValueError):
        profile_unique_count = 0
    profile_ids_text = _format_string_list(summary.get("traffic_profile_ids"))
    try:
        profile_count_avg = float(summary.get("traffic_profile_count_avg", 0.0))
    except (TypeError, ValueError):
        profile_count_avg = 0.0
    try:
        max_profile_count = int(summary.get("max_traffic_profile_count", 0))
    except (TypeError, ValueError):
        max_profile_count = 0
    max_profile_batch = str(summary.get("highest_traffic_profile_batch_id", "")).strip() or "n/a"
    try:
        actor_pattern_unique_count = int(summary.get("traffic_actor_pattern_unique_count", 0))
    except (TypeError, ValueError):
        actor_pattern_unique_count = 0
    actor_pattern_ids_text = _format_string_list(summary.get("traffic_actor_pattern_ids"))
    try:
        actor_pattern_count_avg = float(summary.get("traffic_actor_pattern_count_avg", 0.0))
    except (TypeError, ValueError):
        actor_pattern_count_avg = 0.0
    try:
        max_actor_pattern_count = int(summary.get("max_traffic_actor_pattern_count", 0))
    except (TypeError, ValueError):
        max_actor_pattern_count = 0
    max_actor_pattern_batch = str(summary.get("highest_traffic_actor_pattern_batch_id", "")).strip() or "n/a"
    try:
        npc_count_avg_avg = float(summary.get("traffic_npc_count_avg_avg", 0.0))
    except (TypeError, ValueError):
        npc_count_avg_avg = 0.0
    try:
        npc_count_avg_max = float(summary.get("traffic_npc_count_avg_max", 0.0))
    except (TypeError, ValueError):
        npc_count_avg_max = 0.0
    npc_count_avg_max_batch = str(summary.get("highest_traffic_npc_avg_batch_id", "")).strip() or "n/a"
    try:
        npc_count_max_max = int(summary.get("traffic_npc_count_max_max", 0))
    except (TypeError, ValueError):
        npc_count_max_max = 0
    npc_count_max_max_batch = str(summary.get("highest_traffic_npc_max_batch_id", "")).strip() or "n/a"
    try:
        lane_indices_unique_count = int(summary.get("traffic_lane_indices_unique_count", 0))
    except (TypeError, ValueError):
        lane_indices_unique_count = 0
    try:
        lane_index_unique_count_avg = float(summary.get("traffic_lane_index_unique_count_avg", 0.0))
    except (TypeError, ValueError):
        lane_index_unique_count_avg = 0.0
    lane_indices_raw = summary.get("traffic_lane_indices", [])
    lane_indices: list[int] = []
    if isinstance(lane_indices_raw, list):
        for lane_index_raw in lane_indices_raw:
            try:
                lane_index = int(lane_index_raw)
            except (TypeError, ValueError):
                continue
            lane_indices.append(lane_index)
    lane_indices_text = ",".join(str(item) for item in sorted(set(lane_indices))) or "n/a"
    try:
        dataset_manifest_counts_rows_total = int(summary.get("dataset_manifest_counts_rows_total", 0))
    except (TypeError, ValueError):
        dataset_manifest_counts_rows_total = 0
    try:
        dataset_manifest_run_summary_count_total = int(summary.get("dataset_manifest_run_summary_count_total", 0))
    except (TypeError, ValueError):
        dataset_manifest_run_summary_count_total = 0
    try:
        dataset_manifest_release_summary_count_total = int(
            summary.get("dataset_manifest_release_summary_count_total", 0)
        )
    except (TypeError, ValueError):
        dataset_manifest_release_summary_count_total = 0
    dataset_manifest_versions_text = _format_string_list(summary.get("dataset_manifest_versions"))
    return (
        f"evaluated={evaluated_count},gate_results={gate_result_counts_text},"
        f"gate_reasons_total={gate_reason_count_total},"
        f"runs_total={run_summary_count_total},run_statuses={run_status_counts_text},"
        f"profiles=unique:{profile_unique_count},ids:{profile_ids_text},avg:{profile_count_avg:.3f},"
        f"max:{max_profile_count}({max_profile_batch}),"
        f"actor_patterns=unique:{actor_pattern_unique_count},ids:{actor_pattern_ids_text},avg:{actor_pattern_count_avg:.3f},"
        f"max:{max_actor_pattern_count}({max_actor_pattern_batch}),"
        f"npc_avg=avg:{npc_count_avg_avg:.3f},max:{npc_count_avg_max:.3f}({npc_count_avg_max_batch}),"
        f"npc_max:{npc_count_max_max}({npc_count_max_max_batch}),"
        f"lane_indices=unique:{lane_indices_unique_count},avg_unique_per_manifest:{lane_index_unique_count_avg:.3f},"
        f"indices:{lane_indices_text},"
        f"dataset_manifest=counts_rows_total:{dataset_manifest_counts_rows_total},"
        f"run_summaries_total:{dataset_manifest_run_summary_count_total},"
        f"release_summaries_total:{dataset_manifest_release_summary_count_total},"
        f"versions:{dataset_manifest_versions_text}"
    )


def _format_phase2_map_routing_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    status_counts = _as_non_negative_int_map(summary.get("status_counts", {}))
    status_counts_text = (
        ",".join(
            f"{key}:{status_counts[key]}"
            for key in sorted(status_counts.keys())
        )
        if status_counts
        else "n/a"
    )
    try:
        error_count_total = int(summary.get("error_count_total", 0))
    except (TypeError, ValueError):
        error_count_total = 0
    try:
        warning_count_total = int(summary.get("warning_count_total", 0))
    except (TypeError, ValueError):
        warning_count_total = 0
    try:
        semantic_warning_count_total = int(summary.get("semantic_warning_count_total", 0))
    except (TypeError, ValueError):
        semantic_warning_count_total = 0
    try:
        unreachable_lane_count_total = int(summary.get("unreachable_lane_count_total", 0))
    except (TypeError, ValueError):
        unreachable_lane_count_total = 0
    try:
        non_reciprocal_link_count_total = int(summary.get("non_reciprocal_link_count_total", 0))
    except (TypeError, ValueError):
        non_reciprocal_link_count_total = 0
    try:
        continuity_gap_warning_count_total = int(summary.get("continuity_gap_warning_count_total", 0))
    except (TypeError, ValueError):
        continuity_gap_warning_count_total = 0
    try:
        max_unreachable_lane_count = int(summary.get("max_unreachable_lane_count", 0))
    except (TypeError, ValueError):
        max_unreachable_lane_count = 0
    highest_unreachable_batch_id = str(summary.get("highest_unreachable_batch_id", "")).strip() or "n/a"
    try:
        max_non_reciprocal_link_count = int(summary.get("max_non_reciprocal_link_count", 0))
    except (TypeError, ValueError):
        max_non_reciprocal_link_count = 0
    highest_non_reciprocal_batch_id = str(summary.get("highest_non_reciprocal_batch_id", "")).strip() or "n/a"
    try:
        max_continuity_gap_warning_count = int(summary.get("max_continuity_gap_warning_count", 0))
    except (TypeError, ValueError):
        max_continuity_gap_warning_count = 0
    highest_continuity_gap_batch_id = str(summary.get("highest_continuity_gap_batch_id", "")).strip() or "n/a"
    try:
        route_evaluated_count = int(summary.get("route_evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        route_evaluated_count = 0
    route_status_counts = _as_non_negative_int_map(summary.get("route_status_counts", {}))
    route_status_counts_text = (
        ",".join(
            f"{key}:{route_status_counts[key]}"
            for key in sorted(route_status_counts.keys())
        )
        if route_status_counts
        else "n/a"
    )
    try:
        route_lane_count_total = int(summary.get("route_lane_count_total", 0))
    except (TypeError, ValueError):
        route_lane_count_total = 0
    try:
        route_hop_count_total = int(summary.get("route_hop_count_total", 0))
    except (TypeError, ValueError):
        route_hop_count_total = 0
    try:
        route_total_length_m_total = float(summary.get("route_total_length_m_total", 0.0))
    except (TypeError, ValueError):
        route_total_length_m_total = 0.0
    try:
        route_total_length_m_avg = float(summary.get("route_total_length_m_avg", 0.0))
    except (TypeError, ValueError):
        route_total_length_m_avg = 0.0
    try:
        route_segment_count_total = int(summary.get("route_segment_count_total", 0))
    except (TypeError, ValueError):
        route_segment_count_total = 0
    try:
        route_segment_count_avg = float(summary.get("route_segment_count_avg", 0.0))
    except (TypeError, ValueError):
        route_segment_count_avg = 0.0
    try:
        route_with_via_manifest_count = int(summary.get("route_with_via_manifest_count", 0))
    except (TypeError, ValueError):
        route_with_via_manifest_count = 0
    try:
        route_via_lane_count_total = int(summary.get("route_via_lane_count_total", 0))
    except (TypeError, ValueError):
        route_via_lane_count_total = 0
    try:
        route_via_lane_count_avg = float(summary.get("route_via_lane_count_avg", 0.0))
    except (TypeError, ValueError):
        route_via_lane_count_avg = 0.0
    try:
        max_route_lane_count = int(summary.get("max_route_lane_count", 0))
    except (TypeError, ValueError):
        max_route_lane_count = 0
    highest_route_lane_count_batch_id = str(summary.get("highest_route_lane_count_batch_id", "")).strip() or "n/a"
    try:
        max_route_hop_count = int(summary.get("max_route_hop_count", 0))
    except (TypeError, ValueError):
        max_route_hop_count = 0
    highest_route_hop_count_batch_id = str(summary.get("highest_route_hop_count_batch_id", "")).strip() or "n/a"
    try:
        max_route_segment_count = int(summary.get("max_route_segment_count", 0))
    except (TypeError, ValueError):
        max_route_segment_count = 0
    highest_route_segment_count_batch_id = str(summary.get("highest_route_segment_count_batch_id", "")).strip() or "n/a"
    try:
        max_route_via_lane_count = int(summary.get("max_route_via_lane_count", 0))
    except (TypeError, ValueError):
        max_route_via_lane_count = 0
    highest_route_via_lane_count_batch_id = (
        str(summary.get("highest_route_via_lane_count_batch_id", "")).strip() or "n/a"
    )
    try:
        max_route_total_length_m = float(summary.get("max_route_total_length_m", 0.0))
    except (TypeError, ValueError):
        max_route_total_length_m = 0.0
    highest_route_total_length_batch_id = str(summary.get("highest_route_total_length_batch_id", "")).strip() or "n/a"
    return (
        f"evaluated={evaluated_count},statuses={status_counts_text},"
        f"errors_total={error_count_total},warnings_total={warning_count_total},"
        f"semantic_warnings_total={semantic_warning_count_total},"
        f"unreachable_total={unreachable_lane_count_total},non_reciprocal_total={non_reciprocal_link_count_total},"
        f"continuity_gap_total={continuity_gap_warning_count_total},"
        f"max_unreachable={max_unreachable_lane_count}({highest_unreachable_batch_id}),"
        f"max_non_reciprocal={max_non_reciprocal_link_count}({highest_non_reciprocal_batch_id}),"
        f"max_continuity_gap={max_continuity_gap_warning_count}({highest_continuity_gap_batch_id}),"
        f"route_evaluated={route_evaluated_count},route_statuses={route_status_counts_text},"
        f"route_lane_total={route_lane_count_total},route_hop_total={route_hop_count_total},"
        f"route_length_total_m={route_total_length_m_total:.3f},route_length_avg_m={route_total_length_m_avg:.3f},"
        f"route_segment_total={route_segment_count_total},route_segment_avg={route_segment_count_avg:.3f},"
        f"route_with_via={route_with_via_manifest_count},route_via_lane_total={route_via_lane_count_total},"
        f"route_via_lane_avg={route_via_lane_count_avg:.3f},"
        f"max_route_lane={max_route_lane_count}({highest_route_lane_count_batch_id}),"
        f"max_route_hop={max_route_hop_count}({highest_route_hop_count_batch_id}),"
        f"max_route_segment={max_route_segment_count}({highest_route_segment_count_batch_id}),"
        f"max_route_via_lane={max_route_via_lane_count}({highest_route_via_lane_count_batch_id}),"
        f"max_route_length_m={max_route_total_length_m:.3f}({highest_route_total_length_batch_id})"
    )


def _format_phase2_sensor_fidelity_summary(summary: dict[str, Any]) -> str:
    def _format_counts(value: Any) -> str:
        parsed = _as_non_negative_int_map(value)
        if not parsed:
            return "n/a"
        return ",".join(f"{key}:{parsed[key]}" for key in sorted(parsed.keys()))

    return format_phase2_sensor_fidelity_summary(
        summary,
        format_counts=_format_counts,
        spaced=False,
    )


def _format_phase2_log_replay_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    status_counts = _as_non_negative_int_map(summary.get("status_counts", {}))
    status_counts_text = (
        ",".join(
            f"{key}:{status_counts[key]}"
            for key in sorted(status_counts.keys())
        )
        if status_counts
        else "n/a"
    )
    run_status_counts = _as_non_negative_int_map(summary.get("run_status_counts", {}))
    run_status_counts_text = (
        ",".join(
            f"{key}:{run_status_counts[key]}"
            for key in sorted(run_status_counts.keys())
        )
        if run_status_counts
        else "n/a"
    )
    run_source_counts = _as_non_negative_int_map(summary.get("run_source_counts", {}))
    run_source_counts_text = (
        ",".join(
            f"{key}:{run_source_counts[key]}"
            for key in sorted(run_source_counts.keys())
        )
        if run_source_counts
        else "n/a"
    )
    try:
        manifest_present_count = int(summary.get("manifest_present_count", 0))
    except (TypeError, ValueError):
        manifest_present_count = 0
    try:
        summary_present_count = int(summary.get("summary_present_count", 0))
    except (TypeError, ValueError):
        summary_present_count = 0
    try:
        missing_manifest_count = int(summary.get("missing_manifest_count", 0))
    except (TypeError, ValueError):
        missing_manifest_count = 0
    try:
        missing_summary_count = int(summary.get("missing_summary_count", 0))
    except (TypeError, ValueError):
        missing_summary_count = 0
    try:
        log_id_present_count = int(summary.get("log_id_present_count", 0))
    except (TypeError, ValueError):
        log_id_present_count = 0
    try:
        map_id_present_count = int(summary.get("map_id_present_count", 0))
    except (TypeError, ValueError):
        map_id_present_count = 0
    return (
        f"evaluated={evaluated_count},statuses={status_counts_text},"
        f"run_statuses={run_status_counts_text},run_sources={run_source_counts_text},"
        f"manifest_present={manifest_present_count},summary_present={summary_present_count},"
        f"missing_manifest={missing_manifest_count},missing_summary={missing_summary_count},"
        f"log_id_present={log_id_present_count},map_id_present={map_id_present_count}"
    )


def _format_runtime_native_smoke_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    module_summaries = summary.get("module_summaries", {})
    if not isinstance(module_summaries, dict) or not module_summaries:
        return "n/a"
    try:
        evaluated_count = int(summary.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    try:
        all_pass_count = int(summary.get("all_modules_pass_manifest_count", 0))
    except (TypeError, ValueError):
        all_pass_count = 0
    all_status_counts = _as_non_negative_int_map(summary.get("all_modules_status_counts", {}))
    all_status_counts_text = (
        ",".join(
            f"{key}:{all_status_counts[key]}"
            for key in sorted(all_status_counts.keys())
        )
        if all_status_counts
        else "n/a"
    )
    module_parts: list[str] = []
    for module_name in ("object_sim", "log_sim", "map_toolset"):
        module_payload = module_summaries.get(module_name, {})
        if not isinstance(module_payload, dict):
            continue
        try:
            module_evaluated_count = int(module_payload.get("evaluated_manifest_count", 0))
        except (TypeError, ValueError):
            module_evaluated_count = 0
        module_status_counts = _as_non_negative_int_map(module_payload.get("status_counts", {}))
        module_status_counts_text = (
            ",".join(
                f"{key}:{module_status_counts[key]}"
                for key in sorted(module_status_counts.keys())
            )
            if module_status_counts
            else "n/a"
        )
        module_parts.append(
            f"{module_name}(evaluated={module_evaluated_count},statuses={module_status_counts_text})"
        )
    return (
        f"evaluated={evaluated_count},all_statuses={all_status_counts_text},"
        f"all_pass={all_pass_count},modules={';'.join(module_parts) or 'n/a'}"
    )


def _format_runtime_native_summary_compare_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        artifacts_with_diffs_count = int(summary.get("artifacts_with_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_with_diffs_count = 0
    try:
        artifacts_without_diffs_count = int(summary.get("artifacts_without_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_without_diffs_count = 0
    try:
        versions_total = int(summary.get("versions_total", 0))
    except (TypeError, ValueError):
        versions_total = 0
    try:
        comparisons_total = int(summary.get("comparisons_total", 0))
    except (TypeError, ValueError):
        comparisons_total = 0
    try:
        versions_with_diffs_total = int(summary.get("versions_with_diffs_total", 0))
    except (TypeError, ValueError):
        versions_with_diffs_total = 0
    label_pair_counts = _as_non_negative_int_map(summary.get("label_pair_counts", {}))
    label_pair_counts_text = (
        ",".join(
            f"{key}:{label_pair_counts[key]}"
            for key in sorted(label_pair_counts.keys())
        )
        if label_pair_counts
        else "n/a"
    )
    field_diff_counts = _as_non_negative_int_map(summary.get("field_diff_counts", {}))
    field_diff_counts_text = (
        ",".join(
            f"{key}:{field_diff_counts[key]}"
            for key in sorted(field_diff_counts.keys())
        )
        if field_diff_counts
        else "n/a"
    )
    versions_with_diffs_counts = _as_non_negative_int_map(summary.get("versions_with_diffs_counts", {}))
    versions_with_diffs_counts_text = (
        ",".join(
            f"{key}:{versions_with_diffs_counts[key]}"
            for key in sorted(versions_with_diffs_counts.keys())
        )
        if versions_with_diffs_counts
        else "n/a"
    )
    return (
        f"artifacts={artifact_count},with_diffs={artifacts_with_diffs_count},"
        f"without_diffs={artifacts_without_diffs_count},versions_total={versions_total},"
        f"comparisons_total={comparisons_total},versions_with_diffs_total={versions_with_diffs_total},"
        f"label_pairs={label_pair_counts_text},field_diff_counts={field_diff_counts_text},"
        f"versions_with_diffs={versions_with_diffs_counts_text}"
    )


def _format_runtime_evidence_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        record_count = int(summary.get("record_count", 0))
    except (TypeError, ValueError):
        record_count = 0
    try:
        validated_count = int(summary.get("validated_count", 0))
    except (TypeError, ValueError):
        validated_count = 0
    try:
        failed_count = int(summary.get("failed_count", 0))
    except (TypeError, ValueError):
        failed_count = 0
    try:
        availability_true_count = int(summary.get("availability_true_count", 0))
    except (TypeError, ValueError):
        availability_true_count = 0
    try:
        availability_false_count = int(summary.get("availability_false_count", 0))
    except (TypeError, ValueError):
        availability_false_count = 0
    try:
        availability_unknown_count = int(summary.get("availability_unknown_count", 0))
    except (TypeError, ValueError):
        availability_unknown_count = 0
    try:
        probe_checked_count = int(summary.get("probe_checked_count", 0))
    except (TypeError, ValueError):
        probe_checked_count = 0
    try:
        probe_executed_count = int(summary.get("probe_executed_count", 0))
    except (TypeError, ValueError):
        probe_executed_count = 0
    try:
        runtime_bin_missing_count = int(summary.get("runtime_bin_missing_count", 0))
    except (TypeError, ValueError):
        runtime_bin_missing_count = 0
    try:
        provenance_complete_count = int(summary.get("provenance_complete_count", 0))
    except (TypeError, ValueError):
        provenance_complete_count = 0
    try:
        provenance_missing_count = int(summary.get("provenance_missing_count", 0))
    except (TypeError, ValueError):
        provenance_missing_count = 0
    runtime_counts = summary.get("runtime_counts", {})
    runtime_counts_text = (
        ",".join(
            f"{runtime}:{count}"
            for runtime, count in sorted(runtime_counts.items())
            if str(runtime).strip()
        )
        if isinstance(runtime_counts, dict)
        else ""
    ) or "n/a"
    status_counts = summary.get("status_counts", {})
    status_counts_text = (
        ",".join(
            f"{status}:{count}"
            for status, count in sorted(status_counts.items())
            if str(status).strip()
        )
        if isinstance(status_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"artifacts={artifact_count},records={record_count},"
        f"validated={validated_count},failed={failed_count},"
        f"runtimes={runtime_counts_text},statuses={status_counts_text},"
        f"availability=true:{availability_true_count},false:{availability_false_count},"
        f"unknown:{availability_unknown_count},"
        f"probe_checked={probe_checked_count},probe_executed={probe_executed_count},"
        f"runtime_bin_missing={runtime_bin_missing_count},"
        f"provenance_complete={provenance_complete_count},provenance_missing={provenance_missing_count}"
    )


def _format_runtime_evidence_probe_args_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        effective_count = int(summary.get("probe_args_effective_count", 0))
    except (TypeError, ValueError):
        effective_count = 0
    try:
        requested_count = int(summary.get("probe_args_requested_count", 0))
    except (TypeError, ValueError):
        requested_count = 0
    try:
        flag_present_count = int(summary.get("probe_flag_present_count", 0))
    except (TypeError, ValueError):
        flag_present_count = 0
    try:
        flag_requested_present_count = int(summary.get("probe_flag_requested_present_count", 0))
    except (TypeError, ValueError):
        flag_requested_present_count = 0
    try:
        policy_enable_true_count = int(summary.get("probe_policy_enable_true_count", 0))
    except (TypeError, ValueError):
        policy_enable_true_count = 0
    try:
        policy_execute_true_count = int(summary.get("probe_policy_execute_true_count", 0))
    except (TypeError, ValueError):
        policy_execute_true_count = 0
    try:
        policy_require_availability_true_count = int(summary.get("probe_policy_require_availability_true_count", 0))
    except (TypeError, ValueError):
        policy_require_availability_true_count = 0
    try:
        policy_flag_input_present_count = int(summary.get("probe_policy_flag_input_present_count", 0))
    except (TypeError, ValueError):
        policy_flag_input_present_count = 0
    try:
        policy_args_shlex_input_present_count = int(summary.get("probe_policy_args_shlex_input_present_count", 0))
    except (TypeError, ValueError):
        policy_args_shlex_input_present_count = 0
    source_counts = summary.get("probe_args_source_counts", {})
    source_counts_text = (
        ",".join(
            f"{source}:{count}"
            for source, count in sorted(source_counts.items())
            if str(source).strip()
        )
        if isinstance(source_counts, dict)
        else ""
    ) or "n/a"
    requested_source_counts = summary.get("probe_args_requested_source_counts", {})
    requested_source_counts_text = (
        ",".join(
            f"{source}:{count}"
            for source, count in sorted(requested_source_counts.items())
            if str(source).strip()
        )
        if isinstance(requested_source_counts, dict)
        else ""
    ) or "n/a"
    value_counts = summary.get("probe_arg_value_counts", {})
    value_counts_text = (
        ",".join(
            f"{value}:{count}"
            for value, count in sorted(value_counts.items())
            if str(value).strip()
        )
        if isinstance(value_counts, dict)
        else ""
    ) or "n/a"
    requested_value_counts = summary.get("probe_arg_requested_value_counts", {})
    requested_value_counts_text = (
        ",".join(
            f"{value}:{count}"
            for value, count in sorted(requested_value_counts.items())
            if str(value).strip()
        )
        if isinstance(requested_value_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"effective={effective_count},requested={requested_count},"
        f"sources={source_counts_text},requested_sources={requested_source_counts_text},"
        f"arg_values={value_counts_text},requested_arg_values={requested_value_counts_text},"
        f"flags=effective:{flag_present_count},requested:{flag_requested_present_count},"
        "policy="
        f"enable:{policy_enable_true_count},execute:{policy_execute_true_count},"
        f"require_availability:{policy_require_availability_true_count},"
        f"flag_input:{policy_flag_input_present_count},"
        f"args_shlex_input:{policy_args_shlex_input_present_count}"
    )


def _format_runtime_evidence_scenario_contract_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(summary.get("scenario_contract_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        ready_true_count = int(summary.get("scenario_runtime_ready_true_count", 0))
    except (TypeError, ValueError):
        ready_true_count = 0
    try:
        ready_false_count = int(summary.get("scenario_runtime_ready_false_count", 0))
    except (TypeError, ValueError):
        ready_false_count = 0
    try:
        ready_unknown_count = int(summary.get("scenario_runtime_ready_unknown_count", 0))
    except (TypeError, ValueError):
        ready_unknown_count = 0
    try:
        actor_total = int(summary.get("scenario_actor_count_total", 0))
    except (TypeError, ValueError):
        actor_total = 0
    try:
        sensor_stream_total = int(summary.get("scenario_sensor_stream_count_total", 0))
    except (TypeError, ValueError):
        sensor_stream_total = 0
    try:
        step_total = int(summary.get("scenario_executed_step_count_total", 0))
    except (TypeError, ValueError):
        step_total = 0
    try:
        sim_duration_sec_total = float(summary.get("scenario_sim_duration_sec_total", 0.0))
    except (TypeError, ValueError):
        sim_duration_sec_total = 0.0
    status_counts = summary.get("scenario_contract_status_counts", {})
    status_counts_text = (
        ",".join(
            f"{status}:{count}"
            for status, count in sorted(status_counts.items())
            if str(status).strip()
        )
        if isinstance(status_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"checked={checked_count},ready=true:{ready_true_count},false:{ready_false_count},"
        f"unknown:{ready_unknown_count},statuses={status_counts_text},"
        f"actor_total={actor_total},sensor_stream_total={sensor_stream_total},"
        f"step_total={step_total},sim_duration_sec_total={sim_duration_sec_total:.3f}"
    )


def _format_runtime_evidence_interop_contract_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(summary.get("interop_contract_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        ready_true_count = int(summary.get("interop_runtime_ready_true_count", 0))
    except (TypeError, ValueError):
        ready_true_count = 0
    try:
        ready_false_count = int(summary.get("interop_runtime_ready_false_count", 0))
    except (TypeError, ValueError):
        ready_false_count = 0
    try:
        ready_unknown_count = int(summary.get("interop_runtime_ready_unknown_count", 0))
    except (TypeError, ValueError):
        ready_unknown_count = 0
    try:
        imported_actor_total = int(summary.get("interop_imported_actor_count_total", 0))
    except (TypeError, ValueError):
        imported_actor_total = 0
    try:
        xosc_entity_total = int(summary.get("interop_xosc_entity_count_total", 0))
    except (TypeError, ValueError):
        xosc_entity_total = 0
    try:
        xodr_road_total = int(summary.get("interop_xodr_road_count_total", 0))
    except (TypeError, ValueError):
        xodr_road_total = 0
    try:
        step_total = int(summary.get("interop_executed_step_count_total", 0))
    except (TypeError, ValueError):
        step_total = 0
    try:
        sim_duration_sec_total = float(summary.get("interop_sim_duration_sec_total", 0.0))
    except (TypeError, ValueError):
        sim_duration_sec_total = 0.0
    status_counts = summary.get("interop_contract_status_counts", {})
    status_counts_text = (
        ",".join(
            f"{status}:{count}"
            for status, count in sorted(status_counts.items())
            if str(status).strip()
        )
        if isinstance(status_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"checked={checked_count},ready=true:{ready_true_count},false:{ready_false_count},"
        f"unknown:{ready_unknown_count},statuses={status_counts_text},"
        f"imported_actor_total={imported_actor_total},xosc_entity_total={xosc_entity_total},"
        f"xodr_road_total={xodr_road_total},step_total={step_total},"
        f"sim_duration_sec_total={sim_duration_sec_total:.3f}"
    )


def _format_runtime_evidence_interop_export_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(summary.get("interop_export_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        actor_manifest_total = int(summary.get("interop_export_actor_count_manifest_total", 0))
    except (TypeError, ValueError):
        actor_manifest_total = 0
    try:
        sensor_stream_manifest_total = int(summary.get("interop_export_sensor_stream_count_manifest_total", 0))
    except (TypeError, ValueError):
        sensor_stream_manifest_total = 0
    try:
        xosc_entity_total = int(summary.get("interop_export_xosc_entity_count_total", 0))
    except (TypeError, ValueError):
        xosc_entity_total = 0
    try:
        xodr_road_total = int(summary.get("interop_export_xodr_road_count_total", 0))
    except (TypeError, ValueError):
        xodr_road_total = 0
    try:
        generated_road_length_m_total = float(summary.get("interop_export_generated_road_length_m_total", 0.0))
    except (TypeError, ValueError):
        generated_road_length_m_total = 0.0
    status_counts = summary.get("interop_export_status_counts", {})
    status_counts_text = (
        ",".join(
            f"{status}:{count}"
            for status, count in sorted(status_counts.items())
            if str(status).strip()
        )
        if isinstance(status_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"checked={checked_count},statuses={status_counts_text},"
        f"actor_manifest_total={actor_manifest_total},"
        f"sensor_stream_manifest_total={sensor_stream_manifest_total},"
        f"xosc_entity_total={xosc_entity_total},xodr_road_total={xodr_road_total},"
        f"generated_road_length_m_total={generated_road_length_m_total:.3f}"
    )


def _format_runtime_evidence_interop_import_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(summary.get("interop_import_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        manifest_consistent_true_count = int(summary.get("interop_import_manifest_consistent_true_count", 0))
    except (TypeError, ValueError):
        manifest_consistent_true_count = 0
    try:
        manifest_consistent_false_count = int(summary.get("interop_import_manifest_consistent_false_count", 0))
    except (TypeError, ValueError):
        manifest_consistent_false_count = 0
    try:
        manifest_consistent_unknown_count = int(summary.get("interop_import_manifest_consistent_unknown_count", 0))
    except (TypeError, ValueError):
        manifest_consistent_unknown_count = 0
    try:
        actor_manifest_total = int(summary.get("interop_import_actor_count_manifest_total", 0))
    except (TypeError, ValueError):
        actor_manifest_total = 0
    try:
        xosc_entity_total = int(summary.get("interop_import_xosc_entity_count_total", 0))
    except (TypeError, ValueError):
        xosc_entity_total = 0
    try:
        xodr_road_total = int(summary.get("interop_import_xodr_road_count_total", 0))
    except (TypeError, ValueError):
        xodr_road_total = 0
    try:
        xodr_total_road_length_m_total = float(summary.get("interop_import_xodr_total_road_length_m_total", 0.0))
    except (TypeError, ValueError):
        xodr_total_road_length_m_total = 0.0
    status_counts = summary.get("interop_import_status_counts", {})
    status_counts_text = (
        ",".join(
            f"{status}:{count}"
            for status, count in sorted(status_counts.items())
            if str(status).strip()
        )
        if isinstance(status_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"checked={checked_count},statuses={status_counts_text},"
        "manifest_consistent=true:"
        f"{manifest_consistent_true_count},false:{manifest_consistent_false_count},"
        f"unknown:{manifest_consistent_unknown_count},"
        f"actor_manifest_total={actor_manifest_total},"
        f"xosc_entity_total={xosc_entity_total},xodr_road_total={xodr_road_total},"
        f"xodr_total_road_length_m_total={xodr_total_road_length_m_total:.3f}"
    )


def _format_runtime_evidence_interop_import_modes(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    manifest_counts = summary.get("interop_import_manifest_consistency_mode_counts", {})
    manifest_counts_text = (
        ",".join(
            f"{mode}:{count}"
            for mode, count in sorted(manifest_counts.items())
            if str(mode).strip()
        )
        if isinstance(manifest_counts, dict)
        else ""
    ) or "n/a"
    export_counts = summary.get("interop_import_export_consistency_mode_counts", {})
    export_counts_text = (
        ",".join(
            f"{mode}:{count}"
            for mode, count in sorted(export_counts.items())
            if str(mode).strip()
        )
        if isinstance(export_counts, dict)
        else ""
    ) or "n/a"
    try:
        require_manifest_true_count = int(
            summary.get("interop_import_require_manifest_consistency_input_true_count", 0)
        )
    except (TypeError, ValueError):
        require_manifest_true_count = 0
    try:
        require_export_true_count = int(
            summary.get("interop_import_require_export_consistency_input_true_count", 0)
        )
    except (TypeError, ValueError):
        require_export_true_count = 0
    if (
        manifest_counts_text == "n/a"
        and export_counts_text == "n/a"
        and require_manifest_true_count <= 0
        and require_export_true_count <= 0
    ):
        return "n/a"
    return (
        f"manifest={manifest_counts_text},export={export_counts_text},"
        f"require_inputs=manifest:{require_manifest_true_count},export:{require_export_true_count}"
    )


def _format_runtime_evidence_scene_result_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(summary.get("scene_result_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        ready_true_count = int(summary.get("scene_result_runtime_ready_true_count", 0))
    except (TypeError, ValueError):
        ready_true_count = 0
    try:
        ready_false_count = int(summary.get("scene_result_runtime_ready_false_count", 0))
    except (TypeError, ValueError):
        ready_false_count = 0
    try:
        ready_unknown_count = int(summary.get("scene_result_runtime_ready_unknown_count", 0))
    except (TypeError, ValueError):
        ready_unknown_count = 0
    try:
        actor_total = int(summary.get("scene_result_actor_count_total", 0))
    except (TypeError, ValueError):
        actor_total = 0
    try:
        sensor_stream_total = int(summary.get("scene_result_sensor_stream_count_total", 0))
    except (TypeError, ValueError):
        sensor_stream_total = 0
    try:
        step_total = int(summary.get("scene_result_executed_step_count_total", 0))
    except (TypeError, ValueError):
        step_total = 0
    try:
        sim_duration_sec_total = float(summary.get("scene_result_sim_duration_sec_total", 0.0))
    except (TypeError, ValueError):
        sim_duration_sec_total = 0.0
    try:
        coverage_ratio_avg = float(summary.get("scene_result_coverage_ratio_avg", 0.0))
    except (TypeError, ValueError):
        coverage_ratio_avg = 0.0
    try:
        coverage_ratio_samples = int(summary.get("scene_result_coverage_ratio_sample_count", 0))
    except (TypeError, ValueError):
        coverage_ratio_samples = 0
    try:
        ego_travel_distance_m_total = float(summary.get("scene_result_ego_travel_distance_m_total", 0.0))
    except (TypeError, ValueError):
        ego_travel_distance_m_total = 0.0
    status_counts = summary.get("scene_result_status_counts", {})
    status_counts_text = (
        ",".join(
            f"{status}:{count}"
            for status, count in sorted(status_counts.items())
            if str(status).strip()
        )
        if isinstance(status_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"checked={checked_count},ready=true:{ready_true_count},false:{ready_false_count},"
        f"unknown:{ready_unknown_count},statuses={status_counts_text},"
        f"actor_total={actor_total},sensor_stream_total={sensor_stream_total},"
        f"step_total={step_total},sim_duration_sec_total={sim_duration_sec_total:.3f},"
        f"coverage_ratio_avg={coverage_ratio_avg:.3f},coverage_ratio_samples={coverage_ratio_samples},"
        f"ego_travel_distance_m_total={ego_travel_distance_m_total:.3f}"
    )


def _format_runtime_lane_execution_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        runtime_row_count = int(summary.get("runtime_row_count", 0))
    except (TypeError, ValueError):
        runtime_row_count = 0
    try:
        pass_count = int(summary.get("pass_count", 0))
    except (TypeError, ValueError):
        pass_count = 0
    try:
        fail_count = int(summary.get("fail_count", 0))
    except (TypeError, ValueError):
        fail_count = 0
    try:
        unknown_count = int(summary.get("unknown_count", 0))
    except (TypeError, ValueError):
        unknown_count = 0
    runtime_counts = summary.get("runtime_counts", {})
    runtime_counts_text = (
        ",".join(
            f"{runtime}:{count}"
            for runtime, count in sorted(runtime_counts.items())
            if str(runtime).strip()
        )
        if isinstance(runtime_counts, dict)
        else ""
    ) or "n/a"
    result_counts = summary.get("result_counts", {})
    result_counts_text = (
        ",".join(
            f"{result}:{count}"
            for result, count in sorted(result_counts.items())
            if str(result).strip()
        )
        if isinstance(result_counts, dict)
        else ""
    ) or "n/a"
    runtime_failure_reason_counts = summary.get("runtime_failure_reason_counts", {})
    runtime_failure_reason_counts_text = (
        ",".join(
            f"{reason}:{count}"
            for reason, count in sorted(runtime_failure_reason_counts.items())
            if str(reason).strip()
        )
        if isinstance(runtime_failure_reason_counts, dict)
        else ""
    ) or "n/a"
    lane_counts = summary.get("lane_counts", {})
    lane_counts_text = (
        ",".join(
            f"{lane}:{count}"
            for lane, count in sorted(lane_counts.items())
            if str(lane).strip()
        )
        if isinstance(lane_counts, dict)
        else ""
    ) or "n/a"
    lane_row_counts = summary.get("lane_row_counts", {})
    lane_row_counts_text = (
        ",".join(
            f"{lane}:{count}"
            for lane, count in sorted(lane_row_counts.items())
            if str(lane).strip()
        )
        if isinstance(lane_row_counts, dict)
        else ""
    ) or "n/a"
    runner_platform_counts = summary.get("runner_platform_counts", {})
    runner_platform_counts_text = (
        ",".join(
            f"{platform}:{count}"
            for platform, count in sorted(runner_platform_counts.items())
            if str(platform).strip()
        )
        if isinstance(runner_platform_counts, dict)
        else ""
    ) or "n/a"
    sim_runtime_input_counts = summary.get("sim_runtime_input_counts", {})
    sim_runtime_input_counts_text = (
        ",".join(
            f"{runtime}:{count}"
            for runtime, count in sorted(sim_runtime_input_counts.items())
            if str(runtime).strip()
        )
        if isinstance(sim_runtime_input_counts, dict)
        else ""
    ) or "n/a"
    dry_run_counts = summary.get("dry_run_counts", {})
    dry_run_counts_text = (
        ",".join(
            f"{value}:{count}"
            for value, count in sorted(dry_run_counts.items())
            if str(value).strip()
        )
        if isinstance(dry_run_counts, dict)
        else ""
    ) or "n/a"
    continue_on_runtime_failure_counts = summary.get("continue_on_runtime_failure_counts", {})
    continue_on_runtime_failure_counts_text = (
        ",".join(
            f"{value}:{count}"
            for value, count in sorted(continue_on_runtime_failure_counts.items())
            if str(value).strip()
        )
        if isinstance(continue_on_runtime_failure_counts, dict)
        else ""
    ) or "n/a"
    runtime_exec_lane_warn_min_rows_counts = summary.get("runtime_exec_lane_warn_min_rows_counts", {})
    runtime_exec_lane_warn_min_rows_counts_text = (
        _format_threshold_counts(runtime_exec_lane_warn_min_rows_counts)
        if isinstance(runtime_exec_lane_warn_min_rows_counts, dict)
        else "n/a"
    )
    runtime_exec_lane_hold_min_rows_counts = summary.get("runtime_exec_lane_hold_min_rows_counts", {})
    runtime_exec_lane_hold_min_rows_counts_text = (
        _format_threshold_counts(runtime_exec_lane_hold_min_rows_counts)
        if isinstance(runtime_exec_lane_hold_min_rows_counts, dict)
        else "n/a"
    )
    runtime_compare_warn_min_artifacts_with_diffs_counts = summary.get(
        "runtime_compare_warn_min_artifacts_with_diffs_counts",
        {},
    )
    runtime_compare_warn_min_artifacts_with_diffs_counts_text = (
        _format_threshold_counts(runtime_compare_warn_min_artifacts_with_diffs_counts)
        if isinstance(runtime_compare_warn_min_artifacts_with_diffs_counts, dict)
        else "n/a"
    )
    runtime_compare_hold_min_artifacts_with_diffs_counts = summary.get(
        "runtime_compare_hold_min_artifacts_with_diffs_counts",
        {},
    )
    runtime_compare_hold_min_artifacts_with_diffs_counts_text = (
        _format_threshold_counts(runtime_compare_hold_min_artifacts_with_diffs_counts)
        if isinstance(runtime_compare_hold_min_artifacts_with_diffs_counts, dict)
        else "n/a"
    )
    phase2_sensor_fidelity_score_avg_warn_min_counts = summary.get(
        "phase2_sensor_fidelity_score_avg_warn_min_counts",
        {},
    )
    phase2_sensor_fidelity_score_avg_warn_min_counts_text = (
        _format_threshold_counts(phase2_sensor_fidelity_score_avg_warn_min_counts)
        if isinstance(phase2_sensor_fidelity_score_avg_warn_min_counts, dict)
        else "n/a"
    )
    phase2_sensor_fidelity_score_avg_hold_min_counts = summary.get(
        "phase2_sensor_fidelity_score_avg_hold_min_counts",
        {},
    )
    phase2_sensor_fidelity_score_avg_hold_min_counts_text = (
        _format_threshold_counts(phase2_sensor_fidelity_score_avg_hold_min_counts)
        if isinstance(phase2_sensor_fidelity_score_avg_hold_min_counts, dict)
        else "n/a"
    )
    phase2_sensor_frame_count_avg_warn_min_counts = summary.get(
        "phase2_sensor_frame_count_avg_warn_min_counts",
        {},
    )
    phase2_sensor_frame_count_avg_warn_min_counts_text = (
        _format_threshold_counts(phase2_sensor_frame_count_avg_warn_min_counts)
        if isinstance(phase2_sensor_frame_count_avg_warn_min_counts, dict)
        else "n/a"
    )
    phase2_sensor_frame_count_avg_hold_min_counts = summary.get(
        "phase2_sensor_frame_count_avg_hold_min_counts",
        {},
    )
    phase2_sensor_frame_count_avg_hold_min_counts_text = (
        _format_threshold_counts(phase2_sensor_frame_count_avg_hold_min_counts)
        if isinstance(phase2_sensor_frame_count_avg_hold_min_counts, dict)
        else "n/a"
    )
    phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts = summary.get(
        "phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts",
        {},
    )
    phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text = (
        _format_threshold_counts(phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts)
        if isinstance(phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts, dict)
        else "n/a"
    )
    phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts = summary.get(
        "phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts",
        {},
    )
    phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text = (
        _format_threshold_counts(phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts)
        if isinstance(phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts, dict)
        else "n/a"
    )
    phase2_sensor_lidar_point_count_avg_warn_min_counts = summary.get(
        "phase2_sensor_lidar_point_count_avg_warn_min_counts",
        {},
    )
    phase2_sensor_lidar_point_count_avg_warn_min_counts_text = (
        _format_threshold_counts(phase2_sensor_lidar_point_count_avg_warn_min_counts)
        if isinstance(phase2_sensor_lidar_point_count_avg_warn_min_counts, dict)
        else "n/a"
    )
    phase2_sensor_lidar_point_count_avg_hold_min_counts = summary.get(
        "phase2_sensor_lidar_point_count_avg_hold_min_counts",
        {},
    )
    phase2_sensor_lidar_point_count_avg_hold_min_counts_text = (
        _format_threshold_counts(phase2_sensor_lidar_point_count_avg_hold_min_counts)
        if isinstance(phase2_sensor_lidar_point_count_avg_hold_min_counts, dict)
        else "n/a"
    )
    phase2_sensor_radar_false_positive_rate_avg_warn_max_counts = summary.get(
        "phase2_sensor_radar_false_positive_rate_avg_warn_max_counts",
        {},
    )
    phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text = (
        _format_threshold_counts(phase2_sensor_radar_false_positive_rate_avg_warn_max_counts)
        if isinstance(phase2_sensor_radar_false_positive_rate_avg_warn_max_counts, dict)
        else "n/a"
    )
    phase2_sensor_radar_false_positive_rate_avg_hold_max_counts = summary.get(
        "phase2_sensor_radar_false_positive_rate_avg_hold_max_counts",
        {},
    )
    phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text = (
        _format_threshold_counts(phase2_sensor_radar_false_positive_rate_avg_hold_max_counts)
        if isinstance(phase2_sensor_radar_false_positive_rate_avg_hold_max_counts, dict)
        else "n/a"
    )
    runtime_asset_profile_counts = summary.get("runtime_asset_profile_counts", {})
    runtime_asset_profile_counts_text = (
        ",".join(
            f"{profile}:{count}"
            for profile, count in sorted(runtime_asset_profile_counts.items())
            if str(profile).strip()
        )
        if isinstance(runtime_asset_profile_counts, dict)
        else ""
    ) or "n/a"
    runtime_asset_archive_sha256_mode_counts = summary.get("runtime_asset_archive_sha256_mode_counts", {})
    runtime_asset_archive_sha256_mode_counts_text = (
        ",".join(
            f"{mode}:{count}"
            for mode, count in sorted(runtime_asset_archive_sha256_mode_counts.items())
            if str(mode).strip()
        )
        if isinstance(runtime_asset_archive_sha256_mode_counts, dict)
        else ""
    ) or "n/a"
    runtime_evidence_missing_runtime_counts = summary.get("runtime_evidence_missing_runtime_counts", {})
    runtime_evidence_missing_runtime_counts_text = (
        ",".join(
            f"{runtime}:{count}"
            for runtime, count in sorted(runtime_evidence_missing_runtime_counts.items())
            if str(runtime).strip()
        )
        if isinstance(runtime_evidence_missing_runtime_counts, dict)
        else ""
    ) or "n/a"
    try:
        runtime_evidence_path_present_count = int(summary.get("runtime_evidence_path_present_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_path_present_count = 0
    try:
        runtime_evidence_exists_true_count = int(summary.get("runtime_evidence_exists_true_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_exists_true_count = 0
    try:
        runtime_evidence_exists_false_count = int(summary.get("runtime_evidence_exists_false_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_exists_false_count = 0
    try:
        runtime_evidence_exists_unknown_count = int(summary.get("runtime_evidence_exists_unknown_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_exists_unknown_count = 0
    return (
        f"artifacts={artifact_count},rows={runtime_row_count},"
        f"pass={pass_count},fail={fail_count},unknown={unknown_count},"
        f"results={result_counts_text},failure_reasons={runtime_failure_reason_counts_text},"
        f"runtimes={runtime_counts_text},lanes={lane_counts_text},"
        f"asset_profiles={runtime_asset_profile_counts_text},"
        f"archive_sha256_modes={runtime_asset_archive_sha256_mode_counts_text},"
        f"evidence_paths=present:{runtime_evidence_path_present_count},"
        f"exists:{runtime_evidence_exists_true_count},"
        f"missing:{runtime_evidence_exists_false_count},"
        f"unknown:{runtime_evidence_exists_unknown_count},"
        f"evidence_missing_runtimes={runtime_evidence_missing_runtime_counts_text},"
        f"lane_rows={lane_row_counts_text},"
        f"runner_platforms={runner_platform_counts_text},"
        f"sim_runtime_inputs={sim_runtime_input_counts_text},"
        f"dry_runs={dry_run_counts_text},"
        f"continue_on_runtime_failure={continue_on_runtime_failure_counts_text},"
        f"exec_lane_warn_min_rows={runtime_exec_lane_warn_min_rows_counts_text},"
        f"exec_lane_hold_min_rows={runtime_exec_lane_hold_min_rows_counts_text},"
        "runtime_compare_warn_min_artifacts_with_diffs="
        f"{runtime_compare_warn_min_artifacts_with_diffs_counts_text},"
        "runtime_compare_hold_min_artifacts_with_diffs="
        f"{runtime_compare_hold_min_artifacts_with_diffs_counts_text},"
        "phase2_sensor_fidelity_score_avg_warn_min="
        f"{phase2_sensor_fidelity_score_avg_warn_min_counts_text},"
        "phase2_sensor_fidelity_score_avg_hold_min="
        f"{phase2_sensor_fidelity_score_avg_hold_min_counts_text},"
        "phase2_sensor_frame_count_avg_warn_min="
        f"{phase2_sensor_frame_count_avg_warn_min_counts_text},"
        "phase2_sensor_frame_count_avg_hold_min="
        f"{phase2_sensor_frame_count_avg_hold_min_counts_text},"
        "phase2_sensor_camera_noise_stddev_px_avg_warn_max="
        f"{phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text},"
        "phase2_sensor_camera_noise_stddev_px_avg_hold_max="
        f"{phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text},"
        "phase2_sensor_lidar_point_count_avg_warn_min="
        f"{phase2_sensor_lidar_point_count_avg_warn_min_counts_text},"
        "phase2_sensor_lidar_point_count_avg_hold_min="
        f"{phase2_sensor_lidar_point_count_avg_hold_min_counts_text},"
        "phase2_sensor_radar_false_positive_rate_avg_warn_max="
        f"{phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text},"
        "phase2_sensor_radar_false_positive_rate_avg_hold_max="
        f"{phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text}"
    )


def _format_runtime_lane_phase2_rig_sweep_radar_alignment_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        runtime_row_count = int(summary.get("runtime_row_count", 0))
    except (TypeError, ValueError):
        runtime_row_count = 0
    if runtime_row_count <= 0:
        return "n/a"
    try:
        matched_manifest_count = int(summary.get("matched_manifest_count", 0))
    except (TypeError, ValueError):
        matched_manifest_count = 0
    try:
        metrics_sample_count = int(summary.get("metrics_sample_count", 0))
    except (TypeError, ValueError):
        metrics_sample_count = 0
    try:
        unmatched_row_count = int(summary.get("unmatched_row_count", 0))
    except (TypeError, ValueError):
        unmatched_row_count = 0
    runtime_counts_text = _format_non_negative_int_counts(
        _as_non_negative_int_map(summary.get("runtime_counts", {}))
    )
    result_counts_text = _format_non_negative_int_counts(
        _as_non_negative_int_map(summary.get("result_counts", {}))
    )
    mapping_mode_counts_text = _format_non_negative_int_counts(
        _as_non_negative_int_map(summary.get("mapping_mode_counts", {}))
    )
    pass_minus_fail_metric_delta = _as_float_map(summary.get("pass_minus_fail_metric_delta", {}))
    pass_minus_fail_effective = float(
        pass_minus_fail_metric_delta.get("radar_effective_detection_quality_avg", 0.0) or 0.0
    )
    pass_minus_fail_track_purity = float(
        pass_minus_fail_metric_delta.get("radar_track_purity_avg", 0.0) or 0.0
    )
    pass_minus_fail_doppler = float(
        pass_minus_fail_metric_delta.get("radar_doppler_resolution_quality_avg", 0.0) or 0.0
    )
    pass_minus_fail_range = float(
        pass_minus_fail_metric_delta.get("radar_range_coverage_quality_avg", 0.0) or 0.0
    )
    return (
        f"rows={runtime_row_count},matched={matched_manifest_count},"
        f"metric_samples={metrics_sample_count},runtimes={runtime_counts_text},"
        f"results={result_counts_text},mapping_modes={mapping_mode_counts_text},"
        f"unmatched={unmatched_row_count},"
        f"pass_minus_fail_effective_quality_avg={pass_minus_fail_effective:.3f},"
        f"pass_minus_fail_track_purity_avg={pass_minus_fail_track_purity:.3f},"
        f"pass_minus_fail_doppler_quality_avg={pass_minus_fail_doppler:.3f},"
        f"pass_minus_fail_range_quality_avg={pass_minus_fail_range:.3f}"
    )


def _format_runtime_evidence_compare_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        artifacts_with_diffs_count = int(summary.get("artifacts_with_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_with_diffs_count = 0
    try:
        artifacts_without_diffs_count = int(summary.get("artifacts_without_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_without_diffs_count = 0
    try:
        top_level_mismatches_count = int(summary.get("top_level_mismatches_count", 0))
    except (TypeError, ValueError):
        top_level_mismatches_count = 0
    try:
        status_count_diffs_count = int(summary.get("status_count_diffs_count", 0))
    except (TypeError, ValueError):
        status_count_diffs_count = 0
    try:
        runtime_count_diffs_count = int(summary.get("runtime_count_diffs_count", 0))
    except (TypeError, ValueError):
        runtime_count_diffs_count = 0
    try:
        interop_import_status_count_diffs_count = int(summary.get("interop_import_status_count_diffs_count", 0))
    except (TypeError, ValueError):
        interop_import_status_count_diffs_count = 0
    try:
        interop_import_manifest_consistency_diffs_count = int(
            summary.get("interop_import_manifest_consistency_diffs_count", 0)
        )
    except (TypeError, ValueError):
        interop_import_manifest_consistency_diffs_count = 0
    try:
        interop_import_profile_diff_count = int(summary.get("interop_import_profile_diff_count", 0))
    except (TypeError, ValueError):
        interop_import_profile_diff_count = 0
    try:
        profile_left_only_count = int(summary.get("profile_left_only_count", 0))
    except (TypeError, ValueError):
        profile_left_only_count = 0
    try:
        profile_right_only_count = int(summary.get("profile_right_only_count", 0))
    except (TypeError, ValueError):
        profile_right_only_count = 0
    try:
        shared_profile_count = int(summary.get("shared_profile_count", 0))
    except (TypeError, ValueError):
        shared_profile_count = 0
    try:
        profile_diff_count = int(summary.get("profile_diff_count", 0))
    except (TypeError, ValueError):
        profile_diff_count = 0
    label_pair_counts = summary.get("label_pair_counts", {})
    label_pair_counts_text = (
        ",".join(
            f"{label_pair}:{count}"
            for label_pair, count in sorted(label_pair_counts.items())
            if str(label_pair).strip()
        )
        if isinstance(label_pair_counts, dict)
        else ""
    ) or "n/a"
    return (
        f"artifacts={artifact_count},with_diffs={artifacts_with_diffs_count},"
        f"without_diffs={artifacts_without_diffs_count},"
        f"top_level_mismatches={top_level_mismatches_count},"
        f"status_count_diffs={status_count_diffs_count},"
        f"runtime_count_diffs={runtime_count_diffs_count},"
        f"interop_import_status_count_diffs={interop_import_status_count_diffs_count},"
        "interop_import_manifest_consistency_diffs="
        f"{interop_import_manifest_consistency_diffs_count},"
        f"interop_import_profile_diffs={interop_import_profile_diff_count},"
        "profile_presence="
        f"shared:{shared_profile_count},left_only:{profile_left_only_count},"
        f"right_only:{profile_right_only_count},"
        f"profile_diffs={profile_diff_count},"
        f"label_pairs={label_pair_counts_text}"
    )


def _format_runtime_evidence_compare_interop_import_mode_diff_counts(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    try:
        artifact_count = int(summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        manifest_mode = int(summary.get("interop_import_manifest_mode_count_diffs_count", 0))
    except (TypeError, ValueError):
        manifest_mode = 0
    try:
        export_mode = int(summary.get("interop_import_export_mode_count_diffs_count", 0))
    except (TypeError, ValueError):
        export_mode = 0
    try:
        require_manifest_input = int(summary.get("interop_import_require_manifest_input_count_diffs_count", 0))
    except (TypeError, ValueError):
        require_manifest_input = 0
    try:
        require_export_input = int(summary.get("interop_import_require_export_input_count_diffs_count", 0))
    except (TypeError, ValueError):
        require_export_input = 0
    return (
        f"manifest_mode={manifest_mode},export_mode={export_mode},"
        f"require_manifest_input={require_manifest_input},require_export_input={require_export_input}"
    )


def _runtime_evidence_compare_interop_import_mode_diff_count_total(summary: dict[str, Any]) -> int:
    if not isinstance(summary, dict):
        return 0
    count_total = 0
    for key in (
        "interop_import_manifest_mode_count_diffs_count",
        "interop_import_export_mode_count_diffs_count",
        "interop_import_require_manifest_input_count_diffs_count",
        "interop_import_require_export_input_count_diffs_count",
    ):
        try:
            count_total += max(0, int(summary.get(key, 0)))
        except (TypeError, ValueError):
            continue
    return count_total


def _format_runtime_evidence_compare_interop_import_profile_diff_counts(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    field_counts = _as_non_negative_int_map(summary.get("interop_import_profile_diff_field_counts", {}))
    numeric_counts = _as_non_negative_int_map(summary.get("interop_import_profile_diff_numeric_counts", {}))
    field_counts_text = (
        ",".join(
            f"{key}:{field_counts[key]}"
            for key in sorted(field_counts.keys())
            if int(field_counts[key]) > 0
        )
        if field_counts
        else ""
    ) or "n/a"
    numeric_counts_text = (
        ",".join(
            f"{key}:{numeric_counts[key]}"
            for key in sorted(numeric_counts.keys())
            if int(numeric_counts[key]) > 0
        )
        if numeric_counts
        else ""
    ) or "n/a"
    if field_counts_text == "n/a" and numeric_counts_text == "n/a":
        return "n/a"
    return f"fields={field_counts_text},numeric={numeric_counts_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_breakdown(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    label_pair_counts = _as_non_negative_int_map(summary.get("interop_import_profile_diff_label_pair_counts", {}))
    profile_counts = _as_non_negative_int_map(summary.get("interop_import_profile_diff_profile_counts", {}))
    label_pair_counts_text = (
        ",".join(
            f"{key}:{label_pair_counts[key]}"
            for key in sorted(label_pair_counts.keys())
            if int(label_pair_counts[key]) > 0
        )
        if label_pair_counts
        else ""
    ) or "n/a"
    profile_counts_text = (
        ",".join(
            f"{key}:{profile_counts[key]}"
            for key in sorted(profile_counts.keys())
            if int(profile_counts[key]) > 0
        )
        if profile_counts
        else ""
    ) or "n/a"
    if label_pair_counts_text == "n/a" and profile_counts_text == "n/a":
        return "n/a"
    return f"label_pairs={label_pair_counts_text},profiles={profile_counts_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    priority_counts = _as_non_negative_int_map(
        summary.get("interop_import_profile_diff_numeric_delta_hotspot_priority_counts", {})
    )
    return _format_non_negative_int_counts(priority_counts)


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    action_counts = _as_non_negative_int_map(
        summary.get("interop_import_profile_diff_numeric_delta_hotspot_action_counts", {})
    )
    return _format_non_negative_int_counts(action_counts)


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    reason_counts = _as_non_negative_int_map(
        summary.get("interop_import_profile_diff_numeric_delta_hotspot_reason_counts", {})
    )
    return _format_non_negative_int_counts(reason_counts)


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    delta_totals = _as_float_map(summary.get("interop_import_profile_diff_numeric_delta_totals", {}))
    delta_abs_totals = _as_float_map(summary.get("interop_import_profile_diff_numeric_delta_abs_totals", {}))
    delta_totals_text = _format_float_counts(delta_totals)
    delta_abs_totals_text = _format_float_counts(delta_abs_totals)
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text},delta_abs_totals={delta_abs_totals_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    delta_totals = _as_float_nested_map(
        summary.get("interop_import_profile_diff_numeric_delta_totals_by_label_pair", {})
    )
    delta_abs_totals = _as_float_nested_map(
        summary.get("interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair", {})
    )
    delta_totals_text = _format_float_nested_counts(delta_totals)
    delta_abs_totals_text = _format_float_nested_counts(delta_abs_totals)
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text},delta_abs_totals={delta_abs_totals_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    delta_totals = _as_float_nested_map(
        summary.get("interop_import_profile_diff_numeric_delta_totals_by_profile", {})
    )
    delta_abs_totals = _as_float_nested_map(
        summary.get("interop_import_profile_diff_numeric_delta_abs_totals_by_profile", {})
    )
    delta_totals_text = _format_float_nested_counts(delta_totals)
    delta_abs_totals_text = _format_float_nested_counts(delta_abs_totals)
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text},delta_abs_totals={delta_abs_totals_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    delta_totals = _as_float_nested_map(
        summary.get("interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile", {})
    )
    delta_abs_totals = _as_float_nested_map(
        summary.get("interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile", {})
    )
    delta_totals_text = _format_float_nested_counts(delta_totals)
    delta_abs_totals_text = _format_float_nested_counts(delta_abs_totals)
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text},delta_abs_totals={delta_abs_totals_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions(
    summary: dict[str, Any],
) -> str:
    if not isinstance(summary, dict):
        return "n/a"
    positive_counts = _as_non_negative_int_map(
        summary.get("interop_import_profile_diff_numeric_delta_positive_counts", {})
    )
    negative_counts = _as_non_negative_int_map(
        summary.get("interop_import_profile_diff_numeric_delta_negative_counts", {})
    )
    zero_counts = _as_non_negative_int_map(
        summary.get("interop_import_profile_diff_numeric_delta_zero_counts", {})
    )
    positive_text = (
        ",".join(
            f"{key}:{positive_counts[key]}"
            for key in sorted(positive_counts.keys())
            if int(positive_counts[key]) > 0
        )
        if positive_counts
        else ""
    ) or "n/a"
    negative_text = (
        ",".join(
            f"{key}:{negative_counts[key]}"
            for key in sorted(negative_counts.keys())
            if int(negative_counts[key]) > 0
        )
        if negative_counts
        else ""
    ) or "n/a"
    zero_text = (
        ",".join(
            f"{key}:{zero_counts[key]}"
            for key in sorted(zero_counts.keys())
            if int(zero_counts[key]) > 0
        )
        if zero_counts
        else ""
    ) or "n/a"
    if positive_text == "n/a" and negative_text == "n/a" and zero_text == "n/a":
        return "n/a"
    return f"positive={positive_text},negative={negative_text},zero={zero_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes(
    positive_rows: list[dict[str, Any]],
    negative_rows: list[dict[str, Any]],
    *,
    max_items: int = 5,
) -> str:
    def _format_rows(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "n/a"
        normalized: list[str] = []
        for row in rows[: max(1, int(max_items))]:
            if not isinstance(row, dict):
                continue
            left_label = str(row.get("left_label", "")).strip() or "left"
            right_label = str(row.get("right_label", "")).strip() or "right"
            profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
            numeric_key = str(row.get("numeric_key", "")).strip()
            if not numeric_key:
                continue
            delta_raw = row.get("delta")
            if delta_raw is None or isinstance(delta_raw, bool):
                continue
            try:
                delta_value = float(delta_raw)
            except (TypeError, ValueError):
                continue
            normalized.append(
                f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:delta={delta_value:.6f}"
            )
        if not normalized:
            return "n/a"
        remaining = max(0, len(rows) - len(normalized))
        if remaining > 0:
            normalized.append(f"...(+{remaining} more)")
        return "; ".join(normalized)

    positive_text = _format_rows(positive_rows)
    negative_text = _format_rows(negative_rows)
    if positive_text == "n/a" and negative_text == "n/a":
        return "n/a"
    return f"positive={positive_text},negative={negative_text}"


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots(
    rows: list[dict[str, Any]],
    *,
    total_count: int = 0,
    max_items: int = 5,
) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        numeric_key = str(row.get("numeric_key", "")).strip()
        if not numeric_key:
            continue
        delta_raw = row.get("delta")
        if delta_raw is None or isinstance(delta_raw, bool):
            continue
        try:
            delta_value = float(delta_raw)
        except (TypeError, ValueError):
            continue
        delta_abs_raw = row.get("delta_abs")
        if delta_abs_raw is None or isinstance(delta_abs_raw, bool):
            delta_abs_value = abs(delta_value)
        else:
            try:
                delta_abs_value = float(delta_abs_raw)
            except (TypeError, ValueError):
                delta_abs_value = abs(delta_value)
        normalized.append(
            f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:"
            f"delta={delta_value:.6f}:abs={abs(delta_abs_value):.6f}"
        )
    if not normalized:
        return "n/a"
    remaining = max(0, max(total_count, len(rows)) - len(normalized))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized)


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile(
    rows: list[dict[str, Any]],
    *,
    total_count: int = 0,
    max_items: int = 5,
) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        numeric_key_count_raw = row.get("numeric_key_count", 0)
        if numeric_key_count_raw is None or isinstance(numeric_key_count_raw, bool):
            numeric_key_count = 0
        else:
            try:
                numeric_key_count = max(0, int(numeric_key_count_raw))
            except (TypeError, ValueError):
                numeric_key_count = 0
        delta_total_raw = row.get("delta_total")
        if delta_total_raw is None or isinstance(delta_total_raw, bool):
            delta_total_value = 0.0
        else:
            try:
                delta_total_value = float(delta_total_raw)
            except (TypeError, ValueError):
                delta_total_value = 0.0
        delta_abs_total_raw = row.get("delta_abs_total")
        if delta_abs_total_raw is None or isinstance(delta_abs_total_raw, bool):
            delta_abs_total_value = abs(delta_total_value)
        else:
            try:
                delta_abs_total_value = abs(float(delta_abs_total_raw))
            except (TypeError, ValueError):
                delta_abs_total_value = abs(delta_total_value)
        top_numeric_key = str(row.get("top_numeric_key", "")).strip() or "n/a"
        top_numeric_delta_raw = row.get("top_numeric_delta")
        if top_numeric_delta_raw is None or isinstance(top_numeric_delta_raw, bool):
            top_numeric_delta_value = 0.0
        else:
            try:
                top_numeric_delta_value = float(top_numeric_delta_raw)
            except (TypeError, ValueError):
                top_numeric_delta_value = 0.0
        top_numeric_delta_abs_raw = row.get("top_numeric_delta_abs")
        if top_numeric_delta_abs_raw is None or isinstance(top_numeric_delta_abs_raw, bool):
            top_numeric_delta_abs_value = abs(top_numeric_delta_value)
        else:
            try:
                top_numeric_delta_abs_value = abs(float(top_numeric_delta_abs_raw))
            except (TypeError, ValueError):
                top_numeric_delta_abs_value = abs(top_numeric_delta_value)
        positive_delta_abs_total_raw = row.get("positive_delta_abs_total")
        if positive_delta_abs_total_raw is None or isinstance(positive_delta_abs_total_raw, bool):
            positive_delta_abs_total_value = 0.0
        else:
            try:
                positive_delta_abs_total_value = abs(float(positive_delta_abs_total_raw))
            except (TypeError, ValueError):
                positive_delta_abs_total_value = 0.0
        negative_delta_abs_total_raw = row.get("negative_delta_abs_total")
        if negative_delta_abs_total_raw is None or isinstance(negative_delta_abs_total_raw, bool):
            negative_delta_abs_total_value = 0.0
        else:
            try:
                negative_delta_abs_total_value = abs(float(negative_delta_abs_total_raw))
            except (TypeError, ValueError):
                negative_delta_abs_total_value = 0.0
        zero_numeric_key_count_raw = row.get("zero_numeric_key_count", 0)
        if zero_numeric_key_count_raw is None or isinstance(zero_numeric_key_count_raw, bool):
            zero_numeric_key_count = 0
        else:
            try:
                zero_numeric_key_count = max(0, int(zero_numeric_key_count_raw))
            except (TypeError, ValueError):
                zero_numeric_key_count = 0
        top_positive_numeric_key = str(row.get("top_positive_numeric_key", "")).strip() or "n/a"
        top_negative_numeric_key = str(row.get("top_negative_numeric_key", "")).strip() or "n/a"
        direction_imbalance_ratio_raw = row.get("direction_imbalance_ratio")
        if direction_imbalance_ratio_raw is None or isinstance(direction_imbalance_ratio_raw, bool):
            direction_imbalance_ratio_value = 0.0
        else:
            try:
                direction_imbalance_ratio_value = max(0.0, min(1.0, float(direction_imbalance_ratio_raw)))
            except (TypeError, ValueError):
                direction_imbalance_ratio_value = 0.0
        dominant_direction = str(row.get("dominant_direction", "")).strip().lower()
        if dominant_direction not in {"positive", "negative", "balanced"}:
            dominant_direction = "n/a"
        priority_score_raw = row.get("priority_score")
        if priority_score_raw is None or isinstance(priority_score_raw, bool):
            priority_score_value = 0.0
        else:
            try:
                priority_score_value = float(priority_score_raw)
            except (TypeError, ValueError):
                priority_score_value = 0.0
        priority_bucket = str(row.get("priority_bucket", "")).strip().lower()
        if priority_bucket not in {"high", "medium", "low"}:
            priority_bucket = "n/a"
        recommended_action = str(row.get("recommended_action", "")).strip() or "n/a"
        recommended_reason = str(row.get("recommended_reason", "")).strip() or "n/a"
        normalized.append(
            f"{left_label}_vs_{right_label}:{profile_id}:"
            f"abs_total={delta_abs_total_value:.6f}:delta_total={delta_total_value:.6f}:"
            f"numeric_keys={numeric_key_count}:top_numeric={top_numeric_key}:"
            f"top_abs={top_numeric_delta_abs_value:.6f}:top_delta={top_numeric_delta_value:.6f}:"
            f"pos_abs_total={positive_delta_abs_total_value:.6f}:"
            f"neg_abs_total={negative_delta_abs_total_value:.6f}:"
            f"zero_keys={zero_numeric_key_count}:"
            f"top_pos={top_positive_numeric_key}:top_neg={top_negative_numeric_key}:"
            f"imbalance={direction_imbalance_ratio_value:.6f}:"
            f"direction={dominant_direction}:"
            f"priority_score={priority_score_value:.6f}:"
            f"priority_bucket={priority_bucket}:"
            f"action={recommended_action}:"
            f"reason={recommended_reason}"
        )
    if not normalized:
        return "n/a"
    remaining = max(0, max(total_count, len(rows)) - len(normalized))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized)


def _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations(
    rows: list[dict[str, Any]],
    *,
    total_count: int = 0,
    max_items: int = 5,
) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        recommended_action = str(row.get("recommended_action", "")).strip() or "n/a"
        recommended_reason = str(row.get("recommended_reason", "")).strip() or "n/a"
        recommended_checklist_raw = row.get("recommended_checklist", [])
        recommended_checklist = (
            [str(item).strip() for item in recommended_checklist_raw if str(item).strip()]
            if isinstance(recommended_checklist_raw, list)
            else []
        )
        checklist_text = "|".join(recommended_checklist) if recommended_checklist else "n/a"
        normalized.append(
            f"{left_label}_vs_{right_label}:{profile_id}:"
            f"action={recommended_action}:"
            f"reason={recommended_reason}:"
            f"checklist={checklist_text}"
        )
    if not normalized:
        return "n/a"
    remaining = max(0, max(total_count, len(rows)) - len(normalized))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized)


def _format_runtime_lane_execution_failed_rows(rows: list[dict[str, str]], *, max_items: int = 5) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        runtime = str(row.get("runtime", "")).strip() or "runtime_unknown"
        release_id = str(row.get("release_id", "")).strip() or "release_unknown"
        lane = str(row.get("lane", "")).strip() or "lane_unknown"
        runtime_failure_reason = str(row.get("runtime_failure_reason", "")).strip() or "unknown"
        normalized.append(f"{runtime}:{release_id}:{lane}:{runtime_failure_reason}")
    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized) if normalized else "n/a"


def _format_runtime_evidence_failed_records(rows: list[dict[str, str]], *, max_items: int = 5) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        release_id = str(row.get("release_id", "")).strip() or "release_unknown"
        error_text = str(row.get("error", "")).strip() or "n/a"
        normalized.append(f"{profile_id}:{release_id}:{error_text}")
    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized) if normalized else "n/a"


def _format_runtime_evidence_interop_import_inconsistent_records(
    rows: list[dict[str, Any]],
    *,
    max_items: int = 5,
) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        release_id = str(row.get("release_id", "")).strip() or "release_unknown"
        runtime_name = str(row.get("runtime", "")).strip().lower() or "runtime_unknown"
        try:
            actor_count_manifest = int(row.get("actor_count_manifest", 0) or 0)
        except (TypeError, ValueError):
            actor_count_manifest = 0
        try:
            xosc_entity_count = int(row.get("xosc_entity_count", 0) or 0)
        except (TypeError, ValueError):
            xosc_entity_count = 0
        normalized.append(
            f"{profile_id}:{release_id}:{runtime_name}:manifest={actor_count_manifest}:imported={xosc_entity_count}"
        )
    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized) if normalized else "n/a"


def _format_runtime_evidence_compare_interop_import_profile_diffs(
    rows: list[dict[str, Any]],
    *,
    max_items: int = 5,
) -> str:
    if not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        field_keys_raw = row.get("field_keys", [])
        numeric_keys_raw = row.get("numeric_keys", [])
        field_keys = (
            [str(key).strip() for key in field_keys_raw if str(key).strip()]
            if isinstance(field_keys_raw, list)
            else []
        )
        numeric_keys = (
            [str(key).strip() for key in numeric_keys_raw if str(key).strip()]
            if isinstance(numeric_keys_raw, list)
            else []
        )
        field_text = "|".join(sorted(set(field_keys))) if field_keys else "n/a"
        numeric_text = "|".join(sorted(set(numeric_keys))) if numeric_keys else "n/a"
        normalized.append(
            f"{left_label}_vs_{right_label}:{profile_id}:fields={field_text}:numeric={numeric_text}"
        )
    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized) if normalized else "n/a"


def _collect_phase3_vehicle_dynamics_violation_rows(
    pipeline_manifests: list[dict[str, Any]],
    *,
    speed_warn_max: float,
    speed_hold_max: float,
    position_warn_max: float,
    position_hold_max: float,
    delta_speed_warn_max: float,
    delta_speed_hold_max: float,
    delta_position_warn_max: float,
    delta_position_hold_max: float,
    final_heading_abs_warn_max: float,
    final_heading_abs_hold_max: float,
    final_lateral_position_abs_warn_max: float,
    final_lateral_position_abs_hold_max: float,
    delta_heading_abs_warn_max: float,
    delta_heading_abs_hold_max: float,
    delta_lateral_position_abs_warn_max: float,
    delta_lateral_position_abs_hold_max: float,
    yaw_rate_abs_warn_max: float,
    yaw_rate_abs_hold_max: float,
    delta_yaw_rate_abs_warn_max: float,
    delta_yaw_rate_abs_hold_max: float,
    lateral_velocity_abs_warn_max: float,
    lateral_velocity_abs_hold_max: float,
    accel_abs_warn_max: float,
    accel_abs_hold_max: float,
    lateral_accel_abs_warn_max: float,
    lateral_accel_abs_hold_max: float,
    yaw_accel_abs_warn_max: float,
    yaw_accel_abs_hold_max: float,
    jerk_abs_warn_max: float,
    jerk_abs_hold_max: float,
    lateral_jerk_abs_warn_max: float,
    lateral_jerk_abs_hold_max: float,
    yaw_jerk_abs_warn_max: float,
    yaw_jerk_abs_hold_max: float,
    lateral_position_abs_warn_max: float,
    lateral_position_abs_hold_max: float,
    road_grade_abs_warn_max: float,
    road_grade_abs_hold_max: float,
    grade_force_warn_max: float,
    grade_force_hold_max: float,
    control_overlap_ratio_warn_max: float,
    control_overlap_ratio_hold_max: float,
    control_steering_rate_warn_max: float,
    control_steering_rate_hold_max: float,
    control_throttle_plus_brake_warn_max: float,
    control_throttle_plus_brake_hold_max: float,
    speed_tracking_error_warn_max: float,
    speed_tracking_error_hold_max: float,
    speed_tracking_abs_error_warn_max: float,
    speed_tracking_abs_error_hold_max: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _append_violation(
        *,
        batch_id: str,
        metric: str,
        value: float,
        warn_max: float,
        hold_max: float,
    ) -> None:
        if hold_max > 0 and value > hold_max:
            rows.append(
                {
                    "batch_id": batch_id,
                    "metric": metric,
                    "value": value,
                    "threshold": hold_max,
                    "severity": "HOLD",
                }
            )
            return
        if warn_max > 0 and value > warn_max:
            rows.append(
                {
                    "batch_id": batch_id,
                    "metric": metric,
                    "value": value,
                    "threshold": warn_max,
                    "severity": "WARN",
                }
            )

    for item in pipeline_manifests:
        if not isinstance(item, dict):
            continue
        try:
            step_count = int(item.get("phase3_vehicle_dynamics_step_count", 0))
        except (TypeError, ValueError):
            step_count = 0
        if step_count <= 0:
            continue

        batch_id = str(item.get("batch_id", "")).strip() or "batch_unknown"
        try:
            final_speed_mps = float(item.get("phase3_vehicle_dynamics_final_speed_mps", 0.0) or 0.0)
        except (TypeError, ValueError):
            final_speed_mps = 0.0
        try:
            final_position_m = float(item.get("phase3_vehicle_dynamics_final_position_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            final_position_m = 0.0
        try:
            initial_speed_mps = float(item.get("phase3_vehicle_dynamics_initial_speed_mps", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial_speed_mps = 0.0
        try:
            initial_position_m = float(item.get("phase3_vehicle_dynamics_initial_position_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial_position_m = 0.0
        try:
            final_heading_deg = float(item.get("phase3_vehicle_dynamics_final_heading_deg", 0.0) or 0.0)
        except (TypeError, ValueError):
            final_heading_deg = 0.0
        try:
            final_lateral_position_m = float(
                item.get("phase3_vehicle_dynamics_final_lateral_position_m", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            final_lateral_position_m = 0.0
        try:
            initial_heading_deg = float(item.get("phase3_vehicle_dynamics_initial_heading_deg", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial_heading_deg = 0.0
        try:
            initial_lateral_position_m = float(
                item.get("phase3_vehicle_dynamics_initial_lateral_position_m", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            initial_lateral_position_m = 0.0
        try:
            initial_yaw_rate_rps = float(item.get("phase3_vehicle_dynamics_initial_yaw_rate_rps", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial_yaw_rate_rps = 0.0
        try:
            final_yaw_rate_rps = float(item.get("phase3_vehicle_dynamics_final_yaw_rate_rps", 0.0) or 0.0)
        except (TypeError, ValueError):
            final_yaw_rate_rps = 0.0
        try:
            max_abs_yaw_rate_rps = float(item.get("phase3_vehicle_dynamics_max_abs_yaw_rate_rps", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_abs_yaw_rate_rps = 0.0
        try:
            max_abs_lateral_velocity_mps = float(
                item.get("phase3_vehicle_dynamics_max_abs_lateral_velocity_mps", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            max_abs_lateral_velocity_mps = 0.0
        try:
            max_abs_accel_mps2 = float(item.get("phase3_vehicle_dynamics_max_abs_accel_mps2", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_abs_accel_mps2 = 0.0
        try:
            max_abs_lateral_accel_mps2 = float(
                item.get("phase3_vehicle_dynamics_max_abs_lateral_accel_mps2", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            max_abs_lateral_accel_mps2 = 0.0
        try:
            max_abs_yaw_accel_rps2 = float(
                item.get("phase3_vehicle_dynamics_max_abs_yaw_accel_rps2", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            max_abs_yaw_accel_rps2 = 0.0
        try:
            max_abs_jerk_mps3 = float(item.get("phase3_vehicle_dynamics_max_abs_jerk_mps3", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_abs_jerk_mps3 = 0.0
        try:
            max_abs_lateral_jerk_mps3 = float(
                item.get("phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            max_abs_lateral_jerk_mps3 = 0.0
        try:
            max_abs_yaw_jerk_rps3 = float(
                item.get("phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            max_abs_yaw_jerk_rps3 = 0.0
        try:
            max_abs_lateral_position_m = float(
                item.get("phase3_vehicle_dynamics_max_abs_lateral_position_m", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            max_abs_lateral_position_m = 0.0
        try:
            min_road_grade_percent = float(item.get("phase3_vehicle_dynamics_min_road_grade_percent", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_road_grade_percent = 0.0
        try:
            max_road_grade_percent = float(item.get("phase3_vehicle_dynamics_max_road_grade_percent", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_road_grade_percent = 0.0
        try:
            max_abs_grade_force_n = float(item.get("phase3_vehicle_dynamics_max_abs_grade_force_n", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_abs_grade_force_n = 0.0
        try:
            control_overlap_ratio = float(item.get("phase3_vehicle_control_throttle_brake_overlap_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            control_overlap_ratio = 0.0
        try:
            control_steering_rate_degps = float(
                item.get("phase3_vehicle_control_max_abs_steering_rate_degps", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            control_steering_rate_degps = 0.0
        try:
            control_throttle_plus_brake = float(
                item.get("phase3_vehicle_control_max_throttle_plus_brake", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            control_throttle_plus_brake = 0.0
        try:
            speed_tracking_error_mps = float(item.get("phase3_vehicle_speed_tracking_error_mps_max", 0.0) or 0.0)
        except (TypeError, ValueError):
            speed_tracking_error_mps = 0.0
        try:
            speed_tracking_abs_error_mps = float(
                item.get("phase3_vehicle_speed_tracking_error_abs_mps_max", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            speed_tracking_abs_error_mps = 0.0
        delta_speed_mps = final_speed_mps - initial_speed_mps
        delta_position_m = final_position_m - initial_position_m
        final_heading_abs_deg = abs(final_heading_deg)
        final_lateral_position_abs_m = abs(final_lateral_position_m)
        delta_heading_abs_deg = abs(final_heading_deg - initial_heading_deg)
        delta_lateral_position_abs_m = abs(final_lateral_position_m - initial_lateral_position_m)
        delta_yaw_rate_abs_rps = abs(final_yaw_rate_rps - initial_yaw_rate_rps)
        road_grade_abs_percent = max(abs(min_road_grade_percent), abs(max_road_grade_percent))

        _append_violation(
            batch_id=batch_id,
            metric="final_speed_mps",
            value=final_speed_mps,
            warn_max=speed_warn_max,
            hold_max=speed_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="final_position_m",
            value=final_position_m,
            warn_max=position_warn_max,
            hold_max=position_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="delta_speed_mps",
            value=delta_speed_mps,
            warn_max=delta_speed_warn_max,
            hold_max=delta_speed_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="delta_position_m",
            value=delta_position_m,
            warn_max=delta_position_warn_max,
            hold_max=delta_position_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="final_heading_abs_deg",
            value=final_heading_abs_deg,
            warn_max=final_heading_abs_warn_max,
            hold_max=final_heading_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="final_lateral_position_abs_m",
            value=final_lateral_position_abs_m,
            warn_max=final_lateral_position_abs_warn_max,
            hold_max=final_lateral_position_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="delta_heading_abs_deg",
            value=delta_heading_abs_deg,
            warn_max=delta_heading_abs_warn_max,
            hold_max=delta_heading_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="delta_lateral_position_abs_m",
            value=delta_lateral_position_abs_m,
            warn_max=delta_lateral_position_abs_warn_max,
            hold_max=delta_lateral_position_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="yaw_rate_abs_rps",
            value=max_abs_yaw_rate_rps,
            warn_max=yaw_rate_abs_warn_max,
            hold_max=yaw_rate_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="delta_yaw_rate_abs_rps",
            value=delta_yaw_rate_abs_rps,
            warn_max=delta_yaw_rate_abs_warn_max,
            hold_max=delta_yaw_rate_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="lateral_velocity_abs_mps",
            value=max_abs_lateral_velocity_mps,
            warn_max=lateral_velocity_abs_warn_max,
            hold_max=lateral_velocity_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="accel_abs_mps2",
            value=max_abs_accel_mps2,
            warn_max=accel_abs_warn_max,
            hold_max=accel_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="lateral_accel_abs_mps2",
            value=max_abs_lateral_accel_mps2,
            warn_max=lateral_accel_abs_warn_max,
            hold_max=lateral_accel_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="yaw_accel_abs_rps2",
            value=max_abs_yaw_accel_rps2,
            warn_max=yaw_accel_abs_warn_max,
            hold_max=yaw_accel_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="jerk_abs_mps3",
            value=max_abs_jerk_mps3,
            warn_max=jerk_abs_warn_max,
            hold_max=jerk_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="lateral_jerk_abs_mps3",
            value=max_abs_lateral_jerk_mps3,
            warn_max=lateral_jerk_abs_warn_max,
            hold_max=lateral_jerk_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="yaw_jerk_abs_rps3",
            value=max_abs_yaw_jerk_rps3,
            warn_max=yaw_jerk_abs_warn_max,
            hold_max=yaw_jerk_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="lateral_position_abs_m",
            value=max_abs_lateral_position_m,
            warn_max=lateral_position_abs_warn_max,
            hold_max=lateral_position_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="road_grade_abs_percent",
            value=road_grade_abs_percent,
            warn_max=road_grade_abs_warn_max,
            hold_max=road_grade_abs_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="grade_force_n",
            value=max_abs_grade_force_n,
            warn_max=grade_force_warn_max,
            hold_max=grade_force_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="control_overlap_ratio",
            value=control_overlap_ratio,
            warn_max=control_overlap_ratio_warn_max,
            hold_max=control_overlap_ratio_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="control_steering_rate_degps",
            value=control_steering_rate_degps,
            warn_max=control_steering_rate_warn_max,
            hold_max=control_steering_rate_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="control_throttle_plus_brake",
            value=control_throttle_plus_brake,
            warn_max=control_throttle_plus_brake_warn_max,
            hold_max=control_throttle_plus_brake_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="speed_tracking_error_mps",
            value=speed_tracking_error_mps,
            warn_max=speed_tracking_error_warn_max,
            hold_max=speed_tracking_error_hold_max,
        )
        _append_violation(
            batch_id=batch_id,
            metric="speed_tracking_abs_error_mps",
            value=speed_tracking_abs_error_mps,
            warn_max=speed_tracking_abs_error_warn_max,
            hold_max=speed_tracking_abs_error_hold_max,
        )

    severity_rank = {"HOLD": 0, "WARN": 1}
    rows.sort(
        key=lambda row: (
            severity_rank.get(str(row.get("severity", "")).upper(), 2),
            str(row.get("metric", "")),
            -(
                float(row.get("value", 0.0))
                - float(row.get("threshold", 0.0))
            ),
            str(row.get("batch_id", "")),
        )
    )
    return rows


def _format_phase3_vehicle_dynamics_violation_rows(rows: list[dict[str, Any]], *, max_items: int = 8) -> str:
    if not rows:
        return "n/a"
    parts: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
        metric = str(row.get("metric", "")).strip() or "metric_unknown"
        severity = str(row.get("severity", "")).strip().upper() or "WARN"
        try:
            value = float(row.get("value", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        try:
            threshold = float(row.get("threshold", 0.0))
        except (TypeError, ValueError):
            threshold = 0.0
        parts.append(f"{severity}:{metric}={value:.3f}>{threshold:.3f}({batch_id})")

    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        parts.append(f"...(+{remaining} more)")
    return "; ".join(parts) if parts else "n/a"


def _summarize_phase3_vehicle_dynamics_violation_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        severity = str(row.get("severity", "")).strip().upper()
        metric = str(row.get("metric", "")).strip()
        if severity not in {"WARN", "HOLD"} or not metric:
            continue
        batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
        try:
            value = float(row.get("value", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        try:
            threshold = float(row.get("threshold", 0.0))
        except (TypeError, ValueError):
            threshold = 0.0
        exceedance = value - threshold

        summary_key = f"{severity}:{metric}"
        current = summary.get(summary_key)
        if current is None:
            summary[summary_key] = {
                "violation_count": 1,
                "threshold": threshold,
                "max_value": value,
                "max_exceedance": exceedance,
                "max_batch_id": batch_id,
            }
            continue

        current["violation_count"] = int(current.get("violation_count", 0)) + 1
        current_max_exceedance = float(current.get("max_exceedance", 0.0))
        current_max_batch_id = str(current.get("max_batch_id", "")).strip() or "batch_unknown"
        if exceedance > current_max_exceedance or (
            exceedance == current_max_exceedance and batch_id < current_max_batch_id
        ):
            current["max_value"] = value
            current["max_exceedance"] = exceedance
            current["max_batch_id"] = batch_id

    return {summary_key: summary[summary_key] for summary_key in sorted(summary.keys())}


def _format_phase3_vehicle_dynamics_violation_summary(summary: dict[str, Any]) -> str:
    if not isinstance(summary, dict) or not summary:
        return "n/a"
    parts: list[str] = []
    for summary_key in sorted(summary.keys()):
        row = summary.get(summary_key)
        if not isinstance(row, dict):
            continue
        violation_count = int(row.get("violation_count", 0))
        try:
            max_value = float(row.get("max_value", 0.0))
        except (TypeError, ValueError):
            max_value = 0.0
        try:
            threshold = float(row.get("threshold", 0.0))
        except (TypeError, ValueError):
            threshold = 0.0
        max_batch_id = str(row.get("max_batch_id", "")).strip() or "batch_unknown"
        parts.append(
            f"{summary_key}:count={violation_count},max={max_value:.3f},"
            f"threshold={threshold:.3f},batch={max_batch_id}"
        )
    return "; ".join(parts) if parts else "n/a"


def _phase3_metric_reason(metric: str, severity: str) -> str:
    metric_key = str(metric).strip()
    severity_key = str(severity).strip().lower()
    reason_prefix_map = {
        "final_speed_mps": "phase3_vehicle_final_speed_above",
        "final_position_m": "phase3_vehicle_final_position_above",
        "delta_speed_mps": "phase3_vehicle_delta_speed_above",
        "delta_position_m": "phase3_vehicle_delta_position_above",
        "final_heading_abs_deg": "phase3_vehicle_final_heading_abs_above",
        "final_lateral_position_abs_m": "phase3_vehicle_final_lateral_position_abs_above",
        "delta_heading_abs_deg": "phase3_vehicle_delta_heading_abs_above",
        "delta_lateral_position_abs_m": "phase3_vehicle_delta_lateral_position_abs_above",
        "yaw_rate_abs_rps": "phase3_vehicle_yaw_rate_abs_above",
        "delta_yaw_rate_abs_rps": "phase3_vehicle_delta_yaw_rate_abs_above",
        "lateral_velocity_abs_mps": "phase3_vehicle_lateral_velocity_abs_above",
        "accel_abs_mps2": "phase3_vehicle_accel_abs_above",
        "lateral_accel_abs_mps2": "phase3_vehicle_lateral_accel_abs_above",
        "yaw_accel_abs_rps2": "phase3_vehicle_yaw_accel_abs_above",
        "jerk_abs_mps3": "phase3_vehicle_jerk_abs_above",
        "lateral_jerk_abs_mps3": "phase3_vehicle_lateral_jerk_abs_above",
        "yaw_jerk_abs_rps3": "phase3_vehicle_yaw_jerk_abs_above",
        "lateral_position_abs_m": "phase3_vehicle_lateral_position_abs_above",
        "road_grade_abs_percent": "phase3_vehicle_road_grade_abs_above",
        "grade_force_n": "phase3_vehicle_grade_force_above",
        "control_overlap_ratio": "phase3_vehicle_control_overlap_ratio_above",
        "control_steering_rate_degps": "phase3_vehicle_control_steering_rate_above",
        "control_throttle_plus_brake": "phase3_vehicle_control_throttle_plus_brake_above",
        "speed_tracking_error_mps": "phase3_vehicle_speed_tracking_error_above",
        "speed_tracking_abs_error_mps": "phase3_vehicle_speed_tracking_abs_error_above",
    }
    reason_prefix = reason_prefix_map.get(metric_key, "")
    if not reason_prefix or severity_key not in {"warn", "hold"}:
        return ""
    return f"{reason_prefix}_{severity_key}_max"


def _select_phase3_violation_fallback_rows(
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    severity_rank = {"HOLD": 0, "WARN": 1}
    selected_by_metric: dict[str, dict[str, Any]] = {}
    for summary_key in sorted(summary.keys()):
        row = summary.get(summary_key)
        if not isinstance(row, dict):
            continue
        severity, metric = ("", "")
        if ":" in summary_key:
            severity, metric = summary_key.split(":", 1)
        severity = str(severity).strip().upper()
        metric = str(metric).strip()
        if severity not in {"WARN", "HOLD"} or not metric:
            continue
        try:
            max_value = float(row.get("max_value", 0.0))
        except (TypeError, ValueError):
            max_value = 0.0
        try:
            threshold = float(row.get("threshold", 0.0))
        except (TypeError, ValueError):
            threshold = 0.0
        batch_id = str(row.get("max_batch_id", "")).strip() or "batch_unknown"
        candidate = {
            "severity": severity,
            "metric": metric,
            "max_value": max_value,
            "threshold": threshold,
            "max_batch_id": batch_id,
            "max_exceedance": max_value - threshold,
        }
        current = selected_by_metric.get(metric)
        if current is None:
            selected_by_metric[metric] = candidate
            continue
        current_rank = severity_rank.get(str(current.get("severity", "")).upper(), 2)
        candidate_rank = severity_rank.get(severity, 2)
        if candidate_rank < current_rank:
            selected_by_metric[metric] = candidate
            continue
        if candidate_rank > current_rank:
            continue
        current_exceedance = float(current.get("max_exceedance", 0.0))
        candidate_exceedance = float(candidate.get("max_exceedance", 0.0))
        if candidate_exceedance > current_exceedance:
            selected_by_metric[metric] = candidate
            continue
        if candidate_exceedance == current_exceedance and batch_id < str(
            current.get("max_batch_id", "batch_unknown")
        ):
            selected_by_metric[metric] = candidate

    return sorted(
        selected_by_metric.values(),
        key=lambda item: (
            severity_rank.get(str(item.get("severity", "")).upper(), 2),
            str(item.get("metric", "")),
        ),
    )


def _timing_warning_severity(score: float) -> str:
    if score >= 2.0:
        return "HIGH"
    if score >= 1.0:
        return "WARN"
    return ""


def _median_int(values: list[int]) -> int:
    if not values:
        return 0
    sorted_values = sorted(int(value) for value in values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return int(sorted_values[mid])
    return int(round((sorted_values[mid - 1] + sorted_values[mid]) / 2.0))


def _percentile(sorted_values: list[int], ratio: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    clamped = max(0.0, min(1.0, float(ratio)))
    pos = (len(sorted_values) - 1) * clamped
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo]) * (1.0 - frac) + float(sorted_values[hi]) * frac


def _apply_iqr_filter(values: list[int]) -> list[int]:
    if len(values) < 4:
        return list(values)
    sorted_values = sorted(int(value) for value in values)
    q1 = _percentile(sorted_values, 0.25)
    q3 = _percentile(sorted_values, 0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    filtered = [int(value) for value in values if lower <= int(value) <= upper]
    return filtered if filtered else list(values)


def _apply_trim_ratio(values: list[int], trim_ratio: float) -> list[int]:
    if not values:
        return []
    ratio = float(trim_ratio)
    if ratio <= 0.0:
        return list(values)
    sorted_values = sorted(int(value) for value in values)
    trim_each_side = int(len(sorted_values) * ratio)
    if trim_each_side <= 0:
        return list(values)
    if len(sorted_values) - (trim_each_side * 2) < 1:
        return list(values)

    remove_counts: dict[int, int] = {}
    for value in sorted_values[:trim_each_side]:
        key = int(value)
        remove_counts[key] = remove_counts.get(key, 0) + 1
    for value in sorted_values[-trim_each_side:]:
        key = int(value)
        remove_counts[key] = remove_counts.get(key, 0) + 1

    filtered: list[int] = []
    for raw in values:
        value = int(raw)
        if remove_counts.get(value, 0) > 0:
            remove_counts[value] = int(remove_counts[value]) - 1
            continue
        filtered.append(value)
    return filtered if filtered else list(values)


def _release_lineage_key(release_prefix: str) -> str:
    normalized = str(release_prefix).strip()
    if not normalized:
        return ""
    parts = [part for part in normalized.split("_") if part]
    if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
        return "_".join(parts[:-2])
    return normalized


def _release_sequence(release_prefix: str) -> tuple[int, int] | None:
    normalized = str(release_prefix).strip()
    if not normalized:
        return None
    parts = [part for part in normalized.split("_") if part]
    if len(parts) < 2:
        return None
    if not (parts[-1].isdigit() and parts[-2].isdigit()):
        return None
    return int(parts[-2]), int(parts[-1])


def _collect_history_timing_totals(
    *,
    history_dir: Path,
    exclude_path: Path,
    window: int,
    current_release_prefix: str,
) -> list[int]:
    if window <= 0 or not history_dir.is_dir():
        return []

    current_lineage = _release_lineage_key(current_release_prefix)
    current_sequence = _release_sequence(current_release_prefix)
    candidates: list[dict[str, Any]] = []
    exclude_resolved = exclude_path.resolve()
    for path in (candidate.resolve() for candidate in history_dir.glob("*_release_summary.json")):
        if path == exclude_resolved:
            continue
        try:
            payload = load_json_object(path, subject="history summary json")
        except Exception:
            continue
        timing = payload.get("timing_ms")
        if not isinstance(timing, dict):
            continue
        raw_total = timing.get("total")
        try:
            total_ms = int(raw_total)
        except (TypeError, ValueError):
            continue
        if total_ms <= 0:
            continue
        candidate_release_prefix = str(payload.get("release_prefix", "")).strip()
        candidate_lineage = _release_lineage_key(candidate_release_prefix) if candidate_release_prefix else ""
        if current_lineage:
            if not candidate_release_prefix:
                continue
            if candidate_lineage != current_lineage:
                continue
        candidate_sequence = _release_sequence(candidate_release_prefix) if candidate_release_prefix else None
        if (
            current_lineage
            and current_sequence is not None
            and candidate_lineage == current_lineage
            and candidate_sequence is not None
            and candidate_sequence >= current_sequence
        ):
            continue
        try:
            mtime_ns = int(path.stat().st_mtime_ns)
        except OSError:
            mtime_ns = 0
        candidates.append(
            {
                "path": str(path),
                "total_ms": int(total_ms),
                "mtime_ns": mtime_ns,
                "sequence": candidate_sequence,
            }
        )

    candidates.sort(
        key=lambda item: (
            1 if item.get("sequence") is not None else 0,
            int(item.get("sequence", (0, 0))[0]),
            int(item.get("sequence", (0, 0))[1]),
            int(item.get("mtime_ns", 0)),
            str(item.get("path", "")),
        ),
        reverse=True,
    )
    return [int(item.get("total_ms", 0)) for item in candidates[:window]]


def main() -> int:
    args = parse_args()
    max_codes = parse_positive_int(str(args.max_codes), default=12, field="max-codes")
    timing_total_warn_ms = parse_non_negative_int(
        str(args.timing_total_warn_ms),
        default=0,
        field="timing-total-warn-ms",
    )
    timing_regression_baseline_ms = parse_non_negative_int(
        str(args.timing_regression_baseline_ms),
        default=0,
        field="timing-regression-baseline-ms",
    )
    timing_regression_warn_ratio = parse_non_negative_float(
        str(args.timing_regression_warn_ratio),
        default=0.0,
        field="timing-regression-warn-ratio",
    )
    timing_regression_history_window = parse_non_negative_int(
        str(args.timing_regression_history_window),
        default=0,
        field="timing-regression-history-window",
    )
    timing_regression_history_trim_ratio = parse_non_negative_float(
        str(args.timing_regression_history_trim_ratio),
        default=0.0,
        field="timing-regression-history-trim-ratio",
    )
    if timing_regression_history_trim_ratio >= 0.5:
        raise ValueError("timing-regression-history-trim-ratio must be >= 0 and < 0.5")
    phase4_primary_warn_ratio = parse_non_negative_float(
        str(args.phase4_primary_warn_ratio),
        default=0.0,
        field="phase4-primary-warn-ratio",
    )
    if phase4_primary_warn_ratio > 1.0:
        raise ValueError("phase4-primary-warn-ratio must be between 0 and 1")
    phase4_primary_hold_ratio = parse_non_negative_float(
        str(args.phase4_primary_hold_ratio),
        default=0.0,
        field="phase4-primary-hold-ratio",
    )
    if phase4_primary_hold_ratio > 1.0:
        raise ValueError("phase4-primary-hold-ratio must be between 0 and 1")
    phase4_primary_module_warn_thresholds = parse_phase4_secondary_module_warn_thresholds(
        str(args.phase4_primary_module_warn_thresholds),
        field="phase4-primary-module-warn-thresholds",
    )
    phase4_primary_module_hold_thresholds = parse_phase4_secondary_module_warn_thresholds(
        str(args.phase4_primary_module_hold_thresholds),
        field="phase4-primary-module-hold-thresholds",
    )
    phase4_secondary_warn_ratio = parse_non_negative_float(
        str(args.phase4_secondary_warn_ratio),
        default=0.0,
        field="phase4-secondary-warn-ratio",
    )
    if phase4_secondary_warn_ratio > 1.0:
        raise ValueError("phase4-secondary-warn-ratio must be between 0 and 1")
    phase4_secondary_hold_ratio = parse_non_negative_float(
        str(args.phase4_secondary_hold_ratio),
        default=0.0,
        field="phase4-secondary-hold-ratio",
    )
    if phase4_secondary_hold_ratio > 1.0:
        raise ValueError("phase4-secondary-hold-ratio must be between 0 and 1")
    phase4_secondary_warn_min_modules = parse_positive_int(
        str(args.phase4_secondary_warn_min_modules),
        default=1,
        field="phase4-secondary-warn-min-modules",
    )
    phase4_secondary_module_warn_thresholds = parse_phase4_secondary_module_warn_thresholds(
        str(args.phase4_secondary_module_warn_thresholds),
        field="phase4-secondary-module-warn-thresholds",
    )
    phase4_secondary_module_hold_thresholds = parse_phase4_secondary_module_warn_thresholds(
        str(args.phase4_secondary_module_hold_thresholds),
        field="phase4-secondary-module-hold-thresholds",
    )
    phase3_vehicle_final_speed_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_speed_warn_max),
        default=0.0,
        field="phase3-vehicle-final-speed-warn-max",
    )
    phase3_vehicle_final_speed_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_speed_hold_max),
        default=0.0,
        field="phase3-vehicle-final-speed-hold-max",
    )
    phase3_vehicle_final_position_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_position_warn_max),
        default=0.0,
        field="phase3-vehicle-final-position-warn-max",
    )
    phase3_vehicle_final_position_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_position_hold_max),
        default=0.0,
        field="phase3-vehicle-final-position-hold-max",
    )
    phase3_vehicle_delta_speed_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_speed_warn_max),
        default=0.0,
        field="phase3-vehicle-delta-speed-warn-max",
    )
    phase3_vehicle_delta_speed_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_speed_hold_max),
        default=0.0,
        field="phase3-vehicle-delta-speed-hold-max",
    )
    phase3_vehicle_delta_position_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_position_warn_max),
        default=0.0,
        field="phase3-vehicle-delta-position-warn-max",
    )
    phase3_vehicle_delta_position_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_position_hold_max),
        default=0.0,
        field="phase3-vehicle-delta-position-hold-max",
    )
    phase3_vehicle_final_heading_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_heading_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-final-heading-abs-warn-max",
    )
    phase3_vehicle_final_heading_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_heading_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-final-heading-abs-hold-max",
    )
    phase3_vehicle_final_lateral_position_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_lateral_position_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-final-lateral-position-abs-warn-max",
    )
    phase3_vehicle_final_lateral_position_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_final_lateral_position_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-final-lateral-position-abs-hold-max",
    )
    phase3_vehicle_delta_heading_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_heading_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-delta-heading-abs-warn-max",
    )
    phase3_vehicle_delta_heading_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_heading_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-delta-heading-abs-hold-max",
    )
    phase3_vehicle_delta_lateral_position_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_lateral_position_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-delta-lateral-position-abs-warn-max",
    )
    phase3_vehicle_delta_lateral_position_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_lateral_position_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-delta-lateral-position-abs-hold-max",
    )
    phase3_vehicle_yaw_rate_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_yaw_rate_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-yaw-rate-abs-warn-max",
    )
    phase3_vehicle_yaw_rate_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_yaw_rate_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-yaw-rate-abs-hold-max",
    )
    phase3_vehicle_delta_yaw_rate_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_yaw_rate_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-delta-yaw-rate-abs-warn-max",
    )
    phase3_vehicle_delta_yaw_rate_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_delta_yaw_rate_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-delta-yaw-rate-abs-hold-max",
    )
    phase3_vehicle_lateral_velocity_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_velocity_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-lateral-velocity-abs-warn-max",
    )
    phase3_vehicle_lateral_velocity_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_velocity_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-lateral-velocity-abs-hold-max",
    )
    phase3_vehicle_accel_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_accel_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-accel-abs-warn-max",
    )
    phase3_vehicle_accel_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_accel_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-accel-abs-hold-max",
    )
    phase3_vehicle_lateral_accel_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_accel_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-lateral-accel-abs-warn-max",
    )
    phase3_vehicle_lateral_accel_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_accel_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-lateral-accel-abs-hold-max",
    )
    phase3_vehicle_yaw_accel_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_yaw_accel_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-yaw-accel-abs-warn-max",
    )
    phase3_vehicle_yaw_accel_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_yaw_accel_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-yaw-accel-abs-hold-max",
    )
    phase3_vehicle_jerk_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_jerk_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-jerk-abs-warn-max",
    )
    phase3_vehicle_jerk_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_jerk_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-jerk-abs-hold-max",
    )
    phase3_vehicle_lateral_jerk_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_jerk_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-lateral-jerk-abs-warn-max",
    )
    phase3_vehicle_lateral_jerk_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_jerk_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-lateral-jerk-abs-hold-max",
    )
    phase3_vehicle_yaw_jerk_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_yaw_jerk_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-yaw-jerk-abs-warn-max",
    )
    phase3_vehicle_yaw_jerk_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_yaw_jerk_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-yaw-jerk-abs-hold-max",
    )
    phase3_vehicle_lateral_position_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_position_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-lateral-position-abs-warn-max",
    )
    phase3_vehicle_lateral_position_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_lateral_position_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-lateral-position-abs-hold-max",
    )
    phase3_vehicle_road_grade_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_road_grade_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-road-grade-abs-warn-max",
    )
    phase3_vehicle_road_grade_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_road_grade_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-road-grade-abs-hold-max",
    )
    phase3_vehicle_grade_force_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_grade_force_warn_max),
        default=0.0,
        field="phase3-vehicle-grade-force-warn-max",
    )
    phase3_vehicle_grade_force_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_grade_force_hold_max),
        default=0.0,
        field="phase3-vehicle-grade-force-hold-max",
    )
    phase3_vehicle_control_overlap_ratio_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_control_overlap_ratio_warn_max),
        default=0.0,
        field="phase3-vehicle-control-overlap-ratio-warn-max",
    )
    if phase3_vehicle_control_overlap_ratio_warn_max > 1.0:
        raise ValueError("phase3-vehicle-control-overlap-ratio-warn-max must be <= 1")
    phase3_vehicle_control_overlap_ratio_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_control_overlap_ratio_hold_max),
        default=0.0,
        field="phase3-vehicle-control-overlap-ratio-hold-max",
    )
    if phase3_vehicle_control_overlap_ratio_hold_max > 1.0:
        raise ValueError("phase3-vehicle-control-overlap-ratio-hold-max must be <= 1")
    phase3_vehicle_control_steering_rate_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_control_steering_rate_warn_max),
        default=0.0,
        field="phase3-vehicle-control-steering-rate-warn-max",
    )
    phase3_vehicle_control_steering_rate_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_control_steering_rate_hold_max),
        default=0.0,
        field="phase3-vehicle-control-steering-rate-hold-max",
    )
    phase3_vehicle_control_throttle_plus_brake_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_control_throttle_plus_brake_warn_max),
        default=0.0,
        field="phase3-vehicle-control-throttle-plus-brake-warn-max",
    )
    phase3_vehicle_control_throttle_plus_brake_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_control_throttle_plus_brake_hold_max),
        default=0.0,
        field="phase3-vehicle-control-throttle-plus-brake-hold-max",
    )
    phase3_vehicle_speed_tracking_error_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_speed_tracking_error_warn_max),
        default=0.0,
        field="phase3-vehicle-speed-tracking-error-warn-max",
    )
    phase3_vehicle_speed_tracking_error_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_speed_tracking_error_hold_max),
        default=0.0,
        field="phase3-vehicle-speed-tracking-error-hold-max",
    )
    phase3_vehicle_speed_tracking_error_abs_warn_max = parse_non_negative_float(
        str(args.phase3_vehicle_speed_tracking_error_abs_warn_max),
        default=0.0,
        field="phase3-vehicle-speed-tracking-error-abs-warn-max",
    )
    phase3_vehicle_speed_tracking_error_abs_hold_max = parse_non_negative_float(
        str(args.phase3_vehicle_speed_tracking_error_abs_hold_max),
        default=0.0,
        field="phase3-vehicle-speed-tracking-error-abs-hold-max",
    )
    phase3_core_sim_min_ttc_same_lane_warn_min = parse_non_negative_float(
        str(args.phase3_core_sim_min_ttc_same_lane_warn_min),
        default=0.0,
        field="phase3-core-sim-min-ttc-same-lane-warn-min",
    )
    phase3_core_sim_min_ttc_same_lane_hold_min = parse_non_negative_float(
        str(args.phase3_core_sim_min_ttc_same_lane_hold_min),
        default=0.0,
        field="phase3-core-sim-min-ttc-same-lane-hold-min",
    )
    phase3_core_sim_min_ttc_any_lane_warn_min = parse_non_negative_float(
        str(args.phase3_core_sim_min_ttc_any_lane_warn_min),
        default=0.0,
        field="phase3-core-sim-min-ttc-any-lane-warn-min",
    )
    phase3_core_sim_min_ttc_any_lane_hold_min = parse_non_negative_float(
        str(args.phase3_core_sim_min_ttc_any_lane_hold_min),
        default=0.0,
        field="phase3-core-sim-min-ttc-any-lane-hold-min",
    )
    phase3_core_sim_collision_warn_max = parse_non_negative_int(
        str(args.phase3_core_sim_collision_warn_max),
        default=0,
        field="phase3-core-sim-collision-warn-max",
    )
    phase3_core_sim_collision_hold_max = parse_non_negative_int(
        str(args.phase3_core_sim_collision_hold_max),
        default=0,
        field="phase3-core-sim-collision-hold-max",
    )
    phase3_core_sim_timeout_warn_max = parse_non_negative_int(
        str(args.phase3_core_sim_timeout_warn_max),
        default=0,
        field="phase3-core-sim-timeout-warn-max",
    )
    phase3_core_sim_timeout_hold_max = parse_non_negative_int(
        str(args.phase3_core_sim_timeout_hold_max),
        default=0,
        field="phase3-core-sim-timeout-hold-max",
    )
    phase3_core_sim_gate_hold_warn_max = parse_non_negative_int(
        str(args.phase3_core_sim_gate_hold_warn_max),
        default=0,
        field="phase3-core-sim-gate-hold-warn-max",
    )
    phase3_core_sim_gate_hold_hold_max = parse_non_negative_int(
        str(args.phase3_core_sim_gate_hold_hold_max),
        default=0,
        field="phase3-core-sim-gate-hold-hold-max",
    )
    phase3_core_sim_matrix_min_ttc_same_lane_warn_min = parse_non_negative_float(
        str(args.phase3_core_sim_matrix_min_ttc_same_lane_warn_min),
        default=0.0,
        field="phase3-core-sim-matrix-min-ttc-same-lane-warn-min",
    )
    phase3_core_sim_matrix_min_ttc_same_lane_hold_min = parse_non_negative_float(
        str(args.phase3_core_sim_matrix_min_ttc_same_lane_hold_min),
        default=0.0,
        field="phase3-core-sim-matrix-min-ttc-same-lane-hold-min",
    )
    phase3_core_sim_matrix_min_ttc_any_lane_warn_min = parse_non_negative_float(
        str(args.phase3_core_sim_matrix_min_ttc_any_lane_warn_min),
        default=0.0,
        field="phase3-core-sim-matrix-min-ttc-any-lane-warn-min",
    )
    phase3_core_sim_matrix_min_ttc_any_lane_hold_min = parse_non_negative_float(
        str(args.phase3_core_sim_matrix_min_ttc_any_lane_hold_min),
        default=0.0,
        field="phase3-core-sim-matrix-min-ttc-any-lane-hold-min",
    )
    phase3_core_sim_matrix_failed_cases_warn_max = parse_non_negative_int(
        str(args.phase3_core_sim_matrix_failed_cases_warn_max),
        default=0,
        field="phase3-core-sim-matrix-failed-cases-warn-max",
    )
    phase3_core_sim_matrix_failed_cases_hold_max = parse_non_negative_int(
        str(args.phase3_core_sim_matrix_failed_cases_hold_max),
        default=0,
        field="phase3-core-sim-matrix-failed-cases-hold-max",
    )
    phase3_core_sim_matrix_collision_cases_warn_max = parse_non_negative_int(
        str(args.phase3_core_sim_matrix_collision_cases_warn_max),
        default=0,
        field="phase3-core-sim-matrix-collision-cases-warn-max",
    )
    phase3_core_sim_matrix_collision_cases_hold_max = parse_non_negative_int(
        str(args.phase3_core_sim_matrix_collision_cases_hold_max),
        default=0,
        field="phase3-core-sim-matrix-collision-cases-hold-max",
    )
    phase3_core_sim_matrix_timeout_cases_warn_max = parse_non_negative_int(
        str(args.phase3_core_sim_matrix_timeout_cases_warn_max),
        default=0,
        field="phase3-core-sim-matrix-timeout-cases-warn-max",
    )
    phase3_core_sim_matrix_timeout_cases_hold_max = parse_non_negative_int(
        str(args.phase3_core_sim_matrix_timeout_cases_hold_max),
        default=0,
        field="phase3-core-sim-matrix-timeout-cases-hold-max",
    )
    phase3_lane_risk_min_ttc_same_lane_warn_min = parse_non_negative_float(
        str(args.phase3_lane_risk_min_ttc_same_lane_warn_min),
        default=0.0,
        field="phase3-lane-risk-min-ttc-same-lane-warn-min",
    )
    phase3_lane_risk_min_ttc_same_lane_hold_min = parse_non_negative_float(
        str(args.phase3_lane_risk_min_ttc_same_lane_hold_min),
        default=0.0,
        field="phase3-lane-risk-min-ttc-same-lane-hold-min",
    )
    phase3_lane_risk_min_ttc_adjacent_lane_warn_min = parse_non_negative_float(
        str(args.phase3_lane_risk_min_ttc_adjacent_lane_warn_min),
        default=0.0,
        field="phase3-lane-risk-min-ttc-adjacent-lane-warn-min",
    )
    phase3_lane_risk_min_ttc_adjacent_lane_hold_min = parse_non_negative_float(
        str(args.phase3_lane_risk_min_ttc_adjacent_lane_hold_min),
        default=0.0,
        field="phase3-lane-risk-min-ttc-adjacent-lane-hold-min",
    )
    phase3_lane_risk_min_ttc_any_lane_warn_min = parse_non_negative_float(
        str(args.phase3_lane_risk_min_ttc_any_lane_warn_min),
        default=0.0,
        field="phase3-lane-risk-min-ttc-any-lane-warn-min",
    )
    phase3_lane_risk_min_ttc_any_lane_hold_min = parse_non_negative_float(
        str(args.phase3_lane_risk_min_ttc_any_lane_hold_min),
        default=0.0,
        field="phase3-lane-risk-min-ttc-any-lane-hold-min",
    )
    phase3_lane_risk_ttc_under_3s_same_lane_warn_max = parse_non_negative_int(
        str(args.phase3_lane_risk_ttc_under_3s_same_lane_warn_max),
        default=0,
        field="phase3-lane-risk-ttc-under-3s-same-lane-warn-max",
    )
    phase3_lane_risk_ttc_under_3s_same_lane_hold_max = parse_non_negative_int(
        str(args.phase3_lane_risk_ttc_under_3s_same_lane_hold_max),
        default=0,
        field="phase3-lane-risk-ttc-under-3s-same-lane-hold-max",
    )
    phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max = parse_non_negative_int(
        str(args.phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max),
        default=0,
        field="phase3-lane-risk-ttc-under-3s-adjacent-lane-warn-max",
    )
    phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max = parse_non_negative_int(
        str(args.phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max),
        default=0,
        field="phase3-lane-risk-ttc-under-3s-adjacent-lane-hold-max",
    )
    phase3_lane_risk_ttc_under_3s_any_lane_warn_max = parse_non_negative_int(
        str(args.phase3_lane_risk_ttc_under_3s_any_lane_warn_max),
        default=0,
        field="phase3-lane-risk-ttc-under-3s-any-lane-warn-max",
    )
    phase3_lane_risk_ttc_under_3s_any_lane_hold_max = parse_non_negative_int(
        str(args.phase3_lane_risk_ttc_under_3s_any_lane_hold_max),
        default=0,
        field="phase3-lane-risk-ttc-under-3s-any-lane-hold-max",
    )
    phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max = parse_non_negative_float(
        str(args.phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max),
        default=0.0,
        field="phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max",
    )
    if phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max > 1.0:
        raise ValueError("phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max must be between 0 and 1")
    phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max = parse_non_negative_float(
        str(args.phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max),
        default=0.0,
        field="phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max",
    )
    if phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max > 1.0:
        raise ValueError("phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max must be between 0 and 1")
    phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max = parse_non_negative_float(
        str(args.phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max),
        default=0.0,
        field="phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max",
    )
    if phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max > 1.0:
        raise ValueError("phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max must be between 0 and 1")
    phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max = parse_non_negative_float(
        str(args.phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max),
        default=0.0,
        field="phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max",
    )
    if phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max > 1.0:
        raise ValueError("phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max must be between 0 and 1")
    phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max = parse_non_negative_float(
        str(args.phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max),
        default=0.0,
        field="phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max",
    )
    if phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max > 1.0:
        raise ValueError("phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max must be between 0 and 1")
    phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max = parse_non_negative_float(
        str(args.phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max),
        default=0.0,
        field="phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max",
    )
    if phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max > 1.0:
        raise ValueError("phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max must be between 0 and 1")
    phase3_dataset_traffic_run_summary_warn_min = parse_non_negative_int(
        str(args.phase3_dataset_traffic_run_summary_warn_min),
        default=0,
        field="phase3-dataset-traffic-run-summary-warn-min",
    )
    phase3_dataset_traffic_run_summary_hold_min = parse_non_negative_int(
        str(args.phase3_dataset_traffic_run_summary_hold_min),
        default=0,
        field="phase3-dataset-traffic-run-summary-hold-min",
    )
    phase3_dataset_traffic_profile_count_warn_min = parse_non_negative_int(
        str(args.phase3_dataset_traffic_profile_count_warn_min),
        default=0,
        field="phase3-dataset-traffic-profile-count-warn-min",
    )
    phase3_dataset_traffic_profile_count_hold_min = parse_non_negative_int(
        str(args.phase3_dataset_traffic_profile_count_hold_min),
        default=0,
        field="phase3-dataset-traffic-profile-count-hold-min",
    )
    phase3_dataset_traffic_actor_pattern_count_warn_min = parse_non_negative_int(
        str(args.phase3_dataset_traffic_actor_pattern_count_warn_min),
        default=0,
        field="phase3-dataset-traffic-actor-pattern-count-warn-min",
    )
    phase3_dataset_traffic_actor_pattern_count_hold_min = parse_non_negative_int(
        str(args.phase3_dataset_traffic_actor_pattern_count_hold_min),
        default=0,
        field="phase3-dataset-traffic-actor-pattern-count-hold-min",
    )
    phase3_dataset_traffic_avg_npc_count_warn_min = parse_non_negative_float(
        str(args.phase3_dataset_traffic_avg_npc_count_warn_min),
        default=0.0,
        field="phase3-dataset-traffic-avg-npc-count-warn-min",
    )
    phase3_dataset_traffic_avg_npc_count_hold_min = parse_non_negative_float(
        str(args.phase3_dataset_traffic_avg_npc_count_hold_min),
        default=0.0,
        field="phase3-dataset-traffic-avg-npc-count-hold-min",
    )
    runtime_lane_execution_warn_min_exec_rows = parse_non_negative_int(
        str(args.runtime_lane_execution_warn_min_exec_rows),
        default=0,
        field="runtime-lane-execution-warn-min-exec-rows",
    )
    runtime_lane_execution_hold_min_exec_rows = parse_non_negative_int(
        str(args.runtime_lane_execution_hold_min_exec_rows),
        default=0,
        field="runtime-lane-execution-hold-min-exec-rows",
    )
    runtime_evidence_compare_warn_min_artifacts_with_diffs = parse_non_negative_int(
        str(args.runtime_evidence_compare_warn_min_artifacts_with_diffs),
        default=0,
        field="runtime-evidence-compare-warn-min-artifacts-with-diffs",
    )
    runtime_evidence_compare_hold_min_artifacts_with_diffs = parse_non_negative_int(
        str(args.runtime_evidence_compare_hold_min_artifacts_with_diffs),
        default=0,
        field="runtime-evidence-compare-hold-min-artifacts-with-diffs",
    )
    runtime_evidence_compare_warn_min_interop_import_mode_diff_count = parse_non_negative_int(
        str(args.runtime_evidence_compare_warn_min_interop_import_mode_diff_count),
        default=0,
        field="runtime-evidence-compare-warn-min-interop-import-mode-diff-count",
    )
    runtime_evidence_compare_hold_min_interop_import_mode_diff_count = parse_non_negative_int(
        str(args.runtime_evidence_compare_hold_min_interop_import_mode_diff_count),
        default=0,
        field="runtime-evidence-compare-hold-min-interop-import-mode-diff-count",
    )
    runtime_evidence_interop_contract_checked_warn_min = parse_non_negative_int(
        str(args.runtime_evidence_interop_contract_checked_warn_min),
        default=0,
        field="runtime-evidence-interop-contract-checked-warn-min",
    )
    runtime_evidence_interop_contract_checked_hold_min = parse_non_negative_int(
        str(args.runtime_evidence_interop_contract_checked_hold_min),
        default=0,
        field="runtime-evidence-interop-contract-checked-hold-min",
    )
    runtime_evidence_interop_contract_fail_warn_max = parse_non_negative_int(
        str(args.runtime_evidence_interop_contract_fail_warn_max),
        default=0,
        field="runtime-evidence-interop-contract-fail-warn-max",
    )
    runtime_evidence_interop_contract_fail_hold_max = parse_non_negative_int(
        str(args.runtime_evidence_interop_contract_fail_hold_max),
        default=0,
        field="runtime-evidence-interop-contract-fail-hold-max",
    )
    phase2_map_routing_unreachable_lanes_warn_max = parse_non_negative_int(
        str(args.phase2_map_routing_unreachable_lanes_warn_max),
        default=0,
        field="phase2-map-routing-unreachable-lanes-warn-max",
    )
    phase2_map_routing_unreachable_lanes_hold_max = parse_non_negative_int(
        str(args.phase2_map_routing_unreachable_lanes_hold_max),
        default=0,
        field="phase2-map-routing-unreachable-lanes-hold-max",
    )
    phase2_map_routing_non_reciprocal_links_warn_max = parse_non_negative_int(
        str(args.phase2_map_routing_non_reciprocal_links_warn_max),
        default=0,
        field="phase2-map-routing-non-reciprocal-links-warn-max",
    )
    phase2_map_routing_non_reciprocal_links_hold_max = parse_non_negative_int(
        str(args.phase2_map_routing_non_reciprocal_links_hold_max),
        default=0,
        field="phase2-map-routing-non-reciprocal-links-hold-max",
    )
    phase2_map_routing_continuity_gap_warn_max = parse_non_negative_int(
        str(args.phase2_map_routing_continuity_gap_warn_max),
        default=0,
        field="phase2-map-routing-continuity-gap-warn-max",
    )
    phase2_map_routing_continuity_gap_hold_max = parse_non_negative_int(
        str(args.phase2_map_routing_continuity_gap_hold_max),
        default=0,
        field="phase2-map-routing-continuity-gap-hold-max",
    )
    phase2_sensor_fidelity_score_avg_warn_min = parse_non_negative_float(
        str(args.phase2_sensor_fidelity_score_avg_warn_min),
        default=0.0,
        field="phase2-sensor-fidelity-score-avg-warn-min",
    )
    phase2_sensor_fidelity_score_avg_hold_min = parse_non_negative_float(
        str(args.phase2_sensor_fidelity_score_avg_hold_min),
        default=0.0,
        field="phase2-sensor-fidelity-score-avg-hold-min",
    )
    phase2_sensor_frame_count_avg_warn_min = parse_non_negative_float(
        str(args.phase2_sensor_frame_count_avg_warn_min),
        default=0.0,
        field="phase2-sensor-frame-count-avg-warn-min",
    )
    phase2_sensor_frame_count_avg_hold_min = parse_non_negative_float(
        str(args.phase2_sensor_frame_count_avg_hold_min),
        default=0.0,
        field="phase2-sensor-frame-count-avg-hold-min",
    )
    phase2_sensor_camera_noise_stddev_px_avg_warn_max = parse_non_negative_float(
        str(args.phase2_sensor_camera_noise_stddev_px_avg_warn_max),
        default=0.0,
        field="phase2-sensor-camera-noise-stddev-px-avg-warn-max",
    )
    phase2_sensor_camera_noise_stddev_px_avg_hold_max = parse_non_negative_float(
        str(args.phase2_sensor_camera_noise_stddev_px_avg_hold_max),
        default=0.0,
        field="phase2-sensor-camera-noise-stddev-px-avg-hold-max",
    )
    phase2_sensor_lidar_point_count_avg_warn_min = parse_non_negative_float(
        str(args.phase2_sensor_lidar_point_count_avg_warn_min),
        default=0.0,
        field="phase2-sensor-lidar-point-count-avg-warn-min",
    )
    phase2_sensor_lidar_point_count_avg_hold_min = parse_non_negative_float(
        str(args.phase2_sensor_lidar_point_count_avg_hold_min),
        default=0.0,
        field="phase2-sensor-lidar-point-count-avg-hold-min",
    )
    phase2_sensor_radar_false_positive_rate_avg_warn_max = parse_non_negative_float(
        str(args.phase2_sensor_radar_false_positive_rate_avg_warn_max),
        default=0.0,
        field="phase2-sensor-radar-false-positive-rate-avg-warn-max",
    )
    phase2_sensor_radar_false_positive_rate_avg_hold_max = parse_non_negative_float(
        str(args.phase2_sensor_radar_false_positive_rate_avg_hold_max),
        default=0.0,
        field="phase2-sensor-radar-false-positive-rate-avg-hold-max",
    )
    phase2_log_replay_fail_warn_max = parse_non_negative_int(
        str(args.phase2_log_replay_fail_warn_max),
        default=0,
        field="phase2-log-replay-fail-warn-max",
    )
    phase2_log_replay_fail_hold_max = parse_non_negative_int(
        str(args.phase2_log_replay_fail_hold_max),
        default=0,
        field="phase2-log-replay-fail-hold-max",
    )
    phase2_log_replay_missing_summary_warn_max = parse_non_negative_int(
        str(args.phase2_log_replay_missing_summary_warn_max),
        default=0,
        field="phase2-log-replay-missing-summary-warn-max",
    )
    phase2_log_replay_missing_summary_hold_max = parse_non_negative_int(
        str(args.phase2_log_replay_missing_summary_hold_max),
        default=0,
        field="phase2-log-replay-missing-summary-hold-max",
    )
    runtime_native_smoke_fail_warn_max = parse_non_negative_int(
        str(args.runtime_native_smoke_fail_warn_max),
        default=0,
        field="runtime-native-smoke-fail-warn-max",
    )
    runtime_native_smoke_fail_hold_max = parse_non_negative_int(
        str(args.runtime_native_smoke_fail_hold_max),
        default=0,
        field="runtime-native-smoke-fail-hold-max",
    )
    runtime_native_smoke_partial_warn_max = parse_non_negative_int(
        str(args.runtime_native_smoke_partial_warn_max),
        default=0,
        field="runtime-native-smoke-partial-warn-max",
    )
    runtime_native_smoke_partial_hold_max = parse_non_negative_int(
        str(args.runtime_native_smoke_partial_hold_max),
        default=0,
        field="runtime-native-smoke-partial-hold-max",
    )

    summary_path = Path(args.summary_json).resolve()
    out_path = Path(args.out_json).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary_payload = load_json_object(summary_path, subject="summary json")

    warning = str(summary_payload.get("warning", "")).strip()
    release_prefix = str(summary_payload.get("release_prefix", "")).strip()
    sds_versions = summary_payload.get("sds_versions", [])
    if not isinstance(sds_versions, list):
        sds_versions = []
    sds_versions_list = [str(item).strip() for item in sds_versions if str(item).strip()]
    threshold_drift_hold_policy_failure_detected = _as_bool_flag(
        summary_payload.get("threshold_drift_hold_policy_failure_detected", False)
    )
    threshold_drift_hold_policy_failures_raw = summary_payload.get(
        "threshold_drift_hold_policy_failures",
        [],
    )
    threshold_drift_hold_policy_failures = (
        [str(item).strip() for item in threshold_drift_hold_policy_failures_raw if str(item).strip()]
        if isinstance(threshold_drift_hold_policy_failures_raw, list)
        else []
    )
    threshold_drift_hold_policy_failure_reason_keys_raw = _coerce_string_list(
        summary_payload.get("threshold_drift_hold_policy_failure_reason_keys", [])
    )
    if not threshold_drift_hold_policy_failure_reason_keys_raw:
        threshold_drift_hold_policy_failure_reason_keys_raw = (
            _extract_threshold_drift_hold_policy_reason_keys_from_failures(
                threshold_drift_hold_policy_failures
            )
        )
    threshold_drift_hold_policy_failure_reason_keys = _dedupe_preserve_order(
        threshold_drift_hold_policy_failure_reason_keys_raw
    )
    threshold_drift_hold_policy_failure_reason_key_counts = _as_non_negative_int_map(
        summary_payload.get("threshold_drift_hold_policy_failure_reason_key_counts", {})
    )
    if not threshold_drift_hold_policy_failure_reason_key_counts:
        threshold_drift_hold_policy_failure_reason_key_counts = _count_string_values(
            threshold_drift_hold_policy_failure_reason_keys_raw
        )
    if not threshold_drift_hold_policy_failure_reason_keys and threshold_drift_hold_policy_failure_reason_key_counts:
        threshold_drift_hold_policy_failure_reason_keys = list(
            threshold_drift_hold_policy_failure_reason_key_counts.keys()
        )
    threshold_drift_hold_policy_failure_scope_counts = _as_non_negative_int_map(
        summary_payload.get("threshold_drift_hold_policy_failure_scope_counts", {})
    )
    if not threshold_drift_hold_policy_failure_scope_counts:
        threshold_drift_hold_policy_failure_scope_counts = _count_string_values(
            _extract_threshold_drift_hold_policy_scopes_from_failures(
                threshold_drift_hold_policy_failures
            )
        )
    threshold_drift_hold_policy_failure_scope_reason_key_counts = _as_non_negative_int_nested_map(
        summary_payload.get("threshold_drift_hold_policy_failure_scope_reason_key_counts", {})
    )
    if not threshold_drift_hold_policy_failure_scope_reason_key_counts:
        threshold_drift_hold_policy_failure_scope_reason_key_counts = (
            _extract_threshold_drift_hold_policy_scope_reason_key_counts_from_failures(
                threshold_drift_hold_policy_failures
            )
        )
    try:
        threshold_drift_hold_policy_failure_count = int(
            summary_payload.get(
                "threshold_drift_hold_policy_failure_count",
                len(threshold_drift_hold_policy_failures),
            )
            or 0
        )
    except (TypeError, ValueError):
        threshold_drift_hold_policy_failure_count = len(threshold_drift_hold_policy_failures)
    if threshold_drift_hold_policy_failure_count < 0:
        threshold_drift_hold_policy_failure_count = 0
    if threshold_drift_hold_policy_failure_count == 0 and threshold_drift_hold_policy_failures:
        threshold_drift_hold_policy_failure_count = len(threshold_drift_hold_policy_failures)
    if threshold_drift_hold_policy_failure_count > 0 or threshold_drift_hold_policy_failures:
        threshold_drift_hold_policy_failure_detected = True
    threshold_drift_hold_policy_failure_summary_text = str(
        summary_payload.get("threshold_drift_hold_policy_failure_summary_text", "")
    ).strip()
    if not threshold_drift_hold_policy_failure_summary_text:
        threshold_drift_hold_policy_failure_summary_text = (
            "; ".join(threshold_drift_hold_policy_failures)
            if threshold_drift_hold_policy_failures
            else "none"
        )
    threshold_drift_hold_policy_failures_text = (
        ",".join(_truncate_list(threshold_drift_hold_policy_failures, max_codes))
        if threshold_drift_hold_policy_failures
        else "n/a"
    )
    threshold_drift_hold_policy_failure_reason_keys_text = (
        ",".join(_truncate_list(threshold_drift_hold_policy_failure_reason_keys, max_codes))
        if threshold_drift_hold_policy_failure_reason_keys
        else "n/a"
    )
    threshold_drift_hold_policy_failure_reason_key_counts_text = _format_non_negative_int_counts(
        threshold_drift_hold_policy_failure_reason_key_counts
    )
    threshold_drift_hold_policy_failure_scope_counts_text = _format_non_negative_int_counts(
        threshold_drift_hold_policy_failure_scope_counts
    )
    threshold_drift_hold_policy_failure_scope_reason_key_counts_text = _format_non_negative_int_nested_counts(
        threshold_drift_hold_policy_failure_scope_reason_key_counts
    )

    final_counts = _as_counts(summary_payload.get("final_result_counts"))
    pipeline_overall_counts = _as_counts(summary_payload.get("pipeline_overall_counts"))
    pipeline_trend_counts = _as_counts(summary_payload.get("pipeline_trend_counts"))
    timing_ms = _as_non_negative_int_map(summary_payload.get("timing_ms"))
    timing_total_ms: int | None = timing_ms.get("total") if timing_ms else None
    slowest_stages_ms = _slowest_timing_stages(timing_ms, limit=3)
    slowest_stages_text = _format_slowest_timing(slowest_stages_ms)
    timing_regression_baseline_source = "explicit" if timing_regression_baseline_ms > 0 else "none"
    timing_regression_history_outlier_method = str(args.timing_regression_history_outlier_method).strip().lower()
    timing_regression_history_samples_raw_ms: list[int] = []
    timing_regression_history_samples_ms: list[int] = []
    timing_regression_history_filter: dict[str, Any] = {
        "release_prefix": release_prefix,
        "release_lineage": _release_lineage_key(release_prefix),
        "release_sequence": list(_release_sequence(release_prefix) or ()),
        "outlier_method": timing_regression_history_outlier_method,
        "trim_ratio": timing_regression_history_trim_ratio,
    }
    if timing_regression_baseline_ms <= 0:
        summary_baseline = int(summary_payload.get("timing_regression_baseline_ms", 0) or 0)
        if summary_baseline > 0:
            timing_regression_baseline_ms = summary_baseline
            timing_regression_baseline_source = "summary_json"
    if timing_regression_baseline_ms <= 0 and timing_regression_history_window > 0:
        history_dir_raw = str(args.timing_regression_history_dir).strip()
        history_dir = Path(history_dir_raw).resolve() if history_dir_raw else summary_path.parent
        timing_regression_history_samples_raw_ms = _collect_history_timing_totals(
            history_dir=history_dir,
            exclude_path=summary_path,
            window=timing_regression_history_window,
            current_release_prefix=release_prefix,
        )
        timing_regression_history_samples_ms = list(timing_regression_history_samples_raw_ms)
        if timing_regression_history_outlier_method == "iqr":
            timing_regression_history_samples_ms = _apply_iqr_filter(timing_regression_history_samples_ms)
        timing_regression_history_samples_ms = _apply_trim_ratio(
            timing_regression_history_samples_ms,
            timing_regression_history_trim_ratio,
        )
        timing_regression_history_filter["raw_count"] = len(timing_regression_history_samples_raw_ms)
        timing_regression_history_filter["used_count"] = len(timing_regression_history_samples_ms)
        timing_regression_history_filter["dropped_count"] = max(
            0,
            len(timing_regression_history_samples_raw_ms) - len(timing_regression_history_samples_ms),
        )
        if timing_regression_history_samples_ms:
            timing_regression_baseline_ms = _median_int(timing_regression_history_samples_ms)
            timing_regression_baseline_source = "history_median"
        else:
            timing_regression_baseline_source = "history_empty"
    if timing_regression_warn_ratio <= 0:
        timing_regression_warn_ratio = float(summary_payload.get("timing_regression_warn_ratio", 0.0) or 0.0)
    pipeline_manifest_count = int(summary_payload.get("pipeline_manifest_count", 0) or 0)
    summary_count = int(summary_payload.get("summary_count", 0) or 0)
    pipeline_manifests_raw = summary_payload.get("pipeline_manifests", [])
    pipeline_manifests = pipeline_manifests_raw if isinstance(pipeline_manifests_raw, list) else []
    phase4_primary_coverage_summary_raw = summary_payload.get("phase4_primary_coverage_summary", {})
    phase4_primary_coverage_summary = (
        phase4_primary_coverage_summary_raw
        if isinstance(phase4_primary_coverage_summary_raw, dict)
        else {}
    )
    phase4_secondary_coverage_summary_raw = summary_payload.get("phase4_secondary_coverage_summary", {})
    phase4_secondary_coverage_summary = (
        phase4_secondary_coverage_summary_raw
        if isinstance(phase4_secondary_coverage_summary_raw, dict)
        else {}
    )
    phase3_vehicle_dynamics_summary_raw = summary_payload.get("phase3_vehicle_dynamics_summary", {})
    phase3_vehicle_dynamics_summary = (
        phase3_vehicle_dynamics_summary_raw
        if isinstance(phase3_vehicle_dynamics_summary_raw, dict)
        else {}
    )
    phase3_vehicle_dynamics_summary_text = _format_phase3_vehicle_dynamics_summary(
        phase3_vehicle_dynamics_summary
    )
    phase3_core_sim_summary_raw = summary_payload.get("phase3_core_sim_summary", {})
    phase3_core_sim_summary = (
        phase3_core_sim_summary_raw
        if isinstance(phase3_core_sim_summary_raw, dict)
        else {}
    )
    phase3_core_sim_summary_text = _format_phase3_core_sim_summary(phase3_core_sim_summary)
    phase3_core_sim_matrix_summary_raw = summary_payload.get("phase3_core_sim_matrix_summary", {})
    phase3_core_sim_matrix_summary = (
        phase3_core_sim_matrix_summary_raw
        if isinstance(phase3_core_sim_matrix_summary_raw, dict)
        else {}
    )
    phase3_core_sim_matrix_summary_text = _format_phase3_core_sim_matrix_summary(
        phase3_core_sim_matrix_summary
    )
    phase3_core_sim_gate_min_ttc_same_lane_sec_counts = _as_non_negative_int_map(
        phase3_core_sim_summary.get("gate_min_ttc_same_lane_sec_counts", {})
    )
    phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text = (
        _format_threshold_counts(phase3_core_sim_gate_min_ttc_same_lane_sec_counts)
        if phase3_core_sim_gate_min_ttc_same_lane_sec_counts
        else "n/a"
    )
    phase3_core_sim_gate_min_ttc_any_lane_sec_counts = _as_non_negative_int_map(
        phase3_core_sim_summary.get("gate_min_ttc_any_lane_sec_counts", {})
    )
    phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text = (
        _format_threshold_counts(phase3_core_sim_gate_min_ttc_any_lane_sec_counts)
        if phase3_core_sim_gate_min_ttc_any_lane_sec_counts
        else "n/a"
    )
    phase3_lane_risk_summary_raw = summary_payload.get("phase3_lane_risk_summary", {})
    phase3_lane_risk_summary = (
        phase3_lane_risk_summary_raw
        if isinstance(phase3_lane_risk_summary_raw, dict)
        else {}
    )
    phase3_lane_risk_summary_text = _format_phase3_lane_risk_summary(phase3_lane_risk_summary)
    phase3_lane_risk_gate_min_ttc_same_lane_sec_counts = _as_non_negative_int_map(
        phase3_lane_risk_summary.get("gate_min_ttc_same_lane_sec_counts", {})
    )
    phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text = (
        _format_threshold_counts(phase3_lane_risk_gate_min_ttc_same_lane_sec_counts)
        if phase3_lane_risk_gate_min_ttc_same_lane_sec_counts
        else "n/a"
    )
    phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts = _as_non_negative_int_map(
        phase3_lane_risk_summary.get("gate_min_ttc_adjacent_lane_sec_counts", {})
    )
    phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text = (
        _format_threshold_counts(phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts)
        if phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts
        else "n/a"
    )
    phase3_lane_risk_gate_min_ttc_any_lane_sec_counts = _as_non_negative_int_map(
        phase3_lane_risk_summary.get("gate_min_ttc_any_lane_sec_counts", {})
    )
    phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text = (
        _format_threshold_counts(phase3_lane_risk_gate_min_ttc_any_lane_sec_counts)
        if phase3_lane_risk_gate_min_ttc_any_lane_sec_counts
        else "n/a"
    )
    phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts = _as_non_negative_int_map(
        phase3_lane_risk_summary.get("gate_max_ttc_under_3s_same_lane_total_counts", {})
    )
    phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text = (
        _format_threshold_counts(phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts)
        if phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts
        else "n/a"
    )
    phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts = _as_non_negative_int_map(
        phase3_lane_risk_summary.get("gate_max_ttc_under_3s_adjacent_lane_total_counts", {})
    )
    phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text = (
        _format_threshold_counts(phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts)
        if phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts
        else "n/a"
    )
    phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts = _as_non_negative_int_map(
        phase3_lane_risk_summary.get("gate_max_ttc_under_3s_any_lane_total_counts", {})
    )
    phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text = (
        _format_threshold_counts(phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts)
        if phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts
        else "n/a"
    )
    phase3_dataset_traffic_summary_raw = summary_payload.get("phase3_dataset_traffic_summary", {})
    phase3_dataset_traffic_summary = (
        phase3_dataset_traffic_summary_raw
        if isinstance(phase3_dataset_traffic_summary_raw, dict)
        else {}
    )
    phase3_dataset_traffic_summary_text = _format_phase3_dataset_traffic_summary(
        phase3_dataset_traffic_summary
    )
    phase3_dataset_traffic_gate_min_run_summary_count_counts = _as_non_negative_int_map(
        phase3_dataset_traffic_summary.get("gate_min_run_summary_count_counts", {})
    )
    phase3_dataset_traffic_gate_min_run_summary_count_counts_text = (
        _format_threshold_counts(phase3_dataset_traffic_gate_min_run_summary_count_counts)
        if phase3_dataset_traffic_gate_min_run_summary_count_counts
        else "n/a"
    )
    phase3_dataset_traffic_gate_min_traffic_profile_count_counts = _as_non_negative_int_map(
        phase3_dataset_traffic_summary.get("gate_min_traffic_profile_count_counts", {})
    )
    phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text = (
        _format_threshold_counts(phase3_dataset_traffic_gate_min_traffic_profile_count_counts)
        if phase3_dataset_traffic_gate_min_traffic_profile_count_counts
        else "n/a"
    )
    phase3_dataset_traffic_gate_min_actor_pattern_count_counts = _as_non_negative_int_map(
        phase3_dataset_traffic_summary.get("gate_min_actor_pattern_count_counts", {})
    )
    phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text = (
        _format_threshold_counts(phase3_dataset_traffic_gate_min_actor_pattern_count_counts)
        if phase3_dataset_traffic_gate_min_actor_pattern_count_counts
        else "n/a"
    )
    phase3_dataset_traffic_gate_min_avg_npc_count_counts = _as_non_negative_int_map(
        phase3_dataset_traffic_summary.get("gate_min_avg_npc_count_counts", {})
    )
    phase3_dataset_traffic_gate_min_avg_npc_count_counts_text = (
        _format_threshold_counts(phase3_dataset_traffic_gate_min_avg_npc_count_counts)
        if phase3_dataset_traffic_gate_min_avg_npc_count_counts
        else "n/a"
    )
    phase2_log_replay_summary_raw = summary_payload.get("phase2_log_replay_summary", {})
    phase2_log_replay_summary = (
        phase2_log_replay_summary_raw
        if isinstance(phase2_log_replay_summary_raw, dict)
        else {}
    )
    phase2_log_replay_summary_text = _format_phase2_log_replay_summary(phase2_log_replay_summary)
    phase2_map_routing_summary_raw = summary_payload.get("phase2_map_routing_summary", {})
    phase2_map_routing_summary = (
        phase2_map_routing_summary_raw
        if isinstance(phase2_map_routing_summary_raw, dict)
        else {}
    )
    phase2_map_routing_summary_text = _format_phase2_map_routing_summary(phase2_map_routing_summary)
    phase2_sensor_fidelity_summary_raw = summary_payload.get("phase2_sensor_fidelity_summary", {})
    phase2_sensor_fidelity_summary = (
        phase2_sensor_fidelity_summary_raw
        if isinstance(phase2_sensor_fidelity_summary_raw, dict)
        else {}
    )
    phase2_sensor_fidelity_summary_text = _format_phase2_sensor_fidelity_summary(phase2_sensor_fidelity_summary)
    runtime_native_smoke_summary_raw = summary_payload.get("runtime_native_smoke_summary", {})
    runtime_native_smoke_summary = (
        runtime_native_smoke_summary_raw
        if isinstance(runtime_native_smoke_summary_raw, dict)
        else {}
    )
    runtime_native_smoke_summary_text = _format_runtime_native_smoke_summary(runtime_native_smoke_summary)
    runtime_native_summary_compare_summary_raw = summary_payload.get(
        "runtime_native_summary_compare_summary",
        {},
    )
    runtime_native_summary_compare_summary = (
        runtime_native_summary_compare_summary_raw
        if isinstance(runtime_native_summary_compare_summary_raw, dict)
        else {}
    )
    runtime_native_summary_compare_summary_text = _format_runtime_native_summary_compare_summary(
        runtime_native_summary_compare_summary
    )
    runtime_native_evidence_compare_summary_raw = summary_payload.get(
        "runtime_native_evidence_compare_summary",
        {},
    )
    runtime_native_evidence_compare_summary = (
        runtime_native_evidence_compare_summary_raw
        if isinstance(runtime_native_evidence_compare_summary_raw, dict)
        else {}
    )
    runtime_native_evidence_compare_summary_text = _format_runtime_evidence_compare_summary(
        runtime_native_evidence_compare_summary
    )
    runtime_native_evidence_compare_interop_import_mode_diff_counts_text = (
        _format_runtime_evidence_compare_interop_import_mode_diff_counts(
            runtime_native_evidence_compare_summary
        )
    )
    runtime_native_evidence_compare_interop_import_mode_diff_count_total = (
        _runtime_evidence_compare_interop_import_mode_diff_count_total(
            runtime_native_evidence_compare_summary
        )
    )
    runtime_evidence_summary_raw = summary_payload.get("runtime_evidence_summary", {})
    runtime_evidence_summary = (
        runtime_evidence_summary_raw if isinstance(runtime_evidence_summary_raw, dict) else {}
    )
    runtime_evidence_summary_text = _format_runtime_evidence_summary(runtime_evidence_summary)
    runtime_evidence_probe_args_summary_text = _format_runtime_evidence_probe_args_summary(
        runtime_evidence_summary
    )
    runtime_evidence_scenario_contract_summary_text = _format_runtime_evidence_scenario_contract_summary(
        runtime_evidence_summary
    )
    runtime_evidence_scene_result_summary_text = _format_runtime_evidence_scene_result_summary(
        runtime_evidence_summary
    )
    runtime_evidence_interop_contract_summary_text = _format_runtime_evidence_interop_contract_summary(
        runtime_evidence_summary
    )
    runtime_evidence_interop_export_summary_text = _format_runtime_evidence_interop_export_summary(
        runtime_evidence_summary
    )
    runtime_evidence_interop_import_summary_text = _format_runtime_evidence_interop_import_summary(
        runtime_evidence_summary
    )
    runtime_evidence_interop_import_modes_text = _format_runtime_evidence_interop_import_modes(
        runtime_evidence_summary
    )
    runtime_lane_execution_summary_raw = summary_payload.get("runtime_lane_execution_summary", {})
    runtime_lane_execution_summary = (
        runtime_lane_execution_summary_raw if isinstance(runtime_lane_execution_summary_raw, dict) else {}
    )
    runtime_lane_execution_summary_text = _format_runtime_lane_execution_summary(runtime_lane_execution_summary)
    runtime_lane_phase2_rig_sweep_radar_alignment_summary_raw = summary_payload.get(
        "runtime_lane_phase2_rig_sweep_radar_alignment_summary",
        {},
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_summary = (
        runtime_lane_phase2_rig_sweep_radar_alignment_summary_raw
        if isinstance(runtime_lane_phase2_rig_sweep_radar_alignment_summary_raw, dict)
        else {}
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_summary_text = (
        _format_runtime_lane_phase2_rig_sweep_radar_alignment_summary(
            runtime_lane_phase2_rig_sweep_radar_alignment_summary
        )
    )
    runtime_evidence_compare_summary_raw = summary_payload.get("runtime_evidence_compare_summary", {})
    runtime_evidence_compare_summary = (
        runtime_evidence_compare_summary_raw if isinstance(runtime_evidence_compare_summary_raw, dict) else {}
    )
    runtime_evidence_compare_summary_text = _format_runtime_evidence_compare_summary(
        runtime_evidence_compare_summary
    )
    runtime_evidence_compare_interop_import_mode_diff_counts_text = (
        _format_runtime_evidence_compare_interop_import_mode_diff_counts(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_mode_diff_count_total = (
        _runtime_evidence_compare_interop_import_mode_diff_count_total(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_field_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_field_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_label_pair_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_label_pair_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_profile_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_profile_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals = _as_float_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_totals", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals = _as_float_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_abs_totals", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair = _as_float_nested_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_totals_by_label_pair", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair = _as_float_nested_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile = _as_float_nested_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_totals_by_profile", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile = _as_float_nested_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_abs_totals_by_profile", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile = (
        _as_float_nested_map(
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile",
                {},
            )
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile = (
        _as_float_nested_map(
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile",
                {},
            )
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_positive_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_negative_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts = _as_non_negative_int_map(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_zero_counts", {})
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts = (
        _as_non_negative_int_map(
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspot_priority_counts",
                {},
            )
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts = (
        _as_non_negative_int_map(
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspot_action_counts",
                {},
            )
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts = (
        _as_non_negative_int_map(
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspot_reason_counts",
                {},
            )
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_counts_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_counts(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_breakdown_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_breakdown(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts(
            runtime_evidence_compare_summary
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw = (
        runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_delta_key_max_positive_records",
            [],
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw = (
        runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_delta_key_max_negative_records",
            [],
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records: list[dict[str, Any]] = []
    if isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw, list):
        for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw:
            if not isinstance(row, dict):
                continue
            left_label = str(row.get("left_label", "")).strip() or "left"
            right_label = str(row.get("right_label", "")).strip() or "right"
            profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
            numeric_key = str(row.get("numeric_key", "")).strip()
            if not numeric_key:
                continue
            delta_raw = row.get("delta")
            if delta_raw is None or isinstance(delta_raw, bool):
                continue
            try:
                delta_value = float(delta_raw)
            except (TypeError, ValueError):
                continue
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records.append(
                {
                    "left_label": left_label,
                    "right_label": right_label,
                    "profile_id": profile_id,
                    "numeric_key": numeric_key,
                    "delta": float(round(delta_value, 6)),
                    "delta_abs": float(round(abs(delta_value), 6)),
                }
            )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records.sort(
        key=lambda row: (
            -abs(float(row.get("delta", 0.0))),
            str(row.get("numeric_key", "")),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records: list[dict[str, Any]] = []
    if isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw, list):
        for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw:
            if not isinstance(row, dict):
                continue
            left_label = str(row.get("left_label", "")).strip() or "left"
            right_label = str(row.get("right_label", "")).strip() or "right"
            profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
            numeric_key = str(row.get("numeric_key", "")).strip()
            if not numeric_key:
                continue
            delta_raw = row.get("delta")
            if delta_raw is None or isinstance(delta_raw, bool):
                continue
            try:
                delta_value = float(delta_raw)
            except (TypeError, ValueError):
                continue
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records.append(
                {
                    "left_label": left_label,
                    "right_label": right_label,
                    "profile_id": profile_id,
                    "numeric_key": numeric_key,
                    "delta": float(round(delta_value, 6)),
                    "delta_abs": float(round(abs(delta_value), 6)),
                }
            )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records.sort(
        key=lambda row: (
            -abs(float(row.get("delta", 0.0))),
            str(row.get("numeric_key", "")),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes(
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records,
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records,
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_record_count = int(
        runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_record_count", 0) or 0
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count = int(
        runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count",
            0,
        )
        or 0
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw = (
        runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_delta_hotspots",
            [],
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw = (
        runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile",
            [],
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots: list[dict[str, Any]] = []
    if isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw, list):
        for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw:
            if not isinstance(row, dict):
                continue
            left_label = str(row.get("left_label", "")).strip() or "left"
            right_label = str(row.get("right_label", "")).strip() or "right"
            profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
            numeric_key = str(row.get("numeric_key", "")).strip()
            if not numeric_key:
                continue
            delta_raw = row.get("delta")
            if delta_raw is None or isinstance(delta_raw, bool):
                continue
            try:
                delta_value = float(delta_raw)
            except (TypeError, ValueError):
                continue
            delta_abs_raw = row.get("delta_abs")
            if delta_abs_raw is None or isinstance(delta_abs_raw, bool):
                delta_abs_value = abs(delta_value)
            else:
                try:
                    delta_abs_value = float(delta_abs_raw)
                except (TypeError, ValueError):
                    delta_abs_value = abs(delta_value)
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots.append(
                {
                    "left_label": left_label,
                    "right_label": right_label,
                    "profile_id": profile_id,
                    "numeric_key": numeric_key,
                    "delta": float(round(delta_value, 6)),
                    "delta_abs": float(round(abs(delta_abs_value), 6)),
                }
            )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots.sort(
        key=lambda row: (
            -float(row.get("delta_abs", 0.0)),
            str(row.get("numeric_key", "")),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
            float(row.get("delta", 0.0)),
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots(
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots,
            total_count=runtime_evidence_compare_interop_import_profile_diff_numeric_delta_record_count,
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile: list[
        dict[str, Any]
    ] = []
    if isinstance(
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw,
        list,
    ):
        for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw:
            if not isinstance(row, dict):
                continue
            left_label = str(row.get("left_label", "")).strip() or "left"
            right_label = str(row.get("right_label", "")).strip() or "right"
            profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
            numeric_key_count_raw = row.get("numeric_key_count", 0)
            if numeric_key_count_raw is None or isinstance(numeric_key_count_raw, bool):
                numeric_key_count = 0
            else:
                try:
                    numeric_key_count = max(0, int(numeric_key_count_raw))
                except (TypeError, ValueError):
                    numeric_key_count = 0
            delta_total_raw = row.get("delta_total")
            if delta_total_raw is None or isinstance(delta_total_raw, bool):
                delta_total_value = 0.0
            else:
                try:
                    delta_total_value = float(delta_total_raw)
                except (TypeError, ValueError):
                    delta_total_value = 0.0
            delta_abs_total_raw = row.get("delta_abs_total")
            if delta_abs_total_raw is None or isinstance(delta_abs_total_raw, bool):
                delta_abs_total_value = abs(delta_total_value)
            else:
                try:
                    delta_abs_total_value = abs(float(delta_abs_total_raw))
                except (TypeError, ValueError):
                    delta_abs_total_value = abs(delta_total_value)
            top_numeric_key = str(row.get("top_numeric_key", "")).strip() or "n/a"
            top_numeric_delta_raw = row.get("top_numeric_delta")
            if top_numeric_delta_raw is None or isinstance(top_numeric_delta_raw, bool):
                top_numeric_delta_value = 0.0
            else:
                try:
                    top_numeric_delta_value = float(top_numeric_delta_raw)
                except (TypeError, ValueError):
                    top_numeric_delta_value = 0.0
            top_numeric_delta_abs_raw = row.get("top_numeric_delta_abs")
            if top_numeric_delta_abs_raw is None or isinstance(top_numeric_delta_abs_raw, bool):
                top_numeric_delta_abs_value = abs(top_numeric_delta_value)
            else:
                try:
                    top_numeric_delta_abs_value = abs(float(top_numeric_delta_abs_raw))
                except (TypeError, ValueError):
                    top_numeric_delta_abs_value = abs(top_numeric_delta_value)
            positive_delta_abs_total_raw = row.get("positive_delta_abs_total")
            if positive_delta_abs_total_raw is None or isinstance(positive_delta_abs_total_raw, bool):
                positive_delta_abs_total_value = 0.0
            else:
                try:
                    positive_delta_abs_total_value = abs(float(positive_delta_abs_total_raw))
                except (TypeError, ValueError):
                    positive_delta_abs_total_value = 0.0
            negative_delta_abs_total_raw = row.get("negative_delta_abs_total")
            if negative_delta_abs_total_raw is None or isinstance(negative_delta_abs_total_raw, bool):
                negative_delta_abs_total_value = 0.0
            else:
                try:
                    negative_delta_abs_total_value = abs(float(negative_delta_abs_total_raw))
                except (TypeError, ValueError):
                    negative_delta_abs_total_value = 0.0
            zero_numeric_key_count_raw = row.get("zero_numeric_key_count", 0)
            if zero_numeric_key_count_raw is None or isinstance(zero_numeric_key_count_raw, bool):
                zero_numeric_key_count = 0
            else:
                try:
                    zero_numeric_key_count = max(0, int(zero_numeric_key_count_raw))
                except (TypeError, ValueError):
                    zero_numeric_key_count = 0
            top_positive_numeric_key = str(row.get("top_positive_numeric_key", "")).strip()
            top_positive_delta_raw = row.get("top_positive_delta")
            if top_positive_delta_raw is None or isinstance(top_positive_delta_raw, bool):
                top_positive_delta_value = 0.0
            else:
                try:
                    top_positive_delta_value = float(top_positive_delta_raw)
                except (TypeError, ValueError):
                    top_positive_delta_value = 0.0
            top_negative_numeric_key = str(row.get("top_negative_numeric_key", "")).strip()
            top_negative_delta_raw = row.get("top_negative_delta")
            if top_negative_delta_raw is None or isinstance(top_negative_delta_raw, bool):
                top_negative_delta_value = 0.0
            else:
                try:
                    top_negative_delta_value = float(top_negative_delta_raw)
                except (TypeError, ValueError):
                    top_negative_delta_value = 0.0
            direction_imbalance_ratio_raw = row.get("direction_imbalance_ratio")
            if direction_imbalance_ratio_raw is None or isinstance(direction_imbalance_ratio_raw, bool):
                direction_imbalance_ratio_value = 0.0
            else:
                try:
                    direction_imbalance_ratio_value = max(
                        0.0,
                        min(1.0, float(direction_imbalance_ratio_raw)),
                    )
                except (TypeError, ValueError):
                    direction_imbalance_ratio_value = 0.0
            dominant_direction = str(row.get("dominant_direction", "")).strip().lower()
            if dominant_direction not in {"positive", "negative", "balanced"}:
                dominant_direction = "balanced"
            priority_score_raw = row.get("priority_score")
            if priority_score_raw is None or isinstance(priority_score_raw, bool):
                priority_score_value = 0.0
            else:
                try:
                    priority_score_value = float(priority_score_raw)
                except (TypeError, ValueError):
                    priority_score_value = 0.0
            priority_bucket = str(row.get("priority_bucket", "")).strip().lower()
            if priority_bucket not in {"high", "medium", "low"}:
                priority_bucket = "low"
            recommended_action = str(row.get("recommended_action", "")).strip()
            if not recommended_action:
                recommended_action = "monitor_for_recurrence"
            recommended_reason = str(row.get("recommended_reason", "")).strip()
            if not recommended_reason:
                recommended_reason = "unspecified_reason"
            recommended_checklist_raw = row.get("recommended_checklist", [])
            recommended_checklist = (
                [str(item).strip() for item in recommended_checklist_raw if str(item).strip()]
                if isinstance(recommended_checklist_raw, list)
                else []
            )
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile.append(
                {
                    "left_label": left_label,
                    "right_label": right_label,
                    "profile_id": profile_id,
                    "numeric_key_count": int(numeric_key_count),
                    "delta_total": float(round(delta_total_value, 6)),
                    "delta_abs_total": float(round(delta_abs_total_value, 6)),
                    "positive_delta_abs_total": float(round(positive_delta_abs_total_value, 6)),
                    "negative_delta_abs_total": float(round(negative_delta_abs_total_value, 6)),
                    "zero_numeric_key_count": int(zero_numeric_key_count),
                    "top_numeric_key": top_numeric_key,
                    "top_numeric_delta": float(round(top_numeric_delta_value, 6)),
                    "top_numeric_delta_abs": float(round(top_numeric_delta_abs_value, 6)),
                    "top_positive_numeric_key": top_positive_numeric_key,
                    "top_positive_delta": float(round(top_positive_delta_value, 6)),
                    "top_negative_numeric_key": top_negative_numeric_key,
                    "top_negative_delta": float(round(top_negative_delta_value, 6)),
                    "direction_imbalance_ratio": float(round(direction_imbalance_ratio_value, 6)),
                    "dominant_direction": dominant_direction,
                    "priority_score": float(round(priority_score_value, 6)),
                    "priority_bucket": priority_bucket,
                    "recommended_action": recommended_action,
                    "recommended_reason": recommended_reason,
                    "recommended_checklist": recommended_checklist,
                }
            )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile.sort(
        key=lambda row: (
            -float(row.get("priority_score", 0.0)),
            -float(row.get("delta_abs_total", 0.0)),
            -int(row.get("numeric_key_count", 0)),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile(
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile,
            total_count=runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count,
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text = (
        _format_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations(
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile,
            total_count=runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count,
        )
    )
    runtime_evidence_compare_interop_import_profile_diffs_raw = runtime_evidence_compare_summary.get(
        "interop_import_profile_diff_records",
        [],
    )
    runtime_evidence_compare_interop_import_profile_diffs: list[dict[str, Any]] = []
    if isinstance(runtime_evidence_compare_interop_import_profile_diffs_raw, list):
        for row in runtime_evidence_compare_interop_import_profile_diffs_raw:
            if not isinstance(row, dict):
                continue
            field_keys_raw = row.get("field_keys", [])
            numeric_keys_raw = row.get("numeric_keys", [])
            field_keys = (
                sorted({str(key).strip() for key in field_keys_raw if str(key).strip()})
                if isinstance(field_keys_raw, list)
                else []
            )
            numeric_keys = (
                sorted({str(key).strip() for key in numeric_keys_raw if str(key).strip()})
                if isinstance(numeric_keys_raw, list)
                else []
            )
            if not field_keys and not numeric_keys:
                continue
            runtime_evidence_compare_interop_import_profile_diffs.append(
                {
                    "left_label": str(row.get("left_label", "")).strip() or "left",
                    "right_label": str(row.get("right_label", "")).strip() or "right",
                    "profile_id": str(row.get("profile_id", "")).strip() or "profile_unknown",
                    "field_keys": field_keys,
                    "numeric_keys": numeric_keys,
                }
            )
    runtime_evidence_compare_interop_import_profile_diffs.sort(
        key=lambda row: (
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
            ",".join(str(item) for item in row.get("field_keys", [])),
            ",".join(str(item) for item in row.get("numeric_keys", [])),
        )
    )
    runtime_evidence_compare_interop_import_profile_diffs_text = (
        _format_runtime_evidence_compare_interop_import_profile_diffs(
            runtime_evidence_compare_interop_import_profile_diffs
        )
    )
    runtime_lane_execution_evidence_missing_runtime_counts_raw = runtime_lane_execution_summary.get(
        "runtime_evidence_missing_runtime_counts",
        {},
    )
    runtime_lane_execution_evidence_missing_runtime_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_evidence_missing_runtime_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_evidence_missing_runtime_counts_raw.items()):
            runtime_name = str(key).strip().lower()
            if not runtime_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_evidence_missing_runtime_counts[runtime_name] = count
    runtime_lane_execution_evidence_missing_runtimes_text = (
        ",".join(
            f"{runtime}:{runtime_lane_execution_evidence_missing_runtime_counts[runtime]}"
            for runtime in sorted(runtime_lane_execution_evidence_missing_runtime_counts)
        )
        if runtime_lane_execution_evidence_missing_runtime_counts
        else "n/a"
    )
    runtime_lane_execution_lane_row_counts_raw = runtime_lane_execution_summary.get("lane_row_counts", {})
    runtime_lane_execution_lane_row_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_lane_row_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_lane_row_counts_raw.items()):
            lane_name = str(key).strip().lower()
            if not lane_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_lane_row_counts[lane_name] = count
    if not runtime_lane_execution_lane_row_counts:
        runtime_lane_execution_lane_counts_raw = runtime_lane_execution_summary.get("lane_counts", {})
        if isinstance(runtime_lane_execution_lane_counts_raw, dict):
            for key, value in sorted(runtime_lane_execution_lane_counts_raw.items()):
                lane_name = str(key).strip().lower()
                if not lane_name:
                    continue
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                if count <= 0:
                    continue
                runtime_lane_execution_lane_row_counts[lane_name] = count
    runtime_lane_execution_lane_row_counts_text = (
        ",".join(
            f"{lane}:{runtime_lane_execution_lane_row_counts[lane]}"
            for lane in sorted(runtime_lane_execution_lane_row_counts)
        )
        if runtime_lane_execution_lane_row_counts
        else "n/a"
    )
    runtime_lane_execution_runner_platform_counts_raw = runtime_lane_execution_summary.get("runner_platform_counts", {})
    runtime_lane_execution_runner_platform_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_runner_platform_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_runner_platform_counts_raw.items()):
            platform_name = str(key).strip()
            if not platform_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_runner_platform_counts[platform_name] = count
    runtime_lane_execution_runner_platform_counts_text = (
        ",".join(
            f"{platform}:{runtime_lane_execution_runner_platform_counts[platform]}"
            for platform in sorted(runtime_lane_execution_runner_platform_counts)
        )
        if runtime_lane_execution_runner_platform_counts
        else "n/a"
    )
    runtime_lane_execution_sim_runtime_input_counts_raw = runtime_lane_execution_summary.get(
        "sim_runtime_input_counts",
        {},
    )
    runtime_lane_execution_sim_runtime_input_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_sim_runtime_input_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_sim_runtime_input_counts_raw.items()):
            runtime_name = str(key).strip().lower()
            if not runtime_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_sim_runtime_input_counts[runtime_name] = count
    runtime_lane_execution_sim_runtime_input_counts_text = (
        ",".join(
            f"{runtime}:{runtime_lane_execution_sim_runtime_input_counts[runtime]}"
            for runtime in sorted(runtime_lane_execution_sim_runtime_input_counts)
        )
        if runtime_lane_execution_sim_runtime_input_counts
        else "n/a"
    )
    runtime_lane_execution_dry_run_counts_raw = runtime_lane_execution_summary.get("dry_run_counts", {})
    runtime_lane_execution_dry_run_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_dry_run_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_dry_run_counts_raw.items()):
            value_name = str(key).strip() or "unknown"
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_dry_run_counts[value_name] = count
    runtime_lane_execution_dry_run_counts_text = (
        ",".join(
            f"{value}:{runtime_lane_execution_dry_run_counts[value]}"
            for value in sorted(runtime_lane_execution_dry_run_counts)
        )
        if runtime_lane_execution_dry_run_counts
        else "n/a"
    )
    runtime_lane_execution_continue_on_runtime_failure_counts_raw = runtime_lane_execution_summary.get(
        "continue_on_runtime_failure_counts",
        {},
    )
    runtime_lane_execution_continue_on_runtime_failure_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_continue_on_runtime_failure_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_continue_on_runtime_failure_counts_raw.items()):
            value_name = str(key).strip() or "unknown"
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_continue_on_runtime_failure_counts[value_name] = count
    runtime_lane_execution_continue_on_runtime_failure_counts_text = (
        ",".join(
            f"{value}:{runtime_lane_execution_continue_on_runtime_failure_counts[value]}"
            for value in sorted(runtime_lane_execution_continue_on_runtime_failure_counts)
        )
        if runtime_lane_execution_continue_on_runtime_failure_counts
        else "n/a"
    )
    runtime_lane_execution_exec_lane_warn_min_rows_counts_raw = runtime_lane_execution_summary.get(
        "runtime_exec_lane_warn_min_rows_counts",
        {},
    )
    runtime_lane_execution_exec_lane_warn_min_rows_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_exec_lane_warn_min_rows_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_exec_lane_warn_min_rows_counts_raw.items()):
            threshold_name = str(key).strip()
            if not threshold_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_exec_lane_warn_min_rows_counts[threshold_name] = count
    runtime_lane_execution_exec_lane_warn_min_rows_counts_text = (
        _format_threshold_counts(runtime_lane_execution_exec_lane_warn_min_rows_counts)
        if runtime_lane_execution_exec_lane_warn_min_rows_counts
        else "n/a"
    )
    runtime_lane_execution_exec_lane_hold_min_rows_counts_raw = runtime_lane_execution_summary.get(
        "runtime_exec_lane_hold_min_rows_counts",
        {},
    )
    runtime_lane_execution_exec_lane_hold_min_rows_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_exec_lane_hold_min_rows_counts_raw, dict):
        for key, value in sorted(runtime_lane_execution_exec_lane_hold_min_rows_counts_raw.items()):
            threshold_name = str(key).strip()
            if not threshold_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_exec_lane_hold_min_rows_counts[threshold_name] = count
    runtime_lane_execution_exec_lane_hold_min_rows_counts_text = (
        _format_threshold_counts(runtime_lane_execution_exec_lane_hold_min_rows_counts)
        if runtime_lane_execution_exec_lane_hold_min_rows_counts
        else "n/a"
    )
    runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_raw = (
        runtime_lane_execution_summary.get(
            "runtime_compare_warn_min_artifacts_with_diffs_counts",
            {},
        )
    )
    runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_raw, dict):
        for key, value in sorted(
            runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_raw.items()
        ):
            threshold_name = str(key).strip()
            if not threshold_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts[threshold_name] = count
    runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_text = (
        _format_threshold_counts(runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts)
        if runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts
        else "n/a"
    )
    runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_raw = (
        runtime_lane_execution_summary.get(
            "runtime_compare_hold_min_artifacts_with_diffs_counts",
            {},
        )
    )
    runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts: dict[str, int] = {}
    if isinstance(runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_raw, dict):
        for key, value in sorted(
            runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_raw.items()
        ):
            threshold_name = str(key).strip()
            if not threshold_name:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts[threshold_name] = count
    runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_text = (
        _format_threshold_counts(runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts)
        if runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_fidelity_score_avg_warn_min_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_raw
    )
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts)
        if runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_fidelity_score_avg_hold_min_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_raw
    )
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts)
        if runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_frame_count_avg_warn_min_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_raw
    )
    runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts)
        if runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_frame_count_avg_hold_min_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_raw
    )
    runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts)
        if runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_raw
    )
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts)
        if runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_raw
    )
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts)
        if runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_lidar_point_count_avg_warn_min_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_raw
    )
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts)
        if runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_lidar_point_count_avg_hold_min_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_raw
    )
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts)
        if runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_radar_false_positive_rate_avg_warn_max_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_raw
    )
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts)
        if runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts
        else "n/a"
    )
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_raw = (
        runtime_lane_execution_summary.get(
            "phase2_sensor_radar_false_positive_rate_avg_hold_max_counts",
            {},
        )
    )
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts = _as_non_negative_int_map(
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_raw
    )
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text = (
        _format_threshold_counts(runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts)
        if runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts
        else "n/a"
    )
    runtime_lane_execution_exec_lane_row_count = int(
        runtime_lane_execution_lane_row_counts.get("exec")
        or runtime_lane_execution_summary.get("exec_lane_row_count", 0)
        or 0
    )
    runtime_lane_execution_failed_rows_raw = runtime_lane_execution_summary.get("failed_rows", [])
    runtime_lane_execution_failed_rows: list[dict[str, str]] = []
    if isinstance(runtime_lane_execution_failed_rows_raw, list):
        for row in runtime_lane_execution_failed_rows_raw:
            if not isinstance(row, dict):
                continue
            runtime_lane_execution_failed_rows.append(
                {
                    "runtime": str(row.get("runtime", "")).strip() or "runtime_unknown",
                    "release_id": str(row.get("release_id", "")).strip() or "release_unknown",
                    "lane": str(row.get("lane", "")).strip() or "lane_unknown",
                    "runtime_failure_reason": str(row.get("runtime_failure_reason", "")).strip() or "unknown",
                }
            )
    runtime_lane_execution_failed_rows.sort(
        key=lambda row: (
            str(row.get("release_id", "")),
            str(row.get("runtime", "")),
            str(row.get("lane", "")),
            str(row.get("runtime_failure_reason", "")),
        )
    )
    runtime_lane_execution_failed_rows_text = _format_runtime_lane_execution_failed_rows(
        runtime_lane_execution_failed_rows
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_runtime_counts = _as_non_negative_int_map(
        runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("runtime_counts", {})
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_result_counts = _as_non_negative_int_map(
        runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("result_counts", {})
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_mapping_mode_counts = _as_non_negative_int_map(
        runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("mapping_mode_counts", {})
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary_raw = (
        runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("pass_metric_summary", {})
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary = (
        runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary_raw
        if isinstance(runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary_raw, dict)
        else {}
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary_raw = (
        runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("fail_metric_summary", {})
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary = (
        runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary_raw
        if isinstance(runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary_raw, dict)
        else {}
    )
    runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta = _as_float_map(
        runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("pass_minus_fail_metric_delta", {})
    )
    try:
        runtime_lane_phase2_rig_sweep_radar_alignment_row_count = int(
            runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("runtime_row_count", 0)
        )
    except (TypeError, ValueError):
        runtime_lane_phase2_rig_sweep_radar_alignment_row_count = 0
    try:
        runtime_lane_phase2_rig_sweep_radar_alignment_metrics_sample_count = int(
            runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("metrics_sample_count", 0)
        )
    except (TypeError, ValueError):
        runtime_lane_phase2_rig_sweep_radar_alignment_metrics_sample_count = 0
    try:
        runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_row_count = int(
            runtime_lane_phase2_rig_sweep_radar_alignment_summary.get("unmatched_row_count", 0)
        )
    except (TypeError, ValueError):
        runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_row_count = 0
    try:
        runtime_lane_phase2_rig_sweep_radar_alignment_pass_metrics_sample_count = int(
            runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary.get(
                "metrics_sample_count",
                0,
            )
        )
    except (TypeError, ValueError):
        runtime_lane_phase2_rig_sweep_radar_alignment_pass_metrics_sample_count = 0
    try:
        runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count = int(
            runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary.get(
                "metrics_sample_count",
                0,
            )
        )
    except (TypeError, ValueError):
        runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count = 0
    runtime_evidence_failed_records_raw = runtime_evidence_summary.get("failed_records", [])
    runtime_evidence_failed_records: list[dict[str, str]] = []
    if isinstance(runtime_evidence_failed_records_raw, list):
        for row in runtime_evidence_failed_records_raw:
            if not isinstance(row, dict):
                continue
            runtime_evidence_failed_records.append(
                {
                    "profile_id": str(row.get("profile_id", "")).strip() or "profile_unknown",
                    "release_id": str(row.get("release_id", "")).strip() or "release_unknown",
                    "error": str(row.get("error", "")).strip(),
                }
            )
    runtime_evidence_failed_records.sort(
        key=lambda row: (
            str(row.get("profile_id", "")),
            str(row.get("release_id", "")),
            str(row.get("error", "")),
        )
    )
    runtime_evidence_failed_records_text = _format_runtime_evidence_failed_records(
        runtime_evidence_failed_records
    )
    runtime_evidence_interop_import_inconsistent_records_raw = runtime_evidence_summary.get(
        "interop_import_manifest_inconsistent_records",
        [],
    )
    runtime_evidence_interop_import_inconsistent_records: list[dict[str, Any]] = []
    if isinstance(runtime_evidence_interop_import_inconsistent_records_raw, list):
        for row in runtime_evidence_interop_import_inconsistent_records_raw:
            if not isinstance(row, dict):
                continue
            try:
                actor_count_manifest = int(row.get("actor_count_manifest", 0) or 0)
            except (TypeError, ValueError):
                actor_count_manifest = 0
            try:
                xosc_entity_count = int(row.get("xosc_entity_count", 0) or 0)
            except (TypeError, ValueError):
                xosc_entity_count = 0
            runtime_evidence_interop_import_inconsistent_records.append(
                {
                    "profile_id": str(row.get("profile_id", "")).strip() or "profile_unknown",
                    "release_id": str(row.get("release_id", "")).strip() or "release_unknown",
                    "runtime": str(row.get("runtime", "")).strip().lower() or "runtime_unknown",
                    "actor_count_manifest": actor_count_manifest,
                    "xosc_entity_count": xosc_entity_count,
                }
            )
    runtime_evidence_interop_import_inconsistent_records.sort(
        key=lambda row: (
            str(row.get("profile_id", "")),
            str(row.get("release_id", "")),
            str(row.get("runtime", "")),
            int(row.get("actor_count_manifest", 0) or 0),
            int(row.get("xosc_entity_count", 0) or 0),
        )
    )
    runtime_evidence_interop_import_inconsistent_records_text = (
        _format_runtime_evidence_interop_import_inconsistent_records(
            runtime_evidence_interop_import_inconsistent_records
        )
    )
    phase3_vehicle_dynamics_violation_rows = _collect_phase3_vehicle_dynamics_violation_rows(
        pipeline_manifests,
        speed_warn_max=phase3_vehicle_final_speed_warn_max,
        speed_hold_max=phase3_vehicle_final_speed_hold_max,
        position_warn_max=phase3_vehicle_final_position_warn_max,
        position_hold_max=phase3_vehicle_final_position_hold_max,
        delta_speed_warn_max=phase3_vehicle_delta_speed_warn_max,
        delta_speed_hold_max=phase3_vehicle_delta_speed_hold_max,
        delta_position_warn_max=phase3_vehicle_delta_position_warn_max,
        delta_position_hold_max=phase3_vehicle_delta_position_hold_max,
        final_heading_abs_warn_max=phase3_vehicle_final_heading_abs_warn_max,
        final_heading_abs_hold_max=phase3_vehicle_final_heading_abs_hold_max,
        final_lateral_position_abs_warn_max=phase3_vehicle_final_lateral_position_abs_warn_max,
        final_lateral_position_abs_hold_max=phase3_vehicle_final_lateral_position_abs_hold_max,
        delta_heading_abs_warn_max=phase3_vehicle_delta_heading_abs_warn_max,
        delta_heading_abs_hold_max=phase3_vehicle_delta_heading_abs_hold_max,
        delta_lateral_position_abs_warn_max=phase3_vehicle_delta_lateral_position_abs_warn_max,
        delta_lateral_position_abs_hold_max=phase3_vehicle_delta_lateral_position_abs_hold_max,
        yaw_rate_abs_warn_max=phase3_vehicle_yaw_rate_abs_warn_max,
        yaw_rate_abs_hold_max=phase3_vehicle_yaw_rate_abs_hold_max,
        delta_yaw_rate_abs_warn_max=phase3_vehicle_delta_yaw_rate_abs_warn_max,
        delta_yaw_rate_abs_hold_max=phase3_vehicle_delta_yaw_rate_abs_hold_max,
        lateral_velocity_abs_warn_max=phase3_vehicle_lateral_velocity_abs_warn_max,
        lateral_velocity_abs_hold_max=phase3_vehicle_lateral_velocity_abs_hold_max,
        accel_abs_warn_max=phase3_vehicle_accel_abs_warn_max,
        accel_abs_hold_max=phase3_vehicle_accel_abs_hold_max,
        lateral_accel_abs_warn_max=phase3_vehicle_lateral_accel_abs_warn_max,
        lateral_accel_abs_hold_max=phase3_vehicle_lateral_accel_abs_hold_max,
        yaw_accel_abs_warn_max=phase3_vehicle_yaw_accel_abs_warn_max,
        yaw_accel_abs_hold_max=phase3_vehicle_yaw_accel_abs_hold_max,
        jerk_abs_warn_max=phase3_vehicle_jerk_abs_warn_max,
        jerk_abs_hold_max=phase3_vehicle_jerk_abs_hold_max,
        lateral_jerk_abs_warn_max=phase3_vehicle_lateral_jerk_abs_warn_max,
        lateral_jerk_abs_hold_max=phase3_vehicle_lateral_jerk_abs_hold_max,
        yaw_jerk_abs_warn_max=phase3_vehicle_yaw_jerk_abs_warn_max,
        yaw_jerk_abs_hold_max=phase3_vehicle_yaw_jerk_abs_hold_max,
        lateral_position_abs_warn_max=phase3_vehicle_lateral_position_abs_warn_max,
        lateral_position_abs_hold_max=phase3_vehicle_lateral_position_abs_hold_max,
        road_grade_abs_warn_max=phase3_vehicle_road_grade_abs_warn_max,
        road_grade_abs_hold_max=phase3_vehicle_road_grade_abs_hold_max,
        grade_force_warn_max=phase3_vehicle_grade_force_warn_max,
        grade_force_hold_max=phase3_vehicle_grade_force_hold_max,
        control_overlap_ratio_warn_max=phase3_vehicle_control_overlap_ratio_warn_max,
        control_overlap_ratio_hold_max=phase3_vehicle_control_overlap_ratio_hold_max,
        control_steering_rate_warn_max=phase3_vehicle_control_steering_rate_warn_max,
        control_steering_rate_hold_max=phase3_vehicle_control_steering_rate_hold_max,
        control_throttle_plus_brake_warn_max=phase3_vehicle_control_throttle_plus_brake_warn_max,
        control_throttle_plus_brake_hold_max=phase3_vehicle_control_throttle_plus_brake_hold_max,
        speed_tracking_error_warn_max=phase3_vehicle_speed_tracking_error_warn_max,
        speed_tracking_error_hold_max=phase3_vehicle_speed_tracking_error_hold_max,
        speed_tracking_abs_error_warn_max=phase3_vehicle_speed_tracking_error_abs_warn_max,
        speed_tracking_abs_error_hold_max=phase3_vehicle_speed_tracking_error_abs_hold_max,
    )
    phase3_vehicle_dynamics_violation_rows_text = _format_phase3_vehicle_dynamics_violation_rows(
        phase3_vehicle_dynamics_violation_rows
    )
    phase3_vehicle_dynamics_violation_summary = _summarize_phase3_vehicle_dynamics_violation_rows(
        phase3_vehicle_dynamics_violation_rows
    )
    phase3_vehicle_dynamics_violation_summary_text = _format_phase3_vehicle_dynamics_violation_summary(
        phase3_vehicle_dynamics_violation_summary
    )
    phase3_vehicle_dynamics_warning_messages: list[str] = []
    phase3_vehicle_dynamics_warning_reasons: list[str] = []
    phase3_core_sim_warning_messages: list[str] = []
    phase3_core_sim_warning_reasons: list[str] = []
    phase3_core_sim_matrix_warning_messages: list[str] = []
    phase3_core_sim_matrix_warning_reasons: list[str] = []
    phase3_core_sim_min_ttc_same_lane_warn_min_mismatch = False
    phase3_core_sim_min_ttc_same_lane_hold_min_mismatch = False
    phase3_core_sim_min_ttc_any_lane_warn_min_mismatch = False
    phase3_core_sim_min_ttc_any_lane_hold_min_mismatch = False
    phase3_lane_risk_warning_messages: list[str] = []
    phase3_lane_risk_warning_reasons: list[str] = []
    phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch = False
    phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch = False
    phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch = False
    phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch = False
    phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch = False
    phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch = False
    phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch = False
    phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch = False
    phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch = False
    phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch = False
    phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch = False
    phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch = False
    phase3_dataset_traffic_warning_messages: list[str] = []
    phase3_dataset_traffic_warning_reasons: list[str] = []
    phase3_dataset_traffic_run_summary_warn_min_mismatch = False
    phase3_dataset_traffic_run_summary_hold_min_mismatch = False
    phase3_dataset_traffic_profile_count_warn_min_mismatch = False
    phase3_dataset_traffic_profile_count_hold_min_mismatch = False
    phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch = False
    phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch = False
    phase3_dataset_traffic_avg_npc_count_warn_min_mismatch = False
    phase3_dataset_traffic_avg_npc_count_hold_min_mismatch = False
    phase3_lane_risk_ttc_under_3s_same_lane_ratio: float | None = None
    phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio: float | None = None
    phase3_lane_risk_ttc_under_3s_any_lane_ratio: float | None = None
    runtime_evidence_warning_messages: list[str] = []
    runtime_evidence_warning_reasons: list[str] = []
    runtime_evidence_warning = ""
    runtime_evidence_interop_contract_warning_messages: list[str] = []
    runtime_evidence_interop_contract_warning_reasons: list[str] = []
    runtime_evidence_interop_contract_warning = ""
    runtime_lane_execution_warning_messages: list[str] = []
    runtime_lane_execution_warning_reasons: list[str] = []
    runtime_lane_execution_warning = ""
    runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages: list[str] = []
    runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons: list[str] = []
    runtime_lane_phase2_rig_sweep_radar_alignment_warning = ""
    runtime_lane_execution_exec_lane_warn_min_rows_mismatch = False
    runtime_lane_execution_exec_lane_hold_min_rows_mismatch = False
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch = False
    runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch = False
    runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch = False
    runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch = False
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch = False
    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch = False
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch = False
    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch = False
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch = False
    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch = False
    runtime_evidence_compare_warning_messages: list[str] = []
    runtime_evidence_compare_warning_reasons: list[str] = []
    runtime_evidence_compare_warning = ""
    runtime_native_evidence_compare_warning_messages: list[str] = []
    runtime_native_evidence_compare_warning_reasons: list[str] = []
    runtime_native_evidence_compare_warning = ""
    runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch = False
    runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch = False
    status = "WARN" if warning else _status_from_counts(
        final_counts,
        pipeline_overall_counts,
        pipeline_trend_counts,
    )
    if runtime_evidence_summary:
        try:
            runtime_evidence_artifact_count = int(runtime_evidence_summary.get("artifact_count", 0))
        except (TypeError, ValueError):
            runtime_evidence_artifact_count = 0
        try:
            runtime_evidence_record_count = int(runtime_evidence_summary.get("record_count", 0))
        except (TypeError, ValueError):
            runtime_evidence_record_count = 0
        try:
            runtime_evidence_failed_count = int(runtime_evidence_summary.get("failed_count", 0))
        except (TypeError, ValueError):
            runtime_evidence_failed_count = 0
        try:
            runtime_evidence_probe_executed_count = int(runtime_evidence_summary.get("probe_executed_count", 0))
        except (TypeError, ValueError):
            runtime_evidence_probe_executed_count = 0
        try:
            runtime_evidence_interop_contract_checked_count = int(
                runtime_evidence_summary.get("interop_contract_checked_count", 0)
            )
        except (TypeError, ValueError):
            runtime_evidence_interop_contract_checked_count = 0
        runtime_evidence_interop_contract_status_counts_provided = (
            "interop_contract_status_counts" in runtime_evidence_summary
        )
        runtime_evidence_interop_export_checked_count_provided = (
            "interop_export_checked_count" in runtime_evidence_summary
        )
        runtime_evidence_interop_export_status_counts_provided = (
            "interop_export_status_counts" in runtime_evidence_summary
        )
        runtime_evidence_interop_import_checked_count_provided = (
            "interop_import_checked_count" in runtime_evidence_summary
        )
        runtime_evidence_interop_import_status_counts_provided = (
            "interop_import_status_counts" in runtime_evidence_summary
        )
        try:
            runtime_evidence_interop_export_checked_count = int(
                runtime_evidence_summary.get("interop_export_checked_count", 0)
            )
        except (TypeError, ValueError):
            runtime_evidence_interop_export_checked_count = 0
        try:
            runtime_evidence_interop_import_checked_count = int(
                runtime_evidence_summary.get("interop_import_checked_count", 0)
            )
        except (TypeError, ValueError):
            runtime_evidence_interop_import_checked_count = 0
        try:
            runtime_evidence_interop_import_manifest_consistent_false_count = int(
                runtime_evidence_summary.get("interop_import_manifest_consistent_false_count", 0)
            )
        except (TypeError, ValueError):
            runtime_evidence_interop_import_manifest_consistent_false_count = 0
        runtime_evidence_interop_contract_status_counts = _as_non_negative_int_map(
            runtime_evidence_summary.get("interop_contract_status_counts")
        )
        runtime_evidence_interop_contract_status_total = sum(runtime_evidence_interop_contract_status_counts.values())
        runtime_evidence_interop_contract_pass_count = 0
        for raw_key, raw_value in runtime_evidence_interop_contract_status_counts.items():
            status_key = str(raw_key).strip().lower()
            if status_key == "pass":
                runtime_evidence_interop_contract_pass_count += max(0, int(raw_value))
        runtime_evidence_interop_contract_fail_count = int(
            runtime_evidence_interop_contract_status_counts.get("fail", 0) or 0
        )
        runtime_evidence_interop_contract_non_pass_count = max(
            0,
            runtime_evidence_interop_contract_status_total - runtime_evidence_interop_contract_pass_count,
        )
        if runtime_evidence_interop_contract_non_pass_count > runtime_evidence_interop_contract_fail_count:
            runtime_evidence_interop_contract_fail_count = runtime_evidence_interop_contract_non_pass_count
        runtime_evidence_interop_export_status_counts = _as_non_negative_int_map(
            runtime_evidence_summary.get("interop_export_status_counts")
        )
        runtime_evidence_interop_import_status_counts = _as_non_negative_int_map(
            runtime_evidence_summary.get("interop_import_status_counts")
        )
        runtime_evidence_interop_export_status_total = sum(runtime_evidence_interop_export_status_counts.values())
        runtime_evidence_interop_import_status_total = sum(runtime_evidence_interop_import_status_counts.values())
        runtime_evidence_interop_export_pass_count = 0
        for raw_key, raw_value in runtime_evidence_interop_export_status_counts.items():
            status_key = str(raw_key).strip().lower()
            if status_key == "pass":
                runtime_evidence_interop_export_pass_count += max(0, int(raw_value))
        runtime_evidence_interop_import_pass_count = 0
        for raw_key, raw_value in runtime_evidence_interop_import_status_counts.items():
            status_key = str(raw_key).strip().lower()
            if status_key == "pass":
                runtime_evidence_interop_import_pass_count += max(0, int(raw_value))
        runtime_evidence_probe_args_effective_count_raw = runtime_evidence_summary.get(
            "probe_args_effective_count",
            0,
        )
        runtime_evidence_probe_args_requested_count_raw = runtime_evidence_summary.get(
            "probe_args_requested_count",
            0,
        )
        runtime_evidence_probe_args_effective_count_provided = "probe_args_effective_count" in runtime_evidence_summary
        runtime_evidence_probe_args_requested_count_provided = "probe_args_requested_count" in runtime_evidence_summary
        runtime_evidence_probe_arg_value_counts = _as_non_negative_int_map(
            runtime_evidence_summary.get("probe_arg_value_counts")
        )
        runtime_evidence_probe_arg_requested_value_counts = _as_non_negative_int_map(
            runtime_evidence_summary.get("probe_arg_requested_value_counts")
        )
        runtime_evidence_probe_arg_value_counts_provided = "probe_arg_value_counts" in runtime_evidence_summary
        runtime_evidence_probe_arg_requested_value_counts_provided = (
            "probe_arg_requested_value_counts" in runtime_evidence_summary
        )
        runtime_evidence_probe_arg_value_total = sum(runtime_evidence_probe_arg_value_counts.values())
        runtime_evidence_probe_arg_requested_value_total = sum(
            runtime_evidence_probe_arg_requested_value_counts.values()
        )
        try:
            runtime_evidence_probe_args_effective_count = int(runtime_evidence_probe_args_effective_count_raw)
        except (TypeError, ValueError):
            runtime_evidence_probe_args_effective_count = 0
        try:
            runtime_evidence_probe_args_requested_count = int(runtime_evidence_probe_args_requested_count_raw)
        except (TypeError, ValueError):
            runtime_evidence_probe_args_requested_count = 0
        if (
            runtime_evidence_interop_contract_checked_hold_min > 0
            and runtime_evidence_interop_contract_checked_count < runtime_evidence_interop_contract_checked_hold_min
        ):
            runtime_evidence_interop_contract_warning_messages.append(
                "runtime_evidence_interop_contract_checked_count="
                f"{runtime_evidence_interop_contract_checked_count} "
                "below hold_min_checked="
                f"{runtime_evidence_interop_contract_checked_hold_min}"
            )
            runtime_evidence_interop_contract_warning_reasons.append(
                "runtime_evidence_interop_contract_checked_count_below_hold_min"
            )
            if status != "HOLD":
                status = "HOLD"
        elif (
            runtime_evidence_interop_contract_checked_warn_min > 0
            and runtime_evidence_interop_contract_checked_count < runtime_evidence_interop_contract_checked_warn_min
        ):
            runtime_evidence_interop_contract_warning_messages.append(
                "runtime_evidence_interop_contract_checked_count="
                f"{runtime_evidence_interop_contract_checked_count} "
                "below warn_min_checked="
                f"{runtime_evidence_interop_contract_checked_warn_min}"
            )
            runtime_evidence_interop_contract_warning_reasons.append(
                "runtime_evidence_interop_contract_checked_count_below_warn_min"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_contract_fail_hold_max > 0
            and runtime_evidence_interop_contract_fail_count > runtime_evidence_interop_contract_fail_hold_max
        ):
            runtime_evidence_interop_contract_warning_messages.append(
                "runtime_evidence_interop_contract_fail_count="
                f"{runtime_evidence_interop_contract_fail_count} "
                "exceeded hold_max_fail_count="
                f"{runtime_evidence_interop_contract_fail_hold_max} "
                f"(counts={_format_non_negative_int_counts(runtime_evidence_interop_contract_status_counts)})"
            )
            runtime_evidence_interop_contract_warning_reasons.append(
                "runtime_evidence_interop_contract_fail_count_above_hold_max"
            )
            if status != "HOLD":
                status = "HOLD"
        elif (
            runtime_evidence_interop_contract_fail_warn_max > 0
            and runtime_evidence_interop_contract_fail_count > runtime_evidence_interop_contract_fail_warn_max
        ):
            runtime_evidence_interop_contract_warning_messages.append(
                "runtime_evidence_interop_contract_fail_count="
                f"{runtime_evidence_interop_contract_fail_count} "
                "exceeded warn_max_fail_count="
                f"{runtime_evidence_interop_contract_fail_warn_max} "
                f"(counts={_format_non_negative_int_counts(runtime_evidence_interop_contract_status_counts)})"
            )
            runtime_evidence_interop_contract_warning_reasons.append(
                "runtime_evidence_interop_contract_fail_count_above_warn_max"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if runtime_evidence_failed_count > 0:
            runtime_evidence_warning_messages.append(
                "runtime_evidence_failed="
                f"{runtime_evidence_failed_count}/{runtime_evidence_record_count} "
                f"(artifacts={runtime_evidence_artifact_count})"
            )
            runtime_evidence_warning_reasons.append("runtime_evidence_failed_records")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_probe_executed_count > 0
            and runtime_evidence_probe_args_effective_count_provided
            and runtime_evidence_probe_args_effective_count < runtime_evidence_probe_executed_count
        ):
            runtime_evidence_probe_args_effective_missing = (
                runtime_evidence_probe_executed_count - runtime_evidence_probe_args_effective_count
            )
            runtime_evidence_warning_messages.append(
                "runtime_probe_args_missing_effective="
                f"{runtime_evidence_probe_args_effective_missing}/{runtime_evidence_probe_executed_count} "
                f"(recorded={runtime_evidence_probe_args_effective_count})"
            )
            runtime_evidence_warning_reasons.append("runtime_evidence_probe_args_effective_missing")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_probe_executed_count > 0
            and runtime_evidence_probe_args_requested_count_provided
            and runtime_evidence_probe_args_requested_count < runtime_evidence_probe_executed_count
        ):
            runtime_evidence_probe_args_requested_missing = (
                runtime_evidence_probe_executed_count - runtime_evidence_probe_args_requested_count
            )
            runtime_evidence_warning_messages.append(
                "runtime_probe_args_missing_requested="
                f"{runtime_evidence_probe_args_requested_missing}/{runtime_evidence_probe_executed_count} "
                f"(recorded={runtime_evidence_probe_args_requested_count})"
            )
            runtime_evidence_warning_reasons.append("runtime_evidence_probe_args_requested_missing")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_probe_args_effective_count > 0
            and runtime_evidence_probe_arg_value_counts_provided
            and runtime_evidence_probe_arg_value_total <= 0
        ):
            runtime_evidence_warning_messages.append(
                "runtime_probe_arg_values_missing_effective_detail="
                f"records:{runtime_evidence_probe_args_effective_count},"
                f"total_values:{runtime_evidence_probe_arg_value_total}"
            )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_probe_arg_values_effective_missing_detail"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_probe_args_requested_count > 0
            and runtime_evidence_probe_arg_requested_value_counts_provided
            and runtime_evidence_probe_arg_requested_value_total <= 0
        ):
            runtime_evidence_warning_messages.append(
                "runtime_probe_arg_values_missing_requested_detail="
                f"records:{runtime_evidence_probe_args_requested_count},"
                f"total_values:{runtime_evidence_probe_arg_requested_value_total}"
            )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_probe_arg_values_requested_missing_detail"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_export_checked_count_provided
            and runtime_evidence_interop_contract_checked_count > 0
            and runtime_evidence_interop_export_checked_count < runtime_evidence_interop_contract_checked_count
        ):
            runtime_evidence_interop_export_missing_count = (
                runtime_evidence_interop_contract_checked_count - runtime_evidence_interop_export_checked_count
            )
            runtime_evidence_warning_messages.append(
                "runtime_interop_export_missing="
                f"{runtime_evidence_interop_export_missing_count}/{runtime_evidence_interop_contract_checked_count} "
                f"(checked={runtime_evidence_interop_export_checked_count})"
            )
            runtime_evidence_warning_reasons.append("runtime_evidence_interop_export_missing")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_export_status_counts_provided
            and runtime_evidence_interop_export_checked_count > 0
            and runtime_evidence_interop_export_status_total <= 0
        ):
            runtime_evidence_warning_messages.append(
                "runtime_interop_export_status_missing_detail="
                f"checked:{runtime_evidence_interop_export_checked_count},total_statuses:0"
            )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_interop_export_status_missing_detail"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_export_status_counts_provided
            and runtime_evidence_interop_export_status_total > 0
            and runtime_evidence_interop_export_pass_count < runtime_evidence_interop_export_status_total
        ):
            runtime_evidence_interop_export_non_pass_count = (
                runtime_evidence_interop_export_status_total - runtime_evidence_interop_export_pass_count
            )
            runtime_evidence_warning_messages.append(
                "runtime_interop_export_non_pass="
                f"{runtime_evidence_interop_export_non_pass_count}/{runtime_evidence_interop_export_status_total} "
                f"(pass={runtime_evidence_interop_export_pass_count})"
            )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_interop_export_non_pass_status"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_import_checked_count_provided
            and runtime_evidence_interop_contract_checked_count > 0
            and runtime_evidence_interop_import_checked_count < runtime_evidence_interop_contract_checked_count
        ):
            runtime_evidence_interop_import_missing_count = (
                runtime_evidence_interop_contract_checked_count - runtime_evidence_interop_import_checked_count
            )
            runtime_evidence_warning_messages.append(
                "runtime_interop_import_missing="
                f"{runtime_evidence_interop_import_missing_count}/{runtime_evidence_interop_contract_checked_count} "
                f"(checked={runtime_evidence_interop_import_checked_count})"
            )
            runtime_evidence_warning_reasons.append("runtime_evidence_interop_import_missing")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_import_status_counts_provided
            and runtime_evidence_interop_import_checked_count > 0
            and runtime_evidence_interop_import_status_total <= 0
        ):
            runtime_evidence_warning_messages.append(
                "runtime_interop_import_status_missing_detail="
                f"checked:{runtime_evidence_interop_import_checked_count},total_statuses:0"
            )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_interop_import_status_missing_detail"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_interop_import_status_counts_provided
            and runtime_evidence_interop_import_status_total > 0
            and runtime_evidence_interop_import_pass_count < runtime_evidence_interop_import_status_total
        ):
            runtime_evidence_interop_import_non_pass_count = (
                runtime_evidence_interop_import_status_total - runtime_evidence_interop_import_pass_count
            )
            runtime_evidence_warning_messages.append(
                "runtime_interop_import_non_pass="
                f"{runtime_evidence_interop_import_non_pass_count}/{runtime_evidence_interop_import_status_total} "
                f"(pass={runtime_evidence_interop_import_pass_count})"
            )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_interop_import_non_pass_status"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if runtime_evidence_interop_import_manifest_consistent_false_count > 0:
            runtime_evidence_interop_import_manifest_consistent_total = (
                runtime_evidence_interop_import_checked_count
                if runtime_evidence_interop_import_checked_count > 0
                else max(0, runtime_evidence_record_count)
            )
            runtime_evidence_warning_messages.append(
                "runtime_interop_import_manifest_inconsistent="
                f"{runtime_evidence_interop_import_manifest_consistent_false_count}/"
                f"{runtime_evidence_interop_import_manifest_consistent_total}"
            )
            if runtime_evidence_interop_import_inconsistent_records_text != "n/a":
                runtime_evidence_warning_messages.append(
                    "runtime_interop_import_manifest_inconsistent_records="
                    f"{runtime_evidence_interop_import_inconsistent_records_text}"
                )
            runtime_evidence_warning_reasons.append(
                "runtime_evidence_interop_import_manifest_inconsistent"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if runtime_evidence_interop_contract_status_counts_provided and runtime_evidence_interop_contract_checked_count > 0:
            if runtime_evidence_interop_contract_status_total <= 0:
                runtime_evidence_interop_contract_warning_messages.append(
                    "runtime_evidence_interop_contract_status_missing_detail="
                    f"checked:{runtime_evidence_interop_contract_checked_count},total_statuses:0"
                )
                runtime_evidence_interop_contract_warning_reasons.append(
                    "runtime_evidence_interop_contract_status_missing_detail"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
    if runtime_evidence_warning_messages:
        runtime_evidence_warning = "; ".join(runtime_evidence_warning_messages)
        runtime_evidence_warning_reasons = list(dict.fromkeys(runtime_evidence_warning_reasons))
    if runtime_evidence_interop_contract_warning_messages:
        runtime_evidence_interop_contract_warning = "; ".join(
            runtime_evidence_interop_contract_warning_messages
        )
        runtime_evidence_interop_contract_warning_reasons = list(
            dict.fromkeys(runtime_evidence_interop_contract_warning_reasons)
        )
    if runtime_lane_execution_summary:
        try:
            runtime_lane_execution_artifact_count = int(runtime_lane_execution_summary.get("artifact_count", 0))
        except (TypeError, ValueError):
            runtime_lane_execution_artifact_count = 0
        try:
            runtime_lane_execution_row_count = int(runtime_lane_execution_summary.get("runtime_row_count", 0))
        except (TypeError, ValueError):
            runtime_lane_execution_row_count = 0
        try:
            runtime_lane_execution_fail_count = int(runtime_lane_execution_summary.get("fail_count", 0))
        except (TypeError, ValueError):
            runtime_lane_execution_fail_count = 0
        try:
            runtime_lane_execution_unknown_count = int(runtime_lane_execution_summary.get("unknown_count", 0))
        except (TypeError, ValueError):
            runtime_lane_execution_unknown_count = 0
        try:
            runtime_lane_execution_evidence_missing_count = int(
                runtime_lane_execution_summary.get("runtime_evidence_exists_false_count", 0)
            )
        except (TypeError, ValueError):
            runtime_lane_execution_evidence_missing_count = 0
        if runtime_lane_execution_fail_count > 0:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_failed="
                f"{runtime_lane_execution_fail_count}/{runtime_lane_execution_row_count} "
                f"(artifacts={runtime_lane_execution_artifact_count})"
            )
            runtime_lane_execution_warning_reasons.append("runtime_lane_execution_failed_rows")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if runtime_lane_execution_unknown_count > 0:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_unknown="
                f"{runtime_lane_execution_unknown_count}/{runtime_lane_execution_row_count} "
                f"(artifacts={runtime_lane_execution_artifact_count})"
            )
            runtime_lane_execution_warning_reasons.append("runtime_lane_execution_unknown_rows")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if runtime_lane_execution_evidence_missing_count > 0:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_evidence_missing="
                f"{runtime_lane_execution_evidence_missing_count}/{runtime_lane_execution_row_count} "
                f"(artifacts={runtime_lane_execution_artifact_count})"
            )
            if runtime_lane_execution_evidence_missing_runtimes_text != "n/a":
                runtime_lane_execution_warning_messages.append(
                    "runtime_lane_execution_evidence_missing_runtimes="
                    f"{runtime_lane_execution_evidence_missing_runtimes_text}"
                )
            runtime_lane_execution_warning_reasons.append("runtime_lane_execution_evidence_missing")
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if runtime_lane_execution_row_count > 0:
            if (
                runtime_lane_execution_hold_min_exec_rows > 0
                and runtime_lane_execution_exec_lane_row_count < runtime_lane_execution_hold_min_exec_rows
            ):
                runtime_lane_execution_warning_messages.append(
                    "runtime_lane_execution_exec_lane_rows="
                    f"{runtime_lane_execution_exec_lane_row_count}/{runtime_lane_execution_row_count} "
                    f"below hold_min_exec_rows={runtime_lane_execution_hold_min_exec_rows} "
                    f"(lane_rows={runtime_lane_execution_lane_row_counts_text})"
                )
                runtime_lane_execution_warning_reasons.append(
                    "runtime_lane_execution_exec_lane_rows_below_hold_min"
                )
                if status != "HOLD":
                    status = "HOLD"
            elif (
                runtime_lane_execution_warn_min_exec_rows > 0
                and runtime_lane_execution_exec_lane_row_count < runtime_lane_execution_warn_min_exec_rows
            ):
                runtime_lane_execution_warning_messages.append(
                    "runtime_lane_execution_exec_lane_rows="
                    f"{runtime_lane_execution_exec_lane_row_count}/{runtime_lane_execution_row_count} "
                    f"below warn_min_exec_rows={runtime_lane_execution_warn_min_exec_rows} "
                    f"(lane_rows={runtime_lane_execution_lane_row_counts_text})"
                )
                runtime_lane_execution_warning_reasons.append(
                    "runtime_lane_execution_exec_lane_rows_below_warn_min"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
        runtime_lane_execution_exec_lane_warn_min_rows_mismatch = _threshold_count_mismatch(
            runtime_lane_execution_exec_lane_warn_min_rows_counts,
            runtime_lane_execution_warn_min_exec_rows,
        )
        if runtime_lane_execution_exec_lane_warn_min_rows_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_exec_lane_warn_min_rows_mismatch="
                f"expected:{runtime_lane_execution_warn_min_exec_rows},"
                f"observed:{runtime_lane_execution_exec_lane_warn_min_rows_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_exec_lane_warn_min_rows_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_lane_execution_exec_lane_hold_min_rows_mismatch = _threshold_count_mismatch(
            runtime_lane_execution_exec_lane_hold_min_rows_counts,
            runtime_lane_execution_hold_min_exec_rows,
        )
        if runtime_lane_execution_exec_lane_hold_min_rows_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_exec_lane_hold_min_rows_mismatch="
                f"expected:{runtime_lane_execution_hold_min_exec_rows},"
                f"observed:{runtime_lane_execution_exec_lane_hold_min_rows_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_exec_lane_hold_min_rows_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch = _threshold_count_mismatch(
            runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts,
            runtime_evidence_compare_warn_min_artifacts_with_diffs,
        )
        if runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch:
            runtime_evidence_compare_warning_messages.append(
                "runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch="
                f"expected:{runtime_evidence_compare_warn_min_artifacts_with_diffs},"
                "observed:"
                f"{runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_text}"
            )
            runtime_evidence_compare_warning_reasons.append(
                "runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch = _threshold_count_mismatch(
            runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts,
            runtime_evidence_compare_hold_min_artifacts_with_diffs,
        )
        if runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch:
            runtime_evidence_compare_warning_messages.append(
                "runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch="
                f"expected:{runtime_evidence_compare_hold_min_artifacts_with_diffs},"
                "observed:"
                f"{runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_text}"
            )
            runtime_evidence_compare_warning_reasons.append(
                "runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch = _threshold_float_count_mismatch(
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts,
            phase2_sensor_fidelity_score_avg_warn_min,
        )
        if runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch="
                f"expected:{phase2_sensor_fidelity_score_avg_warn_min:.3f},"
                f"observed:{runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch = _threshold_float_count_mismatch(
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts,
            phase2_sensor_fidelity_score_avg_hold_min,
        )
        if runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch="
                f"expected:{phase2_sensor_fidelity_score_avg_hold_min:.3f},"
                f"observed:{runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch = _threshold_float_count_mismatch(
            runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts,
            phase2_sensor_frame_count_avg_warn_min,
        )
        if runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch="
                f"expected:{phase2_sensor_frame_count_avg_warn_min:.3f},"
                f"observed:{runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch = _threshold_float_count_mismatch(
            runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts,
            phase2_sensor_frame_count_avg_hold_min,
        )
        if runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch="
                f"expected:{phase2_sensor_frame_count_avg_hold_min:.3f},"
                f"observed:{runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch = (
            _threshold_float_count_mismatch(
                runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts,
                phase2_sensor_camera_noise_stddev_px_avg_warn_max,
            )
        )
        if runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch="
                f"expected:{phase2_sensor_camera_noise_stddev_px_avg_warn_max:.3f},"
                "observed:"
                f"{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch = (
            _threshold_float_count_mismatch(
                runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts,
                phase2_sensor_camera_noise_stddev_px_avg_hold_max,
            )
        )
        if runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch="
                f"expected:{phase2_sensor_camera_noise_stddev_px_avg_hold_max:.3f},"
                "observed:"
                f"{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch = (
            _threshold_float_count_mismatch(
                runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts,
                phase2_sensor_lidar_point_count_avg_warn_min,
            )
        )
        if runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch="
                f"expected:{phase2_sensor_lidar_point_count_avg_warn_min:.3f},"
                "observed:"
                f"{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch = (
            _threshold_float_count_mismatch(
                runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts,
                phase2_sensor_lidar_point_count_avg_hold_min,
            )
        )
        if runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch="
                f"expected:{phase2_sensor_lidar_point_count_avg_hold_min:.3f},"
                "observed:"
                f"{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch = (
            _threshold_float_count_mismatch(
                runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts,
                phase2_sensor_radar_false_positive_rate_avg_warn_max,
            )
        )
        if runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch="
                f"expected:{phase2_sensor_radar_false_positive_rate_avg_warn_max:.3f},"
                "observed:"
                f"{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch = (
            _threshold_float_count_mismatch(
                runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts,
                phase2_sensor_radar_false_positive_rate_avg_hold_max,
            )
        )
        if runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch:
            runtime_lane_execution_warning_messages.append(
                "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch="
                f"expected:{phase2_sensor_radar_false_positive_rate_avg_hold_max:.3f},"
                "observed:"
                f"{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text}"
            )
            runtime_lane_execution_warning_reasons.append(
                "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch"
            )
            if status != "HOLD":
                status = "HOLD"
        if (
            runtime_lane_execution_fail_count > 0
            and runtime_lane_phase2_rig_sweep_radar_alignment_row_count > 0
        ):
            if runtime_lane_phase2_rig_sweep_radar_alignment_metrics_sample_count <= 0:
                runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages.append(
                    "runtime_lane_phase2_rig_sweep_radar_alignment_metrics_sample_count=0 "
                    f"while runtime_lane_execution_failed={runtime_lane_execution_fail_count}/"
                    f"{runtime_lane_execution_row_count}"
                )
                runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons.append(
                    "runtime_lane_phase2_rig_sweep_radar_alignment_missing_metric_samples"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            if runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_row_count > 0:
                runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages.append(
                    "runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_rows="
                    f"{runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_row_count}/"
                    f"{runtime_lane_phase2_rig_sweep_radar_alignment_row_count}"
                )
                runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons.append(
                    "runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_rows_present"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            if (
                runtime_lane_phase2_rig_sweep_radar_alignment_pass_metrics_sample_count > 0
                and runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count > 0
            ):
                radar_effective_delta = float(
                    runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta.get(
                        "radar_effective_detection_quality_avg",
                        0.0,
                    )
                    or 0.0
                )
                radar_track_purity_delta = float(
                    runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta.get(
                        "radar_track_purity_avg",
                        0.0,
                    )
                    or 0.0
                )
                radar_doppler_delta = float(
                    runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta.get(
                        "radar_doppler_resolution_quality_avg",
                        0.0,
                    )
                    or 0.0
                )
                radar_range_delta = float(
                    runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta.get(
                        "radar_range_coverage_quality_avg",
                        0.0,
                    )
                    or 0.0
                )
                delta_candidates = [
                    ("radar_effective_quality_avg", radar_effective_delta),
                    ("radar_track_purity_avg", radar_track_purity_delta),
                    ("radar_doppler_quality_avg", radar_doppler_delta),
                    ("radar_range_quality_avg", radar_range_delta),
                ]
                degraded_metrics = [
                    f"{metric_name}:{metric_delta:.3f}"
                    for metric_name, metric_delta in delta_candidates
                    if metric_delta <= -0.05
                ]
                non_positive_metrics = [
                    f"{metric_name}:{metric_delta:.3f}"
                    for metric_name, metric_delta in delta_candidates
                    if metric_delta <= 0.0
                ]
                if degraded_metrics:
                    runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages.append(
                        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_degraded="
                        + ",".join(degraded_metrics)
                    )
                    runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons.append(
                        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_degraded"
                    )
                    if radar_effective_delta <= -0.10 or len(degraded_metrics) >= 2:
                        status = "HOLD"
                    elif status in {"PASS", "INFO"}:
                        status = "WARN"
                elif non_positive_metrics:
                    runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages.append(
                        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_non_positive="
                        + ",".join(non_positive_metrics)
                    )
                    runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons.append(
                        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_non_positive"
                    )
                    if status in {"PASS", "INFO"}:
                        status = "WARN"
            elif runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count > 0:
                runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages.append(
                    "runtime_lane_phase2_rig_sweep_radar_alignment_missing_pass_baseline="
                    f"pass_metric_samples={runtime_lane_phase2_rig_sweep_radar_alignment_pass_metrics_sample_count},"
                    f"fail_metric_samples={runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count}"
                )
                runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons.append(
                    "runtime_lane_phase2_rig_sweep_radar_alignment_missing_pass_baseline"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
    if runtime_lane_execution_warning_messages:
        runtime_lane_execution_warning = "; ".join(runtime_lane_execution_warning_messages)
        runtime_lane_execution_warning_reasons = list(dict.fromkeys(runtime_lane_execution_warning_reasons))
    if runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages:
        runtime_lane_phase2_rig_sweep_radar_alignment_warning = "; ".join(
            runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages
        )
        runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons = list(
            dict.fromkeys(runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons)
        )
    if runtime_evidence_compare_summary:
        try:
            runtime_evidence_compare_artifact_count = int(runtime_evidence_compare_summary.get("artifact_count", 0))
        except (TypeError, ValueError):
            runtime_evidence_compare_artifact_count = 0
        try:
            runtime_evidence_compare_artifacts_with_diffs_count = int(
                runtime_evidence_compare_summary.get("artifacts_with_diffs_count", 0)
            )
        except (TypeError, ValueError):
            runtime_evidence_compare_artifacts_with_diffs_count = 0
        if (
            runtime_evidence_compare_hold_min_artifacts_with_diffs > 0
            and runtime_evidence_compare_artifacts_with_diffs_count
            >= runtime_evidence_compare_hold_min_artifacts_with_diffs
        ):
            runtime_evidence_compare_warning_messages.append(
                "runtime_evidence_compare_with_diffs="
                f"{runtime_evidence_compare_artifacts_with_diffs_count}/{runtime_evidence_compare_artifact_count} "
                "exceeded hold_min_artifacts_with_diffs="
                f"{runtime_evidence_compare_hold_min_artifacts_with_diffs}"
            )
            runtime_evidence_compare_warning_reasons.append(
                "runtime_evidence_compare_with_diffs_above_hold_min"
            )
            if status != "HOLD":
                status = "HOLD"
        elif (
            runtime_evidence_compare_warn_min_artifacts_with_diffs > 0
            and runtime_evidence_compare_artifacts_with_diffs_count
            >= runtime_evidence_compare_warn_min_artifacts_with_diffs
        ):
            runtime_evidence_compare_warning_messages.append(
                "runtime_evidence_compare_with_diffs="
                f"{runtime_evidence_compare_artifacts_with_diffs_count}/{runtime_evidence_compare_artifact_count} "
                "exceeded warn_min_artifacts_with_diffs="
                f"{runtime_evidence_compare_warn_min_artifacts_with_diffs}"
            )
            runtime_evidence_compare_warning_reasons.append(
                "runtime_evidence_compare_with_diffs_above_warn_min"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_compare_hold_min_interop_import_mode_diff_count > 0
            and runtime_evidence_compare_interop_import_mode_diff_count_total
            >= runtime_evidence_compare_hold_min_interop_import_mode_diff_count
        ):
            runtime_evidence_compare_warning_messages.append(
                "runtime_evidence_compare_interop_import_mode_diff_count_total="
                f"{runtime_evidence_compare_interop_import_mode_diff_count_total} "
                "exceeded hold_min_interop_import_mode_diff_count="
                f"{runtime_evidence_compare_hold_min_interop_import_mode_diff_count} "
                f"(counts={runtime_evidence_compare_interop_import_mode_diff_counts_text})"
            )
            runtime_evidence_compare_warning_reasons.append(
                "runtime_evidence_compare_interop_import_mode_diff_count_above_hold_min"
            )
            if status != "HOLD":
                status = "HOLD"
        elif (
            runtime_evidence_compare_warn_min_interop_import_mode_diff_count > 0
            and runtime_evidence_compare_interop_import_mode_diff_count_total
            >= runtime_evidence_compare_warn_min_interop_import_mode_diff_count
        ):
            runtime_evidence_compare_warning_messages.append(
                "runtime_evidence_compare_interop_import_mode_diff_count_total="
                f"{runtime_evidence_compare_interop_import_mode_diff_count_total} "
                "exceeded warn_min_interop_import_mode_diff_count="
                f"{runtime_evidence_compare_warn_min_interop_import_mode_diff_count} "
                f"(counts={runtime_evidence_compare_interop_import_mode_diff_counts_text})"
            )
            runtime_evidence_compare_warning_reasons.append(
                "runtime_evidence_compare_interop_import_mode_diff_count_above_warn_min"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
    if runtime_evidence_compare_warning_messages:
        runtime_evidence_compare_warning = "; ".join(runtime_evidence_compare_warning_messages)
        runtime_evidence_compare_warning_reasons = list(
            dict.fromkeys(runtime_evidence_compare_warning_reasons)
        )
    if runtime_native_evidence_compare_summary:
        try:
            runtime_native_evidence_compare_artifact_count = int(
                runtime_native_evidence_compare_summary.get("artifact_count", 0)
            )
        except (TypeError, ValueError):
            runtime_native_evidence_compare_artifact_count = 0
        try:
            runtime_native_evidence_compare_artifacts_with_diffs_count = int(
                runtime_native_evidence_compare_summary.get("artifacts_with_diffs_count", 0)
            )
        except (TypeError, ValueError):
            runtime_native_evidence_compare_artifacts_with_diffs_count = 0
        if (
            runtime_evidence_compare_hold_min_artifacts_with_diffs > 0
            and runtime_native_evidence_compare_artifacts_with_diffs_count
            >= runtime_evidence_compare_hold_min_artifacts_with_diffs
        ):
            runtime_native_evidence_compare_warning_messages.append(
                "runtime_native_evidence_compare_with_diffs="
                f"{runtime_native_evidence_compare_artifacts_with_diffs_count}/"
                f"{runtime_native_evidence_compare_artifact_count} "
                "exceeded hold_min_artifacts_with_diffs="
                f"{runtime_evidence_compare_hold_min_artifacts_with_diffs}"
            )
            runtime_native_evidence_compare_warning_reasons.append(
                "runtime_native_evidence_compare_with_diffs_above_hold_min"
            )
            if status != "HOLD":
                status = "HOLD"
        elif (
            runtime_evidence_compare_warn_min_artifacts_with_diffs > 0
            and runtime_native_evidence_compare_artifacts_with_diffs_count
            >= runtime_evidence_compare_warn_min_artifacts_with_diffs
        ):
            runtime_native_evidence_compare_warning_messages.append(
                "runtime_native_evidence_compare_with_diffs="
                f"{runtime_native_evidence_compare_artifacts_with_diffs_count}/"
                f"{runtime_native_evidence_compare_artifact_count} "
                "exceeded warn_min_artifacts_with_diffs="
                f"{runtime_evidence_compare_warn_min_artifacts_with_diffs}"
            )
            runtime_native_evidence_compare_warning_reasons.append(
                "runtime_native_evidence_compare_with_diffs_above_warn_min"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
        if (
            runtime_evidence_compare_hold_min_interop_import_mode_diff_count > 0
            and runtime_native_evidence_compare_interop_import_mode_diff_count_total
            >= runtime_evidence_compare_hold_min_interop_import_mode_diff_count
        ):
            runtime_native_evidence_compare_warning_messages.append(
                "runtime_native_evidence_compare_interop_import_mode_diff_count_total="
                f"{runtime_native_evidence_compare_interop_import_mode_diff_count_total} "
                "exceeded hold_min_interop_import_mode_diff_count="
                f"{runtime_evidence_compare_hold_min_interop_import_mode_diff_count} "
                f"(counts={runtime_native_evidence_compare_interop_import_mode_diff_counts_text})"
            )
            runtime_native_evidence_compare_warning_reasons.append(
                "runtime_native_evidence_compare_interop_import_mode_diff_count_above_hold_min"
            )
            if status != "HOLD":
                status = "HOLD"
        elif (
            runtime_evidence_compare_warn_min_interop_import_mode_diff_count > 0
            and runtime_native_evidence_compare_interop_import_mode_diff_count_total
            >= runtime_evidence_compare_warn_min_interop_import_mode_diff_count
        ):
            runtime_native_evidence_compare_warning_messages.append(
                "runtime_native_evidence_compare_interop_import_mode_diff_count_total="
                f"{runtime_native_evidence_compare_interop_import_mode_diff_count_total} "
                "exceeded warn_min_interop_import_mode_diff_count="
                f"{runtime_evidence_compare_warn_min_interop_import_mode_diff_count} "
                f"(counts={runtime_native_evidence_compare_interop_import_mode_diff_counts_text})"
            )
            runtime_native_evidence_compare_warning_reasons.append(
                "runtime_native_evidence_compare_interop_import_mode_diff_count_above_warn_min"
            )
            if status in {"PASS", "INFO"}:
                status = "WARN"
    if runtime_native_evidence_compare_warning_messages:
        runtime_native_evidence_compare_warning = "; ".join(
            runtime_native_evidence_compare_warning_messages
        )
        runtime_native_evidence_compare_warning_reasons = list(
            dict.fromkeys(runtime_native_evidence_compare_warning_reasons)
        )
    runtime_threshold_drift_reasons: list[str] = []
    runtime_threshold_drift_parts: list[str] = []
    if runtime_lane_execution_exec_lane_warn_min_rows_mismatch:
        runtime_threshold_drift_reasons.append("runtime_lane_execution_exec_lane_warn_min_rows_mismatch")
        runtime_threshold_drift_parts.append(
            "exec_lane_warn_min_rows="
            f"expected:{runtime_lane_execution_warn_min_exec_rows},"
            f"observed:{runtime_lane_execution_exec_lane_warn_min_rows_counts_text}"
        )
    if runtime_lane_execution_exec_lane_hold_min_rows_mismatch:
        runtime_threshold_drift_reasons.append("runtime_lane_execution_exec_lane_hold_min_rows_mismatch")
        runtime_threshold_drift_parts.append(
            "exec_lane_hold_min_rows="
            f"expected:{runtime_lane_execution_hold_min_exec_rows},"
            f"observed:{runtime_lane_execution_exec_lane_hold_min_rows_counts_text}"
        )
    if runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch:
        runtime_threshold_drift_reasons.append("runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch")
        runtime_threshold_drift_parts.append(
            "runtime_compare_warn_min_artifacts_with_diffs="
            f"expected:{runtime_evidence_compare_warn_min_artifacts_with_diffs},"
            f"observed:{runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_text}"
        )
    if runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch:
        runtime_threshold_drift_reasons.append("runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch")
        runtime_threshold_drift_parts.append(
            "runtime_compare_hold_min_artifacts_with_diffs="
            f"expected:{runtime_evidence_compare_hold_min_artifacts_with_diffs},"
            f"observed:{runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_fidelity_score_avg_warn_min="
            f"expected:{phase2_sensor_fidelity_score_avg_warn_min:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_fidelity_score_avg_hold_min="
            f"expected:{phase2_sensor_fidelity_score_avg_hold_min:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_frame_count_avg_warn_min="
            f"expected:{phase2_sensor_frame_count_avg_warn_min:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_frame_count_avg_hold_min="
            f"expected:{phase2_sensor_frame_count_avg_hold_min:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_camera_noise_stddev_px_avg_warn_max="
            f"expected:{phase2_sensor_camera_noise_stddev_px_avg_warn_max:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_camera_noise_stddev_px_avg_hold_max="
            f"expected:{phase2_sensor_camera_noise_stddev_px_avg_hold_max:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_lidar_point_count_avg_warn_min="
            f"expected:{phase2_sensor_lidar_point_count_avg_warn_min:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_lidar_point_count_avg_hold_min="
            f"expected:{phase2_sensor_lidar_point_count_avg_hold_min:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_radar_false_positive_rate_avg_warn_max="
            f"expected:{phase2_sensor_radar_false_positive_rate_avg_warn_max:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text}"
        )
    if runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch:
        runtime_threshold_drift_reasons.append(
            "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch"
        )
        runtime_threshold_drift_parts.append(
            "phase2_sensor_radar_false_positive_rate_avg_hold_max="
            f"expected:{phase2_sensor_radar_false_positive_rate_avg_hold_max:.3f},"
            "observed:"
            f"{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text}"
        )
    runtime_threshold_drift_detected = bool(runtime_threshold_drift_parts)
    runtime_threshold_drift_reasons = list(dict.fromkeys(runtime_threshold_drift_reasons))
    runtime_threshold_drift_summary_text = (
        "; ".join(runtime_threshold_drift_parts)
        if runtime_threshold_drift_detected
        else "none"
    )
    if runtime_threshold_drift_detected:
        if (
            runtime_lane_execution_exec_lane_hold_min_rows_mismatch
            or runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch
            or runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch
            or runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch
            or runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch
            or runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch
            or runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch
        ):
            runtime_threshold_drift_severity = "HOLD"
        else:
            runtime_threshold_drift_severity = "WARN"
    else:
        runtime_threshold_drift_severity = "NONE"
    runtime_threshold_drift_hold_detected = runtime_threshold_drift_severity == "HOLD"
    timing_warning_messages: list[str] = []
    timing_warning_reasons: list[str] = []
    timing_warning_score = 0.0
    timing_threshold: dict[str, int | float | bool] = {}
    if (
        timing_total_warn_ms > 0
        and timing_total_ms is not None
    ):
        threshold_ratio = (
            float(timing_total_ms) / float(timing_total_warn_ms)
            if timing_total_warn_ms > 0
            else 0.0
        )
        threshold_exceeded = bool(timing_total_ms >= timing_total_warn_ms)
        timing_threshold = {
            "current_ms": int(timing_total_ms),
            "warn_ms": int(timing_total_warn_ms),
            "ratio": float(threshold_ratio),
            "exceeded": threshold_exceeded,
        }
        if threshold_exceeded:
            timing_warning_messages.append(
                f"timing_total_ms={timing_total_ms} exceeded threshold={timing_total_warn_ms}"
            )
            timing_warning_reasons.append("total_threshold")
            timing_warning_score = max(timing_warning_score, threshold_ratio)

    timing_regression: dict[str, int | float | bool] = {}
    if timing_total_ms is not None and timing_regression_baseline_ms > 0:
        delta_ms = int(timing_total_ms) - int(timing_regression_baseline_ms)
        delta_ratio = (delta_ms / timing_regression_baseline_ms) if timing_regression_baseline_ms > 0 else 0.0
        ratio_multiple = (
            delta_ratio / timing_regression_warn_ratio
            if timing_regression_warn_ratio > 0
            else 0.0
        )
        regression_exceeded = (
            timing_regression_warn_ratio > 0 and delta_ratio >= timing_regression_warn_ratio
        )
        timing_regression = {
            "current_ms": int(timing_total_ms),
            "baseline_ms": int(timing_regression_baseline_ms),
            "delta_ms": int(delta_ms),
            "delta_ratio": float(delta_ratio),
            "warn_ratio": float(timing_regression_warn_ratio),
            "ratio_multiple": float(ratio_multiple),
            "exceeded": bool(regression_exceeded),
        }
        if regression_exceeded:
            timing_warning_messages.append(
                "timing_regression="
                f"{delta_ratio:.3f} exceeded ratio={timing_regression_warn_ratio:.3f} "
                f"(current={timing_total_ms}, baseline={timing_regression_baseline_ms})"
            )
            timing_warning_reasons.append("regression_ratio")
            timing_warning_score = max(timing_warning_score, ratio_multiple)

    timing_warning = "; ".join(timing_warning_messages)
    timing_warning_severity = _timing_warning_severity(timing_warning_score) if timing_warning else ""
    if timing_warning:
        if status in {"PASS", "INFO"}:
            status = "WARN"

    if phase3_vehicle_dynamics_summary:
        try:
            phase3_vehicle_eval_count = int(phase3_vehicle_dynamics_summary.get("evaluated_manifest_count", 0))
        except (TypeError, ValueError):
            phase3_vehicle_eval_count = 0
        if phase3_vehicle_eval_count > 0:
            try:
                max_final_speed = float(phase3_vehicle_dynamics_summary.get("max_final_speed_mps", 0.0) or 0.0)
            except (TypeError, ValueError):
                max_final_speed = 0.0
            try:
                max_final_position = float(
                    phase3_vehicle_dynamics_summary.get("max_final_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_final_position = 0.0
            try:
                max_delta_speed = float(phase3_vehicle_dynamics_summary.get("max_delta_speed_mps", 0.0) or 0.0)
            except (TypeError, ValueError):
                max_delta_speed = 0.0
            try:
                max_delta_position = float(
                    phase3_vehicle_dynamics_summary.get("max_delta_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_delta_position = 0.0
            try:
                min_final_heading = float(
                    phase3_vehicle_dynamics_summary.get("min_final_heading_deg", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                min_final_heading = 0.0
            try:
                max_final_heading = float(
                    phase3_vehicle_dynamics_summary.get("max_final_heading_deg", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_final_heading = 0.0
            try:
                min_final_lateral_position = float(
                    phase3_vehicle_dynamics_summary.get("min_final_lateral_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                min_final_lateral_position = 0.0
            try:
                max_final_lateral_position = float(
                    phase3_vehicle_dynamics_summary.get("max_final_lateral_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_final_lateral_position = 0.0
            try:
                min_delta_heading = float(
                    phase3_vehicle_dynamics_summary.get("min_delta_heading_deg", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                min_delta_heading = 0.0
            try:
                max_delta_heading = float(
                    phase3_vehicle_dynamics_summary.get("max_delta_heading_deg", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_delta_heading = 0.0
            try:
                min_delta_lateral_position = float(
                    phase3_vehicle_dynamics_summary.get("min_delta_lateral_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                min_delta_lateral_position = 0.0
            try:
                max_delta_lateral_position = float(
                    phase3_vehicle_dynamics_summary.get("max_delta_lateral_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_delta_lateral_position = 0.0
            try:
                min_delta_yaw_rate = float(
                    phase3_vehicle_dynamics_summary.get("min_delta_yaw_rate_rps", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                min_delta_yaw_rate = 0.0
            try:
                max_delta_yaw_rate = float(
                    phase3_vehicle_dynamics_summary.get("max_delta_yaw_rate_rps", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_delta_yaw_rate = 0.0
            try:
                max_abs_yaw_rate = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_yaw_rate_rps", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_yaw_rate = 0.0
            try:
                max_abs_lateral_velocity = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_lateral_velocity_mps", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_lateral_velocity = 0.0
            try:
                max_abs_accel = float(phase3_vehicle_dynamics_summary.get("max_abs_accel_mps2", 0.0) or 0.0)
            except (TypeError, ValueError):
                max_abs_accel = 0.0
            try:
                max_abs_lateral_accel = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_lateral_accel_mps2", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_lateral_accel = 0.0
            try:
                max_abs_yaw_accel = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_yaw_accel_rps2", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_yaw_accel = 0.0
            try:
                max_abs_jerk = float(phase3_vehicle_dynamics_summary.get("max_abs_jerk_mps3", 0.0) or 0.0)
            except (TypeError, ValueError):
                max_abs_jerk = 0.0
            try:
                max_abs_lateral_jerk = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_lateral_jerk_mps3", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_lateral_jerk = 0.0
            try:
                max_abs_yaw_jerk = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_yaw_jerk_rps3", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_yaw_jerk = 0.0
            try:
                max_abs_lateral_position = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_lateral_position_m", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_lateral_position = 0.0
            try:
                min_road_grade = float(phase3_vehicle_dynamics_summary.get("min_road_grade_percent", 0.0) or 0.0)
            except (TypeError, ValueError):
                min_road_grade = 0.0
            try:
                max_road_grade = float(phase3_vehicle_dynamics_summary.get("max_road_grade_percent", 0.0) or 0.0)
            except (TypeError, ValueError):
                max_road_grade = 0.0
            try:
                max_abs_grade_force = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_grade_force_n", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_grade_force = 0.0
            try:
                control_overlap_ratio_max = float(
                    phase3_vehicle_dynamics_summary.get("control_throttle_brake_overlap_ratio_max", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                control_overlap_ratio_max = 0.0
            try:
                control_steering_rate_max = float(
                    phase3_vehicle_dynamics_summary.get("control_max_abs_steering_rate_degps_max", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                control_steering_rate_max = 0.0
            try:
                control_throttle_plus_brake_max = float(
                    phase3_vehicle_dynamics_summary.get("control_max_throttle_plus_brake_max", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                control_throttle_plus_brake_max = 0.0
            try:
                max_speed_tracking_error = float(
                    phase3_vehicle_dynamics_summary.get("max_speed_tracking_error_mps", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_speed_tracking_error = 0.0
            try:
                max_abs_speed_tracking_error = float(
                    phase3_vehicle_dynamics_summary.get("max_abs_speed_tracking_error_mps", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                max_abs_speed_tracking_error = 0.0
            highest_speed_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_speed_batch_id", "")).strip() or "batch_unknown"
            )
            highest_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_position_batch_id", "")).strip() or "batch_unknown"
            )
            highest_delta_speed_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_delta_speed_batch_id", "")).strip() or "batch_unknown"
            )
            highest_delta_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_delta_position_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_heading_batch = (
                str(phase3_vehicle_dynamics_summary.get("lowest_heading_batch_id", "")).strip() or "batch_unknown"
            )
            highest_heading_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_heading_batch_id", "")).strip() or "batch_unknown"
            )
            lowest_lateral_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("lowest_lateral_position_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_lateral_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_lateral_position_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_delta_heading_batch = (
                str(phase3_vehicle_dynamics_summary.get("lowest_delta_heading_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_delta_heading_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_delta_heading_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_delta_lateral_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("lowest_delta_lateral_position_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_delta_lateral_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_delta_lateral_position_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_delta_yaw_rate_batch = (
                str(phase3_vehicle_dynamics_summary.get("lowest_delta_yaw_rate_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_delta_yaw_rate_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_delta_yaw_rate_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_yaw_rate_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_yaw_rate_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_lateral_velocity_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_velocity_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_accel_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_accel_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_lateral_accel_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_accel_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_yaw_accel_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_yaw_accel_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_jerk_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_jerk_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_lateral_jerk_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_jerk_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_yaw_jerk_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_yaw_jerk_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_lateral_position_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_position_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_road_grade_batch = (
                str(phase3_vehicle_dynamics_summary.get("lowest_road_grade_batch_id", "")).strip() or "batch_unknown"
            )
            highest_road_grade_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_road_grade_batch_id", "")).strip() or "batch_unknown"
            )
            highest_abs_grade_force_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_grade_force_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_control_overlap_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_control_overlap_ratio_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_control_steering_rate_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_control_steering_rate_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_control_throttle_plus_brake_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_control_throttle_plus_brake_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_speed_tracking_error_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_speed_tracking_error_batch_id", "")).strip()
                or "batch_unknown"
            )
            highest_abs_speed_tracking_error_batch = (
                str(phase3_vehicle_dynamics_summary.get("highest_abs_speed_tracking_error_batch_id", "")).strip()
                or "batch_unknown"
            )
            road_grade_abs_percent = max(abs(min_road_grade), abs(max_road_grade))
            if abs(min_road_grade) > abs(max_road_grade):
                road_grade_abs_batch = lowest_road_grade_batch
            elif abs(max_road_grade) > abs(min_road_grade):
                road_grade_abs_batch = highest_road_grade_batch
            else:
                road_grade_abs_batch = min(lowest_road_grade_batch, highest_road_grade_batch)
            final_heading_abs_deg = max(abs(min_final_heading), abs(max_final_heading))
            if abs(min_final_heading) > abs(max_final_heading):
                final_heading_abs_batch = lowest_heading_batch
            elif abs(max_final_heading) > abs(min_final_heading):
                final_heading_abs_batch = highest_heading_batch
            else:
                final_heading_abs_batch = min(lowest_heading_batch, highest_heading_batch)
            final_lateral_position_abs_m = max(abs(min_final_lateral_position), abs(max_final_lateral_position))
            if abs(min_final_lateral_position) > abs(max_final_lateral_position):
                final_lateral_position_abs_batch = lowest_lateral_position_batch
            elif abs(max_final_lateral_position) > abs(min_final_lateral_position):
                final_lateral_position_abs_batch = highest_lateral_position_batch
            else:
                final_lateral_position_abs_batch = min(
                    lowest_lateral_position_batch, highest_lateral_position_batch
                )
            delta_heading_abs_deg = max(abs(min_delta_heading), abs(max_delta_heading))
            if abs(min_delta_heading) > abs(max_delta_heading):
                delta_heading_abs_batch = lowest_delta_heading_batch
            elif abs(max_delta_heading) > abs(min_delta_heading):
                delta_heading_abs_batch = highest_delta_heading_batch
            else:
                delta_heading_abs_batch = min(lowest_delta_heading_batch, highest_delta_heading_batch)
            delta_lateral_position_abs_m = max(
                abs(min_delta_lateral_position), abs(max_delta_lateral_position)
            )
            if abs(min_delta_lateral_position) > abs(max_delta_lateral_position):
                delta_lateral_position_abs_batch = lowest_delta_lateral_position_batch
            elif abs(max_delta_lateral_position) > abs(min_delta_lateral_position):
                delta_lateral_position_abs_batch = highest_delta_lateral_position_batch
            else:
                delta_lateral_position_abs_batch = min(
                    lowest_delta_lateral_position_batch, highest_delta_lateral_position_batch
                )
            delta_yaw_rate_abs_rps = max(abs(min_delta_yaw_rate), abs(max_delta_yaw_rate))
            if abs(min_delta_yaw_rate) > abs(max_delta_yaw_rate):
                delta_yaw_rate_abs_batch = lowest_delta_yaw_rate_batch
            elif abs(max_delta_yaw_rate) > abs(min_delta_yaw_rate):
                delta_yaw_rate_abs_batch = highest_delta_yaw_rate_batch
            else:
                delta_yaw_rate_abs_batch = min(lowest_delta_yaw_rate_batch, highest_delta_yaw_rate_batch)
            def _append_threshold_warning(
                *,
                metric_label: str,
                value: float,
                warn_max: float,
                hold_max: float,
                batch_id: str,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    phase3_vehicle_dynamics_warning_messages.append(
                        f"{metric_label}={value:.3f} exceeded hold_max={hold_max:.3f} (batch={batch_id})"
                    )
                    phase3_vehicle_dynamics_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase3_vehicle_dynamics_warning_messages.append(
                        f"{metric_label}={value:.3f} exceeded warn_max={warn_max:.3f} (batch={batch_id})"
                    )
                    phase3_vehicle_dynamics_warning_reasons.append(reason_warn)

            _append_threshold_warning(
                metric_label="phase3_vehicle_final_speed_mps",
                value=max_final_speed,
                warn_max=phase3_vehicle_final_speed_warn_max,
                hold_max=phase3_vehicle_final_speed_hold_max,
                batch_id=highest_speed_batch,
                reason_warn="phase3_vehicle_final_speed_above_warn_max",
                reason_hold="phase3_vehicle_final_speed_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_final_position_m",
                value=max_final_position,
                warn_max=phase3_vehicle_final_position_warn_max,
                hold_max=phase3_vehicle_final_position_hold_max,
                batch_id=highest_position_batch,
                reason_warn="phase3_vehicle_final_position_above_warn_max",
                reason_hold="phase3_vehicle_final_position_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_delta_speed_mps",
                value=max_delta_speed,
                warn_max=phase3_vehicle_delta_speed_warn_max,
                hold_max=phase3_vehicle_delta_speed_hold_max,
                batch_id=highest_delta_speed_batch,
                reason_warn="phase3_vehicle_delta_speed_above_warn_max",
                reason_hold="phase3_vehicle_delta_speed_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_delta_position_m",
                value=max_delta_position,
                warn_max=phase3_vehicle_delta_position_warn_max,
                hold_max=phase3_vehicle_delta_position_hold_max,
                batch_id=highest_delta_position_batch,
                reason_warn="phase3_vehicle_delta_position_above_warn_max",
                reason_hold="phase3_vehicle_delta_position_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_final_heading_abs_deg",
                value=final_heading_abs_deg,
                warn_max=phase3_vehicle_final_heading_abs_warn_max,
                hold_max=phase3_vehicle_final_heading_abs_hold_max,
                batch_id=final_heading_abs_batch,
                reason_warn="phase3_vehicle_final_heading_abs_above_warn_max",
                reason_hold="phase3_vehicle_final_heading_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_final_lateral_position_abs_m",
                value=final_lateral_position_abs_m,
                warn_max=phase3_vehicle_final_lateral_position_abs_warn_max,
                hold_max=phase3_vehicle_final_lateral_position_abs_hold_max,
                batch_id=final_lateral_position_abs_batch,
                reason_warn="phase3_vehicle_final_lateral_position_abs_above_warn_max",
                reason_hold="phase3_vehicle_final_lateral_position_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_delta_heading_abs_deg",
                value=delta_heading_abs_deg,
                warn_max=phase3_vehicle_delta_heading_abs_warn_max,
                hold_max=phase3_vehicle_delta_heading_abs_hold_max,
                batch_id=delta_heading_abs_batch,
                reason_warn="phase3_vehicle_delta_heading_abs_above_warn_max",
                reason_hold="phase3_vehicle_delta_heading_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_delta_lateral_position_abs_m",
                value=delta_lateral_position_abs_m,
                warn_max=phase3_vehicle_delta_lateral_position_abs_warn_max,
                hold_max=phase3_vehicle_delta_lateral_position_abs_hold_max,
                batch_id=delta_lateral_position_abs_batch,
                reason_warn="phase3_vehicle_delta_lateral_position_abs_above_warn_max",
                reason_hold="phase3_vehicle_delta_lateral_position_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_yaw_rate_abs_rps",
                value=max_abs_yaw_rate,
                warn_max=phase3_vehicle_yaw_rate_abs_warn_max,
                hold_max=phase3_vehicle_yaw_rate_abs_hold_max,
                batch_id=highest_abs_yaw_rate_batch,
                reason_warn="phase3_vehicle_yaw_rate_abs_above_warn_max",
                reason_hold="phase3_vehicle_yaw_rate_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_delta_yaw_rate_abs_rps",
                value=delta_yaw_rate_abs_rps,
                warn_max=phase3_vehicle_delta_yaw_rate_abs_warn_max,
                hold_max=phase3_vehicle_delta_yaw_rate_abs_hold_max,
                batch_id=delta_yaw_rate_abs_batch,
                reason_warn="phase3_vehicle_delta_yaw_rate_abs_above_warn_max",
                reason_hold="phase3_vehicle_delta_yaw_rate_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_lateral_velocity_abs_mps",
                value=max_abs_lateral_velocity,
                warn_max=phase3_vehicle_lateral_velocity_abs_warn_max,
                hold_max=phase3_vehicle_lateral_velocity_abs_hold_max,
                batch_id=highest_abs_lateral_velocity_batch,
                reason_warn="phase3_vehicle_lateral_velocity_abs_above_warn_max",
                reason_hold="phase3_vehicle_lateral_velocity_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_accel_abs_mps2",
                value=max_abs_accel,
                warn_max=phase3_vehicle_accel_abs_warn_max,
                hold_max=phase3_vehicle_accel_abs_hold_max,
                batch_id=highest_abs_accel_batch,
                reason_warn="phase3_vehicle_accel_abs_above_warn_max",
                reason_hold="phase3_vehicle_accel_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_lateral_accel_abs_mps2",
                value=max_abs_lateral_accel,
                warn_max=phase3_vehicle_lateral_accel_abs_warn_max,
                hold_max=phase3_vehicle_lateral_accel_abs_hold_max,
                batch_id=highest_abs_lateral_accel_batch,
                reason_warn="phase3_vehicle_lateral_accel_abs_above_warn_max",
                reason_hold="phase3_vehicle_lateral_accel_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_yaw_accel_abs_rps2",
                value=max_abs_yaw_accel,
                warn_max=phase3_vehicle_yaw_accel_abs_warn_max,
                hold_max=phase3_vehicle_yaw_accel_abs_hold_max,
                batch_id=highest_abs_yaw_accel_batch,
                reason_warn="phase3_vehicle_yaw_accel_abs_above_warn_max",
                reason_hold="phase3_vehicle_yaw_accel_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_jerk_abs_mps3",
                value=max_abs_jerk,
                warn_max=phase3_vehicle_jerk_abs_warn_max,
                hold_max=phase3_vehicle_jerk_abs_hold_max,
                batch_id=highest_abs_jerk_batch,
                reason_warn="phase3_vehicle_jerk_abs_above_warn_max",
                reason_hold="phase3_vehicle_jerk_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_lateral_jerk_abs_mps3",
                value=max_abs_lateral_jerk,
                warn_max=phase3_vehicle_lateral_jerk_abs_warn_max,
                hold_max=phase3_vehicle_lateral_jerk_abs_hold_max,
                batch_id=highest_abs_lateral_jerk_batch,
                reason_warn="phase3_vehicle_lateral_jerk_abs_above_warn_max",
                reason_hold="phase3_vehicle_lateral_jerk_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_yaw_jerk_abs_rps3",
                value=max_abs_yaw_jerk,
                warn_max=phase3_vehicle_yaw_jerk_abs_warn_max,
                hold_max=phase3_vehicle_yaw_jerk_abs_hold_max,
                batch_id=highest_abs_yaw_jerk_batch,
                reason_warn="phase3_vehicle_yaw_jerk_abs_above_warn_max",
                reason_hold="phase3_vehicle_yaw_jerk_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_lateral_position_abs_m",
                value=max_abs_lateral_position,
                warn_max=phase3_vehicle_lateral_position_abs_warn_max,
                hold_max=phase3_vehicle_lateral_position_abs_hold_max,
                batch_id=highest_abs_lateral_position_batch,
                reason_warn="phase3_vehicle_lateral_position_abs_above_warn_max",
                reason_hold="phase3_vehicle_lateral_position_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_road_grade_abs_percent",
                value=road_grade_abs_percent,
                warn_max=phase3_vehicle_road_grade_abs_warn_max,
                hold_max=phase3_vehicle_road_grade_abs_hold_max,
                batch_id=road_grade_abs_batch,
                reason_warn="phase3_vehicle_road_grade_abs_above_warn_max",
                reason_hold="phase3_vehicle_road_grade_abs_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_grade_force_n",
                value=max_abs_grade_force,
                warn_max=phase3_vehicle_grade_force_warn_max,
                hold_max=phase3_vehicle_grade_force_hold_max,
                batch_id=highest_abs_grade_force_batch,
                reason_warn="phase3_vehicle_grade_force_above_warn_max",
                reason_hold="phase3_vehicle_grade_force_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_control_overlap_ratio",
                value=control_overlap_ratio_max,
                warn_max=phase3_vehicle_control_overlap_ratio_warn_max,
                hold_max=phase3_vehicle_control_overlap_ratio_hold_max,
                batch_id=highest_control_overlap_batch,
                reason_warn="phase3_vehicle_control_overlap_ratio_above_warn_max",
                reason_hold="phase3_vehicle_control_overlap_ratio_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_control_steering_rate_degps",
                value=control_steering_rate_max,
                warn_max=phase3_vehicle_control_steering_rate_warn_max,
                hold_max=phase3_vehicle_control_steering_rate_hold_max,
                batch_id=highest_control_steering_rate_batch,
                reason_warn="phase3_vehicle_control_steering_rate_above_warn_max",
                reason_hold="phase3_vehicle_control_steering_rate_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_control_throttle_plus_brake",
                value=control_throttle_plus_brake_max,
                warn_max=phase3_vehicle_control_throttle_plus_brake_warn_max,
                hold_max=phase3_vehicle_control_throttle_plus_brake_hold_max,
                batch_id=highest_control_throttle_plus_brake_batch,
                reason_warn="phase3_vehicle_control_throttle_plus_brake_above_warn_max",
                reason_hold="phase3_vehicle_control_throttle_plus_brake_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_speed_tracking_error_mps",
                value=max_speed_tracking_error,
                warn_max=phase3_vehicle_speed_tracking_error_warn_max,
                hold_max=phase3_vehicle_speed_tracking_error_hold_max,
                batch_id=highest_speed_tracking_error_batch,
                reason_warn="phase3_vehicle_speed_tracking_error_above_warn_max",
                reason_hold="phase3_vehicle_speed_tracking_error_above_hold_max",
            )
            _append_threshold_warning(
                metric_label="phase3_vehicle_speed_tracking_abs_error_mps",
                value=max_abs_speed_tracking_error,
                warn_max=phase3_vehicle_speed_tracking_error_abs_warn_max,
                hold_max=phase3_vehicle_speed_tracking_error_abs_hold_max,
                batch_id=highest_abs_speed_tracking_error_batch,
                reason_warn="phase3_vehicle_speed_tracking_abs_error_above_warn_max",
                reason_hold="phase3_vehicle_speed_tracking_abs_error_above_hold_max",
            )

    if not phase3_vehicle_dynamics_warning_messages and phase3_vehicle_dynamics_violation_summary:
        fallback_rows = _select_phase3_violation_fallback_rows(phase3_vehicle_dynamics_violation_summary)
        for fallback_row in fallback_rows:
            severity = str(fallback_row.get("severity", "")).strip().upper()
            metric = str(fallback_row.get("metric", "")).strip()
            max_value = float(fallback_row.get("max_value", 0.0))
            threshold = float(fallback_row.get("threshold", 0.0))
            batch_id = str(fallback_row.get("max_batch_id", "")).strip() or "batch_unknown"
            threshold_label = "hold_max" if severity == "HOLD" else "warn_max"
            phase3_vehicle_dynamics_warning_messages.append(
                f"phase3_vehicle_{metric}={max_value:.3f} exceeded {threshold_label}={threshold:.3f} "
                f"(batch={batch_id})"
            )
            reason = _phase3_metric_reason(metric, severity)
            if reason:
                phase3_vehicle_dynamics_warning_reasons.append(reason)
            if severity == "HOLD":
                status = "HOLD"

    phase3_vehicle_dynamics_warning = ""
    if phase3_vehicle_dynamics_warning_messages:
        phase3_vehicle_dynamics_warning = "; ".join(phase3_vehicle_dynamics_warning_messages)
        phase3_vehicle_dynamics_warning_reasons = list(dict.fromkeys(phase3_vehicle_dynamics_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"

    phase3_core_sim_warning = ""
    if phase3_core_sim_summary:
        try:
            phase3_core_sim_evaluated_count = int(phase3_core_sim_summary.get("evaluated_manifest_count", 0))
        except (TypeError, ValueError):
            phase3_core_sim_evaluated_count = 0
        if phase3_core_sim_evaluated_count > 0:
            try:
                min_ttc_same_lane = float(phase3_core_sim_summary.get("min_ttc_same_lane_sec"))
            except (TypeError, ValueError):
                min_ttc_same_lane = math.inf
            try:
                min_ttc_any_lane = float(phase3_core_sim_summary.get("min_ttc_any_lane_sec"))
            except (TypeError, ValueError):
                min_ttc_any_lane = math.inf
            lowest_same_lane_batch = (
                str(phase3_core_sim_summary.get("lowest_same_lane_batch_id", "")).strip() or "batch_unknown"
            )
            lowest_any_lane_batch = (
                str(phase3_core_sim_summary.get("lowest_any_lane_batch_id", "")).strip() or "batch_unknown"
            )
            collision_count = int(phase3_core_sim_summary.get("collision_manifest_count", 0) or 0)
            timeout_count = int(phase3_core_sim_summary.get("timeout_manifest_count", 0) or 0)
            gate_hold_count = int(phase3_core_sim_summary.get("gate_hold_manifest_count", 0) or 0)

            def _append_core_sim_min_ttc_warning(
                *,
                metric_label: str,
                value: float,
                warn_min: float,
                hold_min: float,
                batch_id: str,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if not math.isfinite(value):
                    return
                if hold_min > 0 and value <= hold_min:
                    phase3_core_sim_warning_messages.append(
                        f"{metric_label}={value:.3f} below hold_min={hold_min:.3f} (batch={batch_id})"
                    )
                    phase3_core_sim_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_min > 0 and value <= warn_min:
                    phase3_core_sim_warning_messages.append(
                        f"{metric_label}={value:.3f} below warn_min={warn_min:.3f} (batch={batch_id})"
                    )
                    phase3_core_sim_warning_reasons.append(reason_warn)

            def _append_core_sim_count_warning(
                *,
                metric_label: str,
                value: int,
                warn_max: int,
                hold_max: int,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if value > hold_max:
                    phase3_core_sim_warning_messages.append(
                        f"{metric_label}={value} exceeded hold_max={hold_max}"
                    )
                    phase3_core_sim_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if value > warn_max:
                    phase3_core_sim_warning_messages.append(
                        f"{metric_label}={value} exceeded warn_max={warn_max}"
                    )
                    phase3_core_sim_warning_reasons.append(reason_warn)

            _append_core_sim_min_ttc_warning(
                metric_label="phase3_core_sim_min_ttc_same_lane_sec",
                value=min_ttc_same_lane,
                warn_min=phase3_core_sim_min_ttc_same_lane_warn_min,
                hold_min=phase3_core_sim_min_ttc_same_lane_hold_min,
                batch_id=lowest_same_lane_batch,
                reason_warn="phase3_core_sim_min_ttc_same_lane_below_warn_min",
                reason_hold="phase3_core_sim_min_ttc_same_lane_below_hold_min",
            )
            _append_core_sim_min_ttc_warning(
                metric_label="phase3_core_sim_min_ttc_any_lane_sec",
                value=min_ttc_any_lane,
                warn_min=phase3_core_sim_min_ttc_any_lane_warn_min,
                hold_min=phase3_core_sim_min_ttc_any_lane_hold_min,
                batch_id=lowest_any_lane_batch,
                reason_warn="phase3_core_sim_min_ttc_any_lane_below_warn_min",
                reason_hold="phase3_core_sim_min_ttc_any_lane_below_hold_min",
            )
            _append_core_sim_count_warning(
                metric_label="phase3_core_sim_collision_manifest_count",
                value=max(0, collision_count),
                warn_max=phase3_core_sim_collision_warn_max,
                hold_max=phase3_core_sim_collision_hold_max,
                reason_warn="phase3_core_sim_collision_count_above_warn_max",
                reason_hold="phase3_core_sim_collision_count_above_hold_max",
            )
            _append_core_sim_count_warning(
                metric_label="phase3_core_sim_timeout_manifest_count",
                value=max(0, timeout_count),
                warn_max=phase3_core_sim_timeout_warn_max,
                hold_max=phase3_core_sim_timeout_hold_max,
                reason_warn="phase3_core_sim_timeout_count_above_warn_max",
                reason_hold="phase3_core_sim_timeout_count_above_hold_max",
            )
            _append_core_sim_count_warning(
                metric_label="phase3_core_sim_gate_hold_manifest_count",
                value=max(0, gate_hold_count),
                warn_max=phase3_core_sim_gate_hold_warn_max,
                hold_max=phase3_core_sim_gate_hold_hold_max,
                reason_warn="phase3_core_sim_gate_hold_count_above_warn_max",
                reason_hold="phase3_core_sim_gate_hold_count_above_hold_max",
            )
            phase3_core_sim_min_ttc_same_lane_warn_min_mismatch = _threshold_float_count_mismatch(
                phase3_core_sim_gate_min_ttc_same_lane_sec_counts,
                phase3_core_sim_min_ttc_same_lane_warn_min,
            )
            if phase3_core_sim_min_ttc_same_lane_warn_min_mismatch:
                phase3_core_sim_warning_messages.append(
                    "phase3_core_sim_min_ttc_same_lane_warn_min_mismatch="
                    f"expected:{phase3_core_sim_min_ttc_same_lane_warn_min:.3f},"
                    f"observed:{phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text}"
                )
                phase3_core_sim_warning_reasons.append(
                    "phase3_core_sim_min_ttc_same_lane_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_core_sim_min_ttc_same_lane_hold_min_mismatch = _threshold_float_count_mismatch(
                phase3_core_sim_gate_min_ttc_same_lane_sec_counts,
                phase3_core_sim_min_ttc_same_lane_hold_min,
            )
            if phase3_core_sim_min_ttc_same_lane_hold_min_mismatch:
                phase3_core_sim_warning_messages.append(
                    "phase3_core_sim_min_ttc_same_lane_hold_min_mismatch="
                    f"expected:{phase3_core_sim_min_ttc_same_lane_hold_min:.3f},"
                    f"observed:{phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text}"
                )
                phase3_core_sim_warning_reasons.append(
                    "phase3_core_sim_min_ttc_same_lane_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_core_sim_min_ttc_any_lane_warn_min_mismatch = _threshold_float_count_mismatch(
                phase3_core_sim_gate_min_ttc_any_lane_sec_counts,
                phase3_core_sim_min_ttc_any_lane_warn_min,
            )
            if phase3_core_sim_min_ttc_any_lane_warn_min_mismatch:
                phase3_core_sim_warning_messages.append(
                    "phase3_core_sim_min_ttc_any_lane_warn_min_mismatch="
                    f"expected:{phase3_core_sim_min_ttc_any_lane_warn_min:.3f},"
                    f"observed:{phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text}"
                )
                phase3_core_sim_warning_reasons.append(
                    "phase3_core_sim_min_ttc_any_lane_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_core_sim_min_ttc_any_lane_hold_min_mismatch = _threshold_float_count_mismatch(
                phase3_core_sim_gate_min_ttc_any_lane_sec_counts,
                phase3_core_sim_min_ttc_any_lane_hold_min,
            )
            if phase3_core_sim_min_ttc_any_lane_hold_min_mismatch:
                phase3_core_sim_warning_messages.append(
                    "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch="
                    f"expected:{phase3_core_sim_min_ttc_any_lane_hold_min:.3f},"
                    f"observed:{phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text}"
                )
                phase3_core_sim_warning_reasons.append(
                    "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"

    if phase3_core_sim_warning_messages:
        phase3_core_sim_warning = "; ".join(phase3_core_sim_warning_messages)
        phase3_core_sim_warning_reasons = list(dict.fromkeys(phase3_core_sim_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"
    phase3_core_sim_threshold_drift_reasons: list[str] = []
    phase3_core_sim_threshold_drift_parts: list[str] = []
    if phase3_core_sim_min_ttc_same_lane_warn_min_mismatch:
        phase3_core_sim_threshold_drift_reasons.append(
            "phase3_core_sim_min_ttc_same_lane_warn_min_mismatch"
        )
        phase3_core_sim_threshold_drift_parts.append(
            "min_ttc_same_lane_warn_min="
            f"expected:{phase3_core_sim_min_ttc_same_lane_warn_min:.3f},"
            f"observed:{phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text}"
        )
    if phase3_core_sim_min_ttc_same_lane_hold_min_mismatch:
        phase3_core_sim_threshold_drift_reasons.append(
            "phase3_core_sim_min_ttc_same_lane_hold_min_mismatch"
        )
        phase3_core_sim_threshold_drift_parts.append(
            "min_ttc_same_lane_hold_min="
            f"expected:{phase3_core_sim_min_ttc_same_lane_hold_min:.3f},"
            f"observed:{phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text}"
        )
    if phase3_core_sim_min_ttc_any_lane_warn_min_mismatch:
        phase3_core_sim_threshold_drift_reasons.append(
            "phase3_core_sim_min_ttc_any_lane_warn_min_mismatch"
        )
        phase3_core_sim_threshold_drift_parts.append(
            "min_ttc_any_lane_warn_min="
            f"expected:{phase3_core_sim_min_ttc_any_lane_warn_min:.3f},"
            f"observed:{phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text}"
        )
    if phase3_core_sim_min_ttc_any_lane_hold_min_mismatch:
        phase3_core_sim_threshold_drift_reasons.append(
            "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch"
        )
        phase3_core_sim_threshold_drift_parts.append(
            "min_ttc_any_lane_hold_min="
            f"expected:{phase3_core_sim_min_ttc_any_lane_hold_min:.3f},"
            f"observed:{phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text}"
        )
    phase3_core_sim_threshold_drift_detected = bool(phase3_core_sim_threshold_drift_parts)
    phase3_core_sim_threshold_drift_reasons = list(dict.fromkeys(phase3_core_sim_threshold_drift_reasons))
    phase3_core_sim_threshold_drift_summary_text = (
        "; ".join(phase3_core_sim_threshold_drift_parts)
        if phase3_core_sim_threshold_drift_detected
        else "n/a"
    )
    if phase3_core_sim_threshold_drift_detected:
        if (
            phase3_core_sim_min_ttc_same_lane_hold_min_mismatch
            or phase3_core_sim_min_ttc_any_lane_hold_min_mismatch
        ):
            phase3_core_sim_threshold_drift_severity = "HOLD"
        else:
            phase3_core_sim_threshold_drift_severity = "WARN"
    else:
        phase3_core_sim_threshold_drift_severity = "NONE"

    phase3_core_sim_matrix_warning = ""
    if phase3_core_sim_matrix_summary:
        try:
            phase3_core_sim_matrix_evaluated_count = int(
                phase3_core_sim_matrix_summary.get("evaluated_manifest_count", 0)
            )
        except (TypeError, ValueError):
            phase3_core_sim_matrix_evaluated_count = 0
        if phase3_core_sim_matrix_evaluated_count > 0:
            try:
                min_ttc_same_lane_matrix = float(
                    phase3_core_sim_matrix_summary.get("min_ttc_same_lane_sec_min")
                )
            except (TypeError, ValueError):
                min_ttc_same_lane_matrix = None
            try:
                min_ttc_any_lane_matrix = float(
                    phase3_core_sim_matrix_summary.get("min_ttc_any_lane_sec_min")
                )
            except (TypeError, ValueError):
                min_ttc_any_lane_matrix = None
            lowest_ttc_same_lane_batch = (
                str(phase3_core_sim_matrix_summary.get("lowest_ttc_same_lane_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_ttc_same_lane_run = (
                str(phase3_core_sim_matrix_summary.get("lowest_ttc_same_lane_run_id", "")).strip()
                or "run_unknown"
            )
            lowest_ttc_any_lane_batch = (
                str(phase3_core_sim_matrix_summary.get("lowest_ttc_any_lane_batch_id", "")).strip()
                or "batch_unknown"
            )
            lowest_ttc_any_lane_run = (
                str(phase3_core_sim_matrix_summary.get("lowest_ttc_any_lane_run_id", "")).strip()
                or "run_unknown"
            )
            try:
                failed_case_count_total = max(
                    0,
                    int(phase3_core_sim_matrix_summary.get("failed_case_count_total", 0) or 0),
                )
            except (TypeError, ValueError):
                failed_case_count_total = 0
            try:
                collision_case_count_total = max(
                    0,
                    int(phase3_core_sim_matrix_summary.get("collision_case_count_total", 0) or 0),
                )
            except (TypeError, ValueError):
                collision_case_count_total = 0
            try:
                timeout_case_count_total = max(
                    0,
                    int(phase3_core_sim_matrix_summary.get("timeout_case_count_total", 0) or 0),
                )
            except (TypeError, ValueError):
                timeout_case_count_total = 0

            def _append_core_sim_matrix_min_ttc_warning(
                *,
                metric_label: str,
                value: float | None,
                warn_min: float,
                hold_min: float,
                batch_id: str,
                run_id: str,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if value is None or not math.isfinite(value):
                    return
                if hold_min > 0 and value <= hold_min:
                    phase3_core_sim_matrix_warning_messages.append(
                        f"{metric_label}={value:.3f} below hold_min={hold_min:.3f} "
                        f"(batch={batch_id},run={run_id})"
                    )
                    phase3_core_sim_matrix_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_min > 0 and value <= warn_min:
                    phase3_core_sim_matrix_warning_messages.append(
                        f"{metric_label}={value:.3f} below warn_min={warn_min:.3f} "
                        f"(batch={batch_id},run={run_id})"
                    )
                    phase3_core_sim_matrix_warning_reasons.append(reason_warn)

            def _append_core_sim_matrix_count_warning(
                *,
                metric_label: str,
                value: int,
                warn_max: int,
                hold_max: int,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    phase3_core_sim_matrix_warning_messages.append(
                        f"{metric_label}={value} exceeded hold_max={hold_max}"
                    )
                    phase3_core_sim_matrix_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase3_core_sim_matrix_warning_messages.append(
                        f"{metric_label}={value} exceeded warn_max={warn_max}"
                    )
                    phase3_core_sim_matrix_warning_reasons.append(reason_warn)

            _append_core_sim_matrix_min_ttc_warning(
                metric_label="phase3_core_sim_matrix_min_ttc_same_lane_sec",
                value=min_ttc_same_lane_matrix,
                warn_min=phase3_core_sim_matrix_min_ttc_same_lane_warn_min,
                hold_min=phase3_core_sim_matrix_min_ttc_same_lane_hold_min,
                batch_id=lowest_ttc_same_lane_batch,
                run_id=lowest_ttc_same_lane_run,
                reason_warn="phase3_core_sim_matrix_min_ttc_same_lane_below_warn_min",
                reason_hold="phase3_core_sim_matrix_min_ttc_same_lane_below_hold_min",
            )
            _append_core_sim_matrix_min_ttc_warning(
                metric_label="phase3_core_sim_matrix_min_ttc_any_lane_sec",
                value=min_ttc_any_lane_matrix,
                warn_min=phase3_core_sim_matrix_min_ttc_any_lane_warn_min,
                hold_min=phase3_core_sim_matrix_min_ttc_any_lane_hold_min,
                batch_id=lowest_ttc_any_lane_batch,
                run_id=lowest_ttc_any_lane_run,
                reason_warn="phase3_core_sim_matrix_min_ttc_any_lane_below_warn_min",
                reason_hold="phase3_core_sim_matrix_min_ttc_any_lane_below_hold_min",
            )
            _append_core_sim_matrix_count_warning(
                metric_label="phase3_core_sim_matrix_failed_case_count_total",
                value=failed_case_count_total,
                warn_max=phase3_core_sim_matrix_failed_cases_warn_max,
                hold_max=phase3_core_sim_matrix_failed_cases_hold_max,
                reason_warn="phase3_core_sim_matrix_failed_case_count_above_warn_max",
                reason_hold="phase3_core_sim_matrix_failed_case_count_above_hold_max",
            )
            _append_core_sim_matrix_count_warning(
                metric_label="phase3_core_sim_matrix_collision_case_count_total",
                value=collision_case_count_total,
                warn_max=phase3_core_sim_matrix_collision_cases_warn_max,
                hold_max=phase3_core_sim_matrix_collision_cases_hold_max,
                reason_warn="phase3_core_sim_matrix_collision_case_count_above_warn_max",
                reason_hold="phase3_core_sim_matrix_collision_case_count_above_hold_max",
            )
            _append_core_sim_matrix_count_warning(
                metric_label="phase3_core_sim_matrix_timeout_case_count_total",
                value=timeout_case_count_total,
                warn_max=phase3_core_sim_matrix_timeout_cases_warn_max,
                hold_max=phase3_core_sim_matrix_timeout_cases_hold_max,
                reason_warn="phase3_core_sim_matrix_timeout_case_count_above_warn_max",
                reason_hold="phase3_core_sim_matrix_timeout_case_count_above_hold_max",
            )

    if phase3_core_sim_matrix_warning_messages:
        phase3_core_sim_matrix_warning = "; ".join(phase3_core_sim_matrix_warning_messages)
        phase3_core_sim_matrix_warning_reasons = list(
            dict.fromkeys(phase3_core_sim_matrix_warning_reasons)
        )
        if status in {"PASS", "INFO"}:
            status = "WARN"

    if phase3_lane_risk_summary:
        try:
            phase3_lane_risk_eval_count = int(phase3_lane_risk_summary.get("evaluated_manifest_count", 0))
        except (TypeError, ValueError):
            phase3_lane_risk_eval_count = 0
        if phase3_lane_risk_eval_count > 0:
            try:
                min_ttc_same_lane = float(phase3_lane_risk_summary.get("min_ttc_same_lane_sec"))
            except (TypeError, ValueError):
                min_ttc_same_lane = math.inf
            try:
                min_ttc_adjacent_lane = float(phase3_lane_risk_summary.get("min_ttc_adjacent_lane_sec"))
            except (TypeError, ValueError):
                min_ttc_adjacent_lane = math.inf
            try:
                min_ttc_any_lane = float(phase3_lane_risk_summary.get("min_ttc_any_lane_sec"))
            except (TypeError, ValueError):
                min_ttc_any_lane = math.inf
            try:
                ttc_under_3s_same_lane_total = int(phase3_lane_risk_summary.get("ttc_under_3s_same_lane_total", 0))
            except (TypeError, ValueError):
                ttc_under_3s_same_lane_total = 0
            try:
                ttc_under_3s_adjacent_lane_total = int(
                    phase3_lane_risk_summary.get("ttc_under_3s_adjacent_lane_total", 0)
                )
            except (TypeError, ValueError):
                ttc_under_3s_adjacent_lane_total = 0
            ttc_under_3s_same_lane_total = max(0, ttc_under_3s_same_lane_total)
            ttc_under_3s_adjacent_lane_total = max(0, ttc_under_3s_adjacent_lane_total)
            ttc_under_3s_any_lane_total = ttc_under_3s_same_lane_total + ttc_under_3s_adjacent_lane_total
            try:
                same_lane_rows_total = int(phase3_lane_risk_summary.get("same_lane_rows_total", 0))
            except (TypeError, ValueError):
                same_lane_rows_total = 0
            try:
                adjacent_lane_rows_total = int(phase3_lane_risk_summary.get("adjacent_lane_rows_total", 0))
            except (TypeError, ValueError):
                adjacent_lane_rows_total = 0
            same_lane_rows_total = max(0, same_lane_rows_total)
            adjacent_lane_rows_total = max(0, adjacent_lane_rows_total)
            any_lane_rows_total = same_lane_rows_total + adjacent_lane_rows_total
            phase3_lane_risk_ttc_under_3s_same_lane_ratio = (
                (ttc_under_3s_same_lane_total / same_lane_rows_total) if same_lane_rows_total > 0 else None
            )
            phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio = (
                (ttc_under_3s_adjacent_lane_total / adjacent_lane_rows_total)
                if adjacent_lane_rows_total > 0
                else None
            )
            phase3_lane_risk_ttc_under_3s_any_lane_ratio = (
                (ttc_under_3s_any_lane_total / any_lane_rows_total) if any_lane_rows_total > 0 else None
            )
            lowest_same_lane_batch = (
                str(phase3_lane_risk_summary.get("lowest_same_lane_batch_id", "")).strip() or "batch_unknown"
            )
            lowest_adjacent_lane_batch = (
                str(phase3_lane_risk_summary.get("lowest_adjacent_lane_batch_id", "")).strip() or "batch_unknown"
            )
            lowest_any_lane_batch = (
                str(phase3_lane_risk_summary.get("lowest_any_lane_batch_id", "")).strip() or "batch_unknown"
            )

            def _append_min_ttc_warning(
                *,
                metric_label: str,
                value: float,
                warn_min: float,
                hold_min: float,
                batch_id: str,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if not math.isfinite(value):
                    return
                if hold_min > 0 and value <= hold_min:
                    phase3_lane_risk_warning_messages.append(
                        f"{metric_label}={value:.3f} below hold_min={hold_min:.3f} (batch={batch_id})"
                    )
                    phase3_lane_risk_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_min > 0 and value <= warn_min:
                    phase3_lane_risk_warning_messages.append(
                        f"{metric_label}={value:.3f} below warn_min={warn_min:.3f} (batch={batch_id})"
                    )
                    phase3_lane_risk_warning_reasons.append(reason_warn)

            def _append_count_warning(
                *,
                metric_label: str,
                value: int,
                warn_max: int,
                hold_max: int,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    phase3_lane_risk_warning_messages.append(
                        f"{metric_label}={value} exceeded hold_max={hold_max}"
                    )
                    phase3_lane_risk_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase3_lane_risk_warning_messages.append(
                        f"{metric_label}={value} exceeded warn_max={warn_max}"
                    )
                    phase3_lane_risk_warning_reasons.append(reason_warn)

            def _append_ratio_warning(
                *,
                metric_label: str,
                value: float | None,
                warn_max: float,
                hold_max: float,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if value is None:
                    return
                if hold_max > 0 and value > hold_max:
                    phase3_lane_risk_warning_messages.append(
                        f"{metric_label}={value:.3f} exceeded hold_max={hold_max:.3f}"
                    )
                    phase3_lane_risk_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase3_lane_risk_warning_messages.append(
                        f"{metric_label}={value:.3f} exceeded warn_max={warn_max:.3f}"
                    )
                    phase3_lane_risk_warning_reasons.append(reason_warn)

            _append_min_ttc_warning(
                metric_label="phase3_lane_risk_min_ttc_same_lane_sec",
                value=min_ttc_same_lane,
                warn_min=phase3_lane_risk_min_ttc_same_lane_warn_min,
                hold_min=phase3_lane_risk_min_ttc_same_lane_hold_min,
                batch_id=lowest_same_lane_batch,
                reason_warn="phase3_lane_risk_min_ttc_same_lane_below_warn_min",
                reason_hold="phase3_lane_risk_min_ttc_same_lane_below_hold_min",
            )
            _append_min_ttc_warning(
                metric_label="phase3_lane_risk_min_ttc_adjacent_lane_sec",
                value=min_ttc_adjacent_lane,
                warn_min=phase3_lane_risk_min_ttc_adjacent_lane_warn_min,
                hold_min=phase3_lane_risk_min_ttc_adjacent_lane_hold_min,
                batch_id=lowest_adjacent_lane_batch,
                reason_warn="phase3_lane_risk_min_ttc_adjacent_lane_below_warn_min",
                reason_hold="phase3_lane_risk_min_ttc_adjacent_lane_below_hold_min",
            )
            _append_min_ttc_warning(
                metric_label="phase3_lane_risk_min_ttc_any_lane_sec",
                value=min_ttc_any_lane,
                warn_min=phase3_lane_risk_min_ttc_any_lane_warn_min,
                hold_min=phase3_lane_risk_min_ttc_any_lane_hold_min,
                batch_id=lowest_any_lane_batch,
                reason_warn="phase3_lane_risk_min_ttc_any_lane_below_warn_min",
                reason_hold="phase3_lane_risk_min_ttc_any_lane_below_hold_min",
            )
            _append_count_warning(
                metric_label="phase3_lane_risk_ttc_under_3s_same_lane_total",
                value=ttc_under_3s_same_lane_total,
                warn_max=phase3_lane_risk_ttc_under_3s_same_lane_warn_max,
                hold_max=phase3_lane_risk_ttc_under_3s_same_lane_hold_max,
                reason_warn="phase3_lane_risk_ttc_under_3s_same_lane_total_above_warn_max",
                reason_hold="phase3_lane_risk_ttc_under_3s_same_lane_total_above_hold_max",
            )
            _append_count_warning(
                metric_label="phase3_lane_risk_ttc_under_3s_adjacent_lane_total",
                value=ttc_under_3s_adjacent_lane_total,
                warn_max=phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max,
                hold_max=phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max,
                reason_warn="phase3_lane_risk_ttc_under_3s_adjacent_lane_total_above_warn_max",
                reason_hold="phase3_lane_risk_ttc_under_3s_adjacent_lane_total_above_hold_max",
            )
            _append_count_warning(
                metric_label="phase3_lane_risk_ttc_under_3s_any_lane_total",
                value=ttc_under_3s_any_lane_total,
                warn_max=phase3_lane_risk_ttc_under_3s_any_lane_warn_max,
                hold_max=phase3_lane_risk_ttc_under_3s_any_lane_hold_max,
                reason_warn="phase3_lane_risk_ttc_under_3s_any_lane_total_above_warn_max",
                reason_hold="phase3_lane_risk_ttc_under_3s_any_lane_total_above_hold_max",
            )
            _append_ratio_warning(
                metric_label="phase3_lane_risk_ttc_under_3s_same_lane_ratio",
                value=phase3_lane_risk_ttc_under_3s_same_lane_ratio,
                warn_max=phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max,
                hold_max=phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max,
                reason_warn="phase3_lane_risk_ttc_under_3s_same_lane_ratio_above_warn_max",
                reason_hold="phase3_lane_risk_ttc_under_3s_same_lane_ratio_above_hold_max",
            )
            _append_ratio_warning(
                metric_label="phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio",
                value=phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio,
                warn_max=phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max,
                hold_max=phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max,
                reason_warn="phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_above_warn_max",
                reason_hold="phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_above_hold_max",
            )
            _append_ratio_warning(
                metric_label="phase3_lane_risk_ttc_under_3s_any_lane_ratio",
                value=phase3_lane_risk_ttc_under_3s_any_lane_ratio,
                warn_max=phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max,
                hold_max=phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max,
                reason_warn="phase3_lane_risk_ttc_under_3s_any_lane_ratio_above_warn_max",
                reason_hold="phase3_lane_risk_ttc_under_3s_any_lane_ratio_above_hold_max",
            )
            phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch = _threshold_float_count_mismatch(
                phase3_lane_risk_gate_min_ttc_same_lane_sec_counts,
                phase3_lane_risk_min_ttc_same_lane_warn_min,
            )
            if phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch="
                    f"expected:{phase3_lane_risk_min_ttc_same_lane_warn_min:.3f},"
                    f"observed:{phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch = _threshold_float_count_mismatch(
                phase3_lane_risk_gate_min_ttc_same_lane_sec_counts,
                phase3_lane_risk_min_ttc_same_lane_hold_min,
            )
            if phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch="
                    f"expected:{phase3_lane_risk_min_ttc_same_lane_hold_min:.3f},"
                    f"observed:{phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch = _threshold_float_count_mismatch(
                phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts,
                phase3_lane_risk_min_ttc_adjacent_lane_warn_min,
            )
            if phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch="
                    f"expected:{phase3_lane_risk_min_ttc_adjacent_lane_warn_min:.3f},"
                    f"observed:{phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch = _threshold_float_count_mismatch(
                phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts,
                phase3_lane_risk_min_ttc_adjacent_lane_hold_min,
            )
            if phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch="
                    f"expected:{phase3_lane_risk_min_ttc_adjacent_lane_hold_min:.3f},"
                    f"observed:{phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch = _threshold_float_count_mismatch(
                phase3_lane_risk_gate_min_ttc_any_lane_sec_counts,
                phase3_lane_risk_min_ttc_any_lane_warn_min,
            )
            if phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch="
                    f"expected:{phase3_lane_risk_min_ttc_any_lane_warn_min:.3f},"
                    f"observed:{phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch = _threshold_float_count_mismatch(
                phase3_lane_risk_gate_min_ttc_any_lane_sec_counts,
                phase3_lane_risk_min_ttc_any_lane_hold_min,
            )
            if phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch="
                    f"expected:{phase3_lane_risk_min_ttc_any_lane_hold_min:.3f},"
                    f"observed:{phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch = _threshold_count_mismatch(
                phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts,
                phase3_lane_risk_ttc_under_3s_same_lane_warn_max,
            )
            if phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch="
                    f"expected:{phase3_lane_risk_ttc_under_3s_same_lane_warn_max},"
                    f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch = _threshold_count_mismatch(
                phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts,
                phase3_lane_risk_ttc_under_3s_same_lane_hold_max,
            )
            if phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch="
                    f"expected:{phase3_lane_risk_ttc_under_3s_same_lane_hold_max},"
                    f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch = _threshold_count_mismatch(
                phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts,
                phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max,
            )
            if phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch="
                    f"expected:{phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max},"
                    f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch = _threshold_count_mismatch(
                phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts,
                phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max,
            )
            if phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch="
                    f"expected:{phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max},"
                    f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch = _threshold_count_mismatch(
                phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts,
                phase3_lane_risk_ttc_under_3s_any_lane_warn_max,
            )
            if phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch="
                    f"expected:{phase3_lane_risk_ttc_under_3s_any_lane_warn_max},"
                    f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch = _threshold_count_mismatch(
                phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts,
                phase3_lane_risk_ttc_under_3s_any_lane_hold_max,
            )
            if phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch:
                phase3_lane_risk_warning_messages.append(
                    "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch="
                    f"expected:{phase3_lane_risk_ttc_under_3s_any_lane_hold_max},"
                    f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text}"
                )
                phase3_lane_risk_warning_reasons.append(
                    "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"

    phase3_lane_risk_warning = ""
    if phase3_lane_risk_warning_messages:
        phase3_lane_risk_warning = "; ".join(phase3_lane_risk_warning_messages)
        phase3_lane_risk_warning_reasons = list(dict.fromkeys(phase3_lane_risk_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"
    phase3_lane_risk_threshold_drift_reasons: list[str] = []
    phase3_lane_risk_threshold_drift_parts: list[str] = []
    if phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "min_ttc_same_lane_warn_min="
            f"expected:{phase3_lane_risk_min_ttc_same_lane_warn_min:.3f},"
            f"observed:{phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text}"
        )
    if phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "min_ttc_same_lane_hold_min="
            f"expected:{phase3_lane_risk_min_ttc_same_lane_hold_min:.3f},"
            f"observed:{phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text}"
        )
    if phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "min_ttc_adjacent_lane_warn_min="
            f"expected:{phase3_lane_risk_min_ttc_adjacent_lane_warn_min:.3f},"
            f"observed:{phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text}"
        )
    if phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "min_ttc_adjacent_lane_hold_min="
            f"expected:{phase3_lane_risk_min_ttc_adjacent_lane_hold_min:.3f},"
            f"observed:{phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text}"
        )
    if phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "min_ttc_any_lane_warn_min="
            f"expected:{phase3_lane_risk_min_ttc_any_lane_warn_min:.3f},"
            f"observed:{phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text}"
        )
    if phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "min_ttc_any_lane_hold_min="
            f"expected:{phase3_lane_risk_min_ttc_any_lane_hold_min:.3f},"
            f"observed:{phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text}"
        )
    if phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "ttc_under_3s_same_lane_warn_max="
            f"expected:{phase3_lane_risk_ttc_under_3s_same_lane_warn_max},"
            f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text}"
        )
    if phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "ttc_under_3s_same_lane_hold_max="
            f"expected:{phase3_lane_risk_ttc_under_3s_same_lane_hold_max},"
            f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text}"
        )
    if phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "ttc_under_3s_adjacent_lane_warn_max="
            f"expected:{phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max},"
            f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text}"
        )
    if phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "ttc_under_3s_adjacent_lane_hold_max="
            f"expected:{phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max},"
            f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text}"
        )
    if phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "ttc_under_3s_any_lane_warn_max="
            f"expected:{phase3_lane_risk_ttc_under_3s_any_lane_warn_max},"
            f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text}"
        )
    if phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch:
        phase3_lane_risk_threshold_drift_reasons.append(
            "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch"
        )
        phase3_lane_risk_threshold_drift_parts.append(
            "ttc_under_3s_any_lane_hold_max="
            f"expected:{phase3_lane_risk_ttc_under_3s_any_lane_hold_max},"
            f"observed:{phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text}"
        )
    phase3_lane_risk_threshold_drift_detected = bool(phase3_lane_risk_threshold_drift_parts)
    phase3_lane_risk_threshold_drift_reasons = list(dict.fromkeys(phase3_lane_risk_threshold_drift_reasons))
    phase3_lane_risk_threshold_drift_summary_text = (
        "; ".join(phase3_lane_risk_threshold_drift_parts)
        if phase3_lane_risk_threshold_drift_detected
        else "n/a"
    )
    if phase3_lane_risk_threshold_drift_detected:
        if (
            phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch
            or phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch
            or phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch
            or phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch
            or phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch
            or phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch
        ):
            phase3_lane_risk_threshold_drift_severity = "HOLD"
        else:
            phase3_lane_risk_threshold_drift_severity = "WARN"
    else:
        phase3_lane_risk_threshold_drift_severity = "NONE"

    phase3_dataset_traffic_warning = ""
    if phase3_dataset_traffic_summary:
        try:
            phase3_dataset_traffic_evaluated_count = int(
                phase3_dataset_traffic_summary.get("evaluated_manifest_count", 0) or 0
            )
        except (TypeError, ValueError):
            phase3_dataset_traffic_evaluated_count = 0
        if phase3_dataset_traffic_evaluated_count > 0:
            try:
                phase3_dataset_traffic_run_summary_count_total = int(
                    phase3_dataset_traffic_summary.get("run_summary_count_total", 0) or 0
                )
            except (TypeError, ValueError):
                phase3_dataset_traffic_run_summary_count_total = 0
            try:
                phase3_dataset_traffic_profile_unique_count = int(
                    phase3_dataset_traffic_summary.get("traffic_profile_unique_count", 0) or 0
                )
            except (TypeError, ValueError):
                phase3_dataset_traffic_profile_unique_count = 0
            try:
                phase3_dataset_traffic_actor_pattern_unique_count = int(
                    phase3_dataset_traffic_summary.get("traffic_actor_pattern_unique_count", 0) or 0
                )
            except (TypeError, ValueError):
                phase3_dataset_traffic_actor_pattern_unique_count = 0
            try:
                phase3_dataset_traffic_npc_count_avg_avg = float(
                    phase3_dataset_traffic_summary.get("traffic_npc_count_avg_avg", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                phase3_dataset_traffic_npc_count_avg_avg = 0.0

            def _append_phase3_dataset_traffic_min_warning(
                *,
                metric_label: str,
                value: int,
                warn_min: int,
                hold_min: int,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_min > 0 and value < hold_min:
                    phase3_dataset_traffic_warning_messages.append(
                        f"{metric_label}={value} below hold_min={hold_min}"
                    )
                    phase3_dataset_traffic_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_min > 0 and value < warn_min:
                    phase3_dataset_traffic_warning_messages.append(
                        f"{metric_label}={value} below warn_min={warn_min}"
                    )
                    phase3_dataset_traffic_warning_reasons.append(reason_warn)

            def _append_phase3_dataset_traffic_float_min_warning(
                *,
                metric_label: str,
                value: float,
                warn_min: float,
                hold_min: float,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_min > 0 and value < hold_min:
                    phase3_dataset_traffic_warning_messages.append(
                        f"{metric_label}={value:.3f} below hold_min={hold_min:.3f}"
                    )
                    phase3_dataset_traffic_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_min > 0 and value < warn_min:
                    phase3_dataset_traffic_warning_messages.append(
                        f"{metric_label}={value:.3f} below warn_min={warn_min:.3f}"
                    )
                    phase3_dataset_traffic_warning_reasons.append(reason_warn)

            _append_phase3_dataset_traffic_min_warning(
                metric_label="phase3_dataset_traffic_run_summary_count_total",
                value=phase3_dataset_traffic_run_summary_count_total,
                warn_min=phase3_dataset_traffic_run_summary_warn_min,
                hold_min=phase3_dataset_traffic_run_summary_hold_min,
                reason_warn="phase3_dataset_traffic_run_summary_count_below_warn_min",
                reason_hold="phase3_dataset_traffic_run_summary_count_below_hold_min",
            )
            _append_phase3_dataset_traffic_min_warning(
                metric_label="phase3_dataset_traffic_profile_unique_count",
                value=phase3_dataset_traffic_profile_unique_count,
                warn_min=phase3_dataset_traffic_profile_count_warn_min,
                hold_min=phase3_dataset_traffic_profile_count_hold_min,
                reason_warn="phase3_dataset_traffic_profile_unique_count_below_warn_min",
                reason_hold="phase3_dataset_traffic_profile_unique_count_below_hold_min",
            )
            _append_phase3_dataset_traffic_min_warning(
                metric_label="phase3_dataset_traffic_actor_pattern_unique_count",
                value=phase3_dataset_traffic_actor_pattern_unique_count,
                warn_min=phase3_dataset_traffic_actor_pattern_count_warn_min,
                hold_min=phase3_dataset_traffic_actor_pattern_count_hold_min,
                reason_warn="phase3_dataset_traffic_actor_pattern_unique_count_below_warn_min",
                reason_hold="phase3_dataset_traffic_actor_pattern_unique_count_below_hold_min",
            )
            _append_phase3_dataset_traffic_float_min_warning(
                metric_label="phase3_dataset_traffic_npc_count_avg_avg",
                value=phase3_dataset_traffic_npc_count_avg_avg,
                warn_min=phase3_dataset_traffic_avg_npc_count_warn_min,
                hold_min=phase3_dataset_traffic_avg_npc_count_hold_min,
                reason_warn="phase3_dataset_traffic_npc_count_avg_avg_below_warn_min",
                reason_hold="phase3_dataset_traffic_npc_count_avg_avg_below_hold_min",
            )
            phase3_dataset_traffic_run_summary_warn_min_mismatch = _threshold_count_mismatch(
                phase3_dataset_traffic_gate_min_run_summary_count_counts,
                phase3_dataset_traffic_run_summary_warn_min,
            )
            if phase3_dataset_traffic_run_summary_warn_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_run_summary_warn_min_mismatch="
                    f"expected:{phase3_dataset_traffic_run_summary_warn_min},"
                    f"observed:{phase3_dataset_traffic_gate_min_run_summary_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_run_summary_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_dataset_traffic_run_summary_hold_min_mismatch = _threshold_count_mismatch(
                phase3_dataset_traffic_gate_min_run_summary_count_counts,
                phase3_dataset_traffic_run_summary_hold_min,
            )
            if phase3_dataset_traffic_run_summary_hold_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_run_summary_hold_min_mismatch="
                    f"expected:{phase3_dataset_traffic_run_summary_hold_min},"
                    f"observed:{phase3_dataset_traffic_gate_min_run_summary_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_run_summary_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_dataset_traffic_profile_count_warn_min_mismatch = _threshold_count_mismatch(
                phase3_dataset_traffic_gate_min_traffic_profile_count_counts,
                phase3_dataset_traffic_profile_count_warn_min,
            )
            if phase3_dataset_traffic_profile_count_warn_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_profile_count_warn_min_mismatch="
                    f"expected:{phase3_dataset_traffic_profile_count_warn_min},"
                    f"observed:{phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_profile_count_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_dataset_traffic_profile_count_hold_min_mismatch = _threshold_count_mismatch(
                phase3_dataset_traffic_gate_min_traffic_profile_count_counts,
                phase3_dataset_traffic_profile_count_hold_min,
            )
            if phase3_dataset_traffic_profile_count_hold_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_profile_count_hold_min_mismatch="
                    f"expected:{phase3_dataset_traffic_profile_count_hold_min},"
                    f"observed:{phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_profile_count_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch = _threshold_count_mismatch(
                phase3_dataset_traffic_gate_min_actor_pattern_count_counts,
                phase3_dataset_traffic_actor_pattern_count_warn_min,
            )
            if phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch="
                    f"expected:{phase3_dataset_traffic_actor_pattern_count_warn_min},"
                    f"observed:{phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch = _threshold_count_mismatch(
                phase3_dataset_traffic_gate_min_actor_pattern_count_counts,
                phase3_dataset_traffic_actor_pattern_count_hold_min,
            )
            if phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch="
                    f"expected:{phase3_dataset_traffic_actor_pattern_count_hold_min},"
                    f"observed:{phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
            phase3_dataset_traffic_avg_npc_count_warn_min_mismatch = _threshold_float_count_mismatch(
                phase3_dataset_traffic_gate_min_avg_npc_count_counts,
                phase3_dataset_traffic_avg_npc_count_warn_min,
            )
            if phase3_dataset_traffic_avg_npc_count_warn_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_avg_npc_count_warn_min_mismatch="
                    f"expected:{phase3_dataset_traffic_avg_npc_count_warn_min:.3f},"
                    f"observed:{phase3_dataset_traffic_gate_min_avg_npc_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_avg_npc_count_warn_min_mismatch"
                )
                if status in {"PASS", "INFO"}:
                    status = "WARN"
            phase3_dataset_traffic_avg_npc_count_hold_min_mismatch = _threshold_float_count_mismatch(
                phase3_dataset_traffic_gate_min_avg_npc_count_counts,
                phase3_dataset_traffic_avg_npc_count_hold_min,
            )
            if phase3_dataset_traffic_avg_npc_count_hold_min_mismatch:
                phase3_dataset_traffic_warning_messages.append(
                    "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch="
                    f"expected:{phase3_dataset_traffic_avg_npc_count_hold_min:.3f},"
                    f"observed:{phase3_dataset_traffic_gate_min_avg_npc_count_counts_text}"
                )
                phase3_dataset_traffic_warning_reasons.append(
                    "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch"
                )
                if status != "HOLD":
                    status = "HOLD"
    if phase3_dataset_traffic_warning_messages:
        phase3_dataset_traffic_warning = "; ".join(phase3_dataset_traffic_warning_messages)
        phase3_dataset_traffic_warning_reasons = list(dict.fromkeys(phase3_dataset_traffic_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"
    phase3_dataset_traffic_threshold_drift_reasons: list[str] = []
    phase3_dataset_traffic_threshold_drift_parts: list[str] = []
    if phase3_dataset_traffic_run_summary_warn_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_run_summary_warn_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "run_summary_warn_min="
            f"expected:{phase3_dataset_traffic_run_summary_warn_min},"
            f"observed:{phase3_dataset_traffic_gate_min_run_summary_count_counts_text}"
        )
    if phase3_dataset_traffic_run_summary_hold_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_run_summary_hold_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "run_summary_hold_min="
            f"expected:{phase3_dataset_traffic_run_summary_hold_min},"
            f"observed:{phase3_dataset_traffic_gate_min_run_summary_count_counts_text}"
        )
    if phase3_dataset_traffic_profile_count_warn_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_profile_count_warn_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "profile_count_warn_min="
            f"expected:{phase3_dataset_traffic_profile_count_warn_min},"
            f"observed:{phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text}"
        )
    if phase3_dataset_traffic_profile_count_hold_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_profile_count_hold_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "profile_count_hold_min="
            f"expected:{phase3_dataset_traffic_profile_count_hold_min},"
            f"observed:{phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text}"
        )
    if phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "actor_pattern_count_warn_min="
            f"expected:{phase3_dataset_traffic_actor_pattern_count_warn_min},"
            f"observed:{phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text}"
        )
    if phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "actor_pattern_count_hold_min="
            f"expected:{phase3_dataset_traffic_actor_pattern_count_hold_min},"
            f"observed:{phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text}"
        )
    if phase3_dataset_traffic_avg_npc_count_warn_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_avg_npc_count_warn_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "avg_npc_count_warn_min="
            f"expected:{phase3_dataset_traffic_avg_npc_count_warn_min:.3f},"
            f"observed:{phase3_dataset_traffic_gate_min_avg_npc_count_counts_text}"
        )
    if phase3_dataset_traffic_avg_npc_count_hold_min_mismatch:
        phase3_dataset_traffic_threshold_drift_reasons.append(
            "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch"
        )
        phase3_dataset_traffic_threshold_drift_parts.append(
            "avg_npc_count_hold_min="
            f"expected:{phase3_dataset_traffic_avg_npc_count_hold_min:.3f},"
            f"observed:{phase3_dataset_traffic_gate_min_avg_npc_count_counts_text}"
        )
    phase3_dataset_traffic_threshold_drift_detected = bool(phase3_dataset_traffic_threshold_drift_parts)
    phase3_dataset_traffic_threshold_drift_reasons = list(
        dict.fromkeys(phase3_dataset_traffic_threshold_drift_reasons)
    )
    phase3_dataset_traffic_threshold_drift_summary_text = (
        "; ".join(phase3_dataset_traffic_threshold_drift_parts)
        if phase3_dataset_traffic_threshold_drift_detected
        else "n/a"
    )
    if phase3_dataset_traffic_threshold_drift_detected:
        if (
            phase3_dataset_traffic_run_summary_hold_min_mismatch
            or phase3_dataset_traffic_profile_count_hold_min_mismatch
            or phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch
            or phase3_dataset_traffic_avg_npc_count_hold_min_mismatch
        ):
            phase3_dataset_traffic_threshold_drift_severity = "HOLD"
        else:
            phase3_dataset_traffic_threshold_drift_severity = "WARN"
    else:
        phase3_dataset_traffic_threshold_drift_severity = "NONE"

    phase2_log_replay_warning = ""
    phase2_log_replay_warning_messages: list[str] = []
    phase2_log_replay_warning_reasons: list[str] = []
    if phase2_log_replay_summary:
        try:
            phase2_log_replay_evaluated_count = int(
                phase2_log_replay_summary.get("evaluated_manifest_count", 0)
            )
        except (TypeError, ValueError):
            phase2_log_replay_evaluated_count = 0
        if phase2_log_replay_evaluated_count > 0:
            phase2_log_replay_status_counts = _as_non_negative_int_map(
                phase2_log_replay_summary.get("status_counts", {})
            )
            phase2_log_replay_fail_count = int(phase2_log_replay_status_counts.get("fail", 0) or 0)
            try:
                phase2_log_replay_missing_summary_count = int(
                    phase2_log_replay_summary.get("missing_summary_count", 0)
                )
            except (TypeError, ValueError):
                phase2_log_replay_missing_summary_count = 0

            def _append_phase2_log_replay_count_warning(
                *,
                metric_label: str,
                value: int,
                warn_max: int,
                hold_max: int,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    phase2_log_replay_warning_messages.append(
                        f"{metric_label}={value} exceeded hold_max={hold_max}"
                    )
                    phase2_log_replay_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase2_log_replay_warning_messages.append(
                        f"{metric_label}={value} exceeded warn_max={warn_max}"
                    )
                    phase2_log_replay_warning_reasons.append(reason_warn)

            _append_phase2_log_replay_count_warning(
                metric_label="phase2_log_replay_fail_count",
                value=phase2_log_replay_fail_count,
                warn_max=phase2_log_replay_fail_warn_max,
                hold_max=phase2_log_replay_fail_hold_max,
                reason_warn="phase2_log_replay_fail_count_above_warn_max",
                reason_hold="phase2_log_replay_fail_count_above_hold_max",
            )
            _append_phase2_log_replay_count_warning(
                metric_label="phase2_log_replay_missing_summary_count",
                value=phase2_log_replay_missing_summary_count,
                warn_max=phase2_log_replay_missing_summary_warn_max,
                hold_max=phase2_log_replay_missing_summary_hold_max,
                reason_warn="phase2_log_replay_missing_summary_count_above_warn_max",
                reason_hold="phase2_log_replay_missing_summary_count_above_hold_max",
            )

    if phase2_log_replay_warning_messages:
        phase2_log_replay_warning = "; ".join(phase2_log_replay_warning_messages)
        phase2_log_replay_warning_reasons = list(dict.fromkeys(phase2_log_replay_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"

    runtime_native_smoke_warning = ""
    runtime_native_smoke_warning_messages: list[str] = []
    runtime_native_smoke_warning_reasons: list[str] = []
    if runtime_native_smoke_summary:
        try:
            runtime_native_smoke_evaluated_count = int(
                runtime_native_smoke_summary.get("evaluated_manifest_count", 0)
            )
        except (TypeError, ValueError):
            runtime_native_smoke_evaluated_count = 0
        if runtime_native_smoke_evaluated_count > 0:
            runtime_native_smoke_all_status_counts = _as_non_negative_int_map(
                runtime_native_smoke_summary.get("all_modules_status_counts", {})
            )
            runtime_native_smoke_fail_count = int(runtime_native_smoke_all_status_counts.get("fail", 0) or 0)
            runtime_native_smoke_partial_count = int(
                runtime_native_smoke_all_status_counts.get("partial", 0) or 0
            )

            def _append_runtime_native_smoke_count_warning(
                *,
                metric_label: str,
                value: int,
                warn_max: int,
                hold_max: int,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    runtime_native_smoke_warning_messages.append(
                        f"{metric_label}={value} exceeded hold_max={hold_max}"
                    )
                    runtime_native_smoke_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    runtime_native_smoke_warning_messages.append(
                        f"{metric_label}={value} exceeded warn_max={warn_max}"
                    )
                    runtime_native_smoke_warning_reasons.append(reason_warn)

            _append_runtime_native_smoke_count_warning(
                metric_label="runtime_native_smoke_fail_count",
                value=runtime_native_smoke_fail_count,
                warn_max=runtime_native_smoke_fail_warn_max,
                hold_max=runtime_native_smoke_fail_hold_max,
                reason_warn="runtime_native_smoke_fail_count_above_warn_max",
                reason_hold="runtime_native_smoke_fail_count_above_hold_max",
            )
            _append_runtime_native_smoke_count_warning(
                metric_label="runtime_native_smoke_partial_count",
                value=runtime_native_smoke_partial_count,
                warn_max=runtime_native_smoke_partial_warn_max,
                hold_max=runtime_native_smoke_partial_hold_max,
                reason_warn="runtime_native_smoke_partial_count_above_warn_max",
                reason_hold="runtime_native_smoke_partial_count_above_hold_max",
            )

    if runtime_native_smoke_warning_messages:
        runtime_native_smoke_warning = "; ".join(runtime_native_smoke_warning_messages)
        runtime_native_smoke_warning_reasons = list(dict.fromkeys(runtime_native_smoke_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"

    hold_policy_reason_aggregation_enabled = (
        threshold_drift_hold_policy_failure_detected
        or threshold_drift_hold_policy_failure_count > 0
        or bool(threshold_drift_hold_policy_failures)
    )
    if hold_policy_reason_aggregation_enabled:

        def _merge_threshold_drift_hold_policy_scope_reasons(
            *,
            scope_key: str,
            reason_keys: list[str],
        ) -> None:
            nonlocal threshold_drift_hold_policy_failure_reason_keys
            deduped_reason_keys = _dedupe_preserve_order(
                [str(item).strip() for item in reason_keys if str(item).strip()]
            )
            if not deduped_reason_keys:
                return
            scope_reason_key_counts = threshold_drift_hold_policy_failure_scope_reason_key_counts.setdefault(
                scope_key,
                {},
            )
            for reason_key in deduped_reason_keys:
                if reason_key in threshold_drift_hold_policy_failure_reason_key_counts:
                    continue
                threshold_drift_hold_policy_failure_reason_key_counts[reason_key] = 1
                scope_reason_key_counts[reason_key] = 1
                if reason_key not in threshold_drift_hold_policy_failure_reason_keys:
                    threshold_drift_hold_policy_failure_reason_keys.append(reason_key)

        _merge_threshold_drift_hold_policy_scope_reasons(
            scope_key="phase2_log_replay",
            reason_keys=phase2_log_replay_warning_reasons,
        )
        _merge_threshold_drift_hold_policy_scope_reasons(
            scope_key="runtime_native_smoke",
            reason_keys=runtime_native_smoke_warning_reasons,
        )

        if (
            not threshold_drift_hold_policy_failure_reason_keys
            and threshold_drift_hold_policy_failure_reason_key_counts
        ):
            threshold_drift_hold_policy_failure_reason_keys = list(
                threshold_drift_hold_policy_failure_reason_key_counts.keys()
            )

        threshold_drift_hold_policy_failure_reason_keys_text = (
            ",".join(_truncate_list(threshold_drift_hold_policy_failure_reason_keys, max_codes))
            if threshold_drift_hold_policy_failure_reason_keys
            else "n/a"
        )
        threshold_drift_hold_policy_failure_reason_key_counts_text = _format_non_negative_int_counts(
            threshold_drift_hold_policy_failure_reason_key_counts
        )
        threshold_drift_hold_policy_failure_scope_reason_key_counts_text = _format_non_negative_int_nested_counts(
            threshold_drift_hold_policy_failure_scope_reason_key_counts
        )

    phase2_map_routing_warning = ""
    phase2_map_routing_warning_messages: list[str] = []
    phase2_map_routing_warning_reasons: list[str] = []
    if phase2_map_routing_summary:
        try:
            phase2_map_routing_evaluated_count = int(phase2_map_routing_summary.get("evaluated_manifest_count", 0))
        except (TypeError, ValueError):
            phase2_map_routing_evaluated_count = 0
        if phase2_map_routing_evaluated_count > 0:
            try:
                phase2_map_routing_max_unreachable = int(phase2_map_routing_summary.get("max_unreachable_lane_count", 0))
            except (TypeError, ValueError):
                phase2_map_routing_max_unreachable = 0
            phase2_map_routing_highest_unreachable_batch = (
                str(phase2_map_routing_summary.get("highest_unreachable_batch_id", "")).strip() or "batch_unknown"
            )
            try:
                phase2_map_routing_max_non_reciprocal = int(
                    phase2_map_routing_summary.get("max_non_reciprocal_link_count", 0)
                )
            except (TypeError, ValueError):
                phase2_map_routing_max_non_reciprocal = 0
            phase2_map_routing_highest_non_reciprocal_batch = (
                str(phase2_map_routing_summary.get("highest_non_reciprocal_batch_id", "")).strip() or "batch_unknown"
            )
            try:
                phase2_map_routing_max_continuity_gap = int(
                    phase2_map_routing_summary.get("max_continuity_gap_warning_count", 0)
                )
            except (TypeError, ValueError):
                phase2_map_routing_max_continuity_gap = 0
            phase2_map_routing_highest_continuity_gap_batch = (
                str(phase2_map_routing_summary.get("highest_continuity_gap_batch_id", "")).strip() or "batch_unknown"
            )

            def _append_phase2_map_routing_count_warning(
                *,
                metric_label: str,
                value: int,
                warn_max: int,
                hold_max: int,
                batch_id: str,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    phase2_map_routing_warning_messages.append(
                        f"{metric_label}={value} exceeded hold_max={hold_max} (batch={batch_id})"
                    )
                    phase2_map_routing_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase2_map_routing_warning_messages.append(
                        f"{metric_label}={value} exceeded warn_max={warn_max} (batch={batch_id})"
                    )
                    phase2_map_routing_warning_reasons.append(reason_warn)

            _append_phase2_map_routing_count_warning(
                metric_label="phase2_map_routing_unreachable_lane_count",
                value=phase2_map_routing_max_unreachable,
                warn_max=phase2_map_routing_unreachable_lanes_warn_max,
                hold_max=phase2_map_routing_unreachable_lanes_hold_max,
                batch_id=phase2_map_routing_highest_unreachable_batch,
                reason_warn="phase2_map_routing_unreachable_lanes_above_warn_max",
                reason_hold="phase2_map_routing_unreachable_lanes_above_hold_max",
            )
            _append_phase2_map_routing_count_warning(
                metric_label="phase2_map_routing_non_reciprocal_link_count",
                value=phase2_map_routing_max_non_reciprocal,
                warn_max=phase2_map_routing_non_reciprocal_links_warn_max,
                hold_max=phase2_map_routing_non_reciprocal_links_hold_max,
                batch_id=phase2_map_routing_highest_non_reciprocal_batch,
                reason_warn="phase2_map_routing_non_reciprocal_links_above_warn_max",
                reason_hold="phase2_map_routing_non_reciprocal_links_above_hold_max",
            )
            _append_phase2_map_routing_count_warning(
                metric_label="phase2_map_routing_continuity_gap_warning_count",
                value=phase2_map_routing_max_continuity_gap,
                warn_max=phase2_map_routing_continuity_gap_warn_max,
                hold_max=phase2_map_routing_continuity_gap_hold_max,
                batch_id=phase2_map_routing_highest_continuity_gap_batch,
                reason_warn="phase2_map_routing_continuity_gap_above_warn_max",
                reason_hold="phase2_map_routing_continuity_gap_above_hold_max",
            )

    if phase2_map_routing_warning_messages:
        phase2_map_routing_warning = "; ".join(phase2_map_routing_warning_messages)
        phase2_map_routing_warning_reasons = list(dict.fromkeys(phase2_map_routing_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"

    phase2_sensor_fidelity_warning = ""
    phase2_sensor_fidelity_warning_messages: list[str] = []
    phase2_sensor_fidelity_warning_reasons: list[str] = []
    if phase2_sensor_fidelity_summary:
        try:
            phase2_sensor_fidelity_evaluated_count = int(
                phase2_sensor_fidelity_summary.get("evaluated_manifest_count", 0)
            )
        except (TypeError, ValueError):
            phase2_sensor_fidelity_evaluated_count = 0
        if phase2_sensor_fidelity_evaluated_count > 0:
            try:
                phase2_sensor_fidelity_score_avg = float(
                    phase2_sensor_fidelity_summary.get("fidelity_tier_score_avg", 0.0)
                )
            except (TypeError, ValueError):
                phase2_sensor_fidelity_score_avg = 0.0
            try:
                phase2_sensor_frame_count_avg = float(
                    phase2_sensor_fidelity_summary.get("sensor_frame_count_avg", 0.0)
                )
            except (TypeError, ValueError):
                phase2_sensor_frame_count_avg = 0.0
            try:
                phase2_sensor_camera_noise_stddev_px_avg = float(
                    phase2_sensor_fidelity_summary.get("sensor_camera_noise_stddev_px_avg", 0.0)
                )
            except (TypeError, ValueError):
                phase2_sensor_camera_noise_stddev_px_avg = 0.0
            try:
                phase2_sensor_lidar_point_count_avg = float(
                    phase2_sensor_fidelity_summary.get("sensor_lidar_point_count_avg", 0.0)
                )
            except (TypeError, ValueError):
                phase2_sensor_lidar_point_count_avg = 0.0
            try:
                phase2_sensor_radar_false_positive_rate_avg = float(
                    phase2_sensor_fidelity_summary.get("sensor_radar_false_positive_rate_avg", 0.0)
                )
            except (TypeError, ValueError):
                phase2_sensor_radar_false_positive_rate_avg = 0.0

            def _append_phase2_sensor_fidelity_float_min_warning(
                *,
                metric_label: str,
                value: float,
                warn_min: float,
                hold_min: float,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_min > 0 and value < hold_min:
                    phase2_sensor_fidelity_warning_messages.append(
                        f"{metric_label}={value:.3f} below hold_min={hold_min:.3f}"
                    )
                    phase2_sensor_fidelity_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_min > 0 and value < warn_min:
                    phase2_sensor_fidelity_warning_messages.append(
                        f"{metric_label}={value:.3f} below warn_min={warn_min:.3f}"
                    )
                    phase2_sensor_fidelity_warning_reasons.append(reason_warn)

            def _append_phase2_sensor_fidelity_float_max_warning(
                *,
                metric_label: str,
                value: float,
                warn_max: float,
                hold_max: float,
                reason_warn: str,
                reason_hold: str,
            ) -> None:
                nonlocal status
                if hold_max > 0 and value > hold_max:
                    phase2_sensor_fidelity_warning_messages.append(
                        f"{metric_label}={value:.3f} above hold_max={hold_max:.3f}"
                    )
                    phase2_sensor_fidelity_warning_reasons.append(reason_hold)
                    status = "HOLD"
                    return
                if warn_max > 0 and value > warn_max:
                    phase2_sensor_fidelity_warning_messages.append(
                        f"{metric_label}={value:.3f} above warn_max={warn_max:.3f}"
                    )
                    phase2_sensor_fidelity_warning_reasons.append(reason_warn)

            _append_phase2_sensor_fidelity_float_min_warning(
                metric_label="phase2_sensor_fidelity_score_avg",
                value=phase2_sensor_fidelity_score_avg,
                warn_min=phase2_sensor_fidelity_score_avg_warn_min,
                hold_min=phase2_sensor_fidelity_score_avg_hold_min,
                reason_warn="phase2_sensor_fidelity_score_avg_below_warn_min",
                reason_hold="phase2_sensor_fidelity_score_avg_below_hold_min",
            )
            _append_phase2_sensor_fidelity_float_min_warning(
                metric_label="phase2_sensor_frame_count_avg",
                value=phase2_sensor_frame_count_avg,
                warn_min=phase2_sensor_frame_count_avg_warn_min,
                hold_min=phase2_sensor_frame_count_avg_hold_min,
                reason_warn="phase2_sensor_frame_count_avg_below_warn_min",
                reason_hold="phase2_sensor_frame_count_avg_below_hold_min",
            )
            _append_phase2_sensor_fidelity_float_max_warning(
                metric_label="phase2_sensor_camera_noise_stddev_px_avg",
                value=phase2_sensor_camera_noise_stddev_px_avg,
                warn_max=phase2_sensor_camera_noise_stddev_px_avg_warn_max,
                hold_max=phase2_sensor_camera_noise_stddev_px_avg_hold_max,
                reason_warn="phase2_sensor_camera_noise_stddev_px_avg_above_warn_max",
                reason_hold="phase2_sensor_camera_noise_stddev_px_avg_above_hold_max",
            )
            _append_phase2_sensor_fidelity_float_min_warning(
                metric_label="phase2_sensor_lidar_point_count_avg",
                value=phase2_sensor_lidar_point_count_avg,
                warn_min=phase2_sensor_lidar_point_count_avg_warn_min,
                hold_min=phase2_sensor_lidar_point_count_avg_hold_min,
                reason_warn="phase2_sensor_lidar_point_count_avg_below_warn_min",
                reason_hold="phase2_sensor_lidar_point_count_avg_below_hold_min",
            )
            _append_phase2_sensor_fidelity_float_max_warning(
                metric_label="phase2_sensor_radar_false_positive_rate_avg",
                value=phase2_sensor_radar_false_positive_rate_avg,
                warn_max=phase2_sensor_radar_false_positive_rate_avg_warn_max,
                hold_max=phase2_sensor_radar_false_positive_rate_avg_hold_max,
                reason_warn="phase2_sensor_radar_false_positive_rate_avg_above_warn_max",
                reason_hold="phase2_sensor_radar_false_positive_rate_avg_above_hold_max",
            )

    if phase2_sensor_fidelity_warning_messages:
        phase2_sensor_fidelity_warning = "; ".join(phase2_sensor_fidelity_warning_messages)
        phase2_sensor_fidelity_warning_reasons = list(dict.fromkeys(phase2_sensor_fidelity_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"

    phase4_primary_warning = ""
    phase4_primary_warning_messages: list[str] = []
    phase4_primary_warning_reasons: list[str] = []
    phase4_primary_coverage_rows: list[dict[str, Any]] = []
    phase4_primary_module_warning_rows: list[dict[str, Any]] = []
    phase4_primary_module_hold_rows: list[dict[str, Any]] = []
    phase4_primary_module_warning_summary: dict[str, dict[str, Any]] = {}
    phase4_primary_module_hold_summary: dict[str, dict[str, Any]] = {}
    phase4_secondary_warning = ""
    phase4_secondary_warning_messages: list[str] = []
    phase4_secondary_warning_reasons: list[str] = []
    phase4_secondary_coverage_rows: list[dict[str, Any]] = []
    phase4_secondary_module_warning_rows: list[dict[str, Any]] = []
    phase4_secondary_module_hold_rows: list[dict[str, Any]] = []
    phase4_secondary_module_warning_summary: dict[str, dict[str, Any]] = {}
    phase4_secondary_module_hold_summary: dict[str, dict[str, Any]] = {}
    if pipeline_manifests:
        for item in pipeline_manifests:
            if not isinstance(item, dict):
                continue
            primary_coverage_raw = item.get("phase4_reference_primary_total_coverage_ratio", 0.0)
            primary_module_coverage_raw = item.get("phase4_reference_primary_module_coverage", {})
            secondary_module_count_raw = item.get("phase4_reference_secondary_module_count", 0)
            secondary_coverage_raw = item.get("phase4_reference_secondary_total_coverage_ratio", 0.0)
            secondary_module_coverage_raw = item.get("phase4_reference_secondary_module_coverage", {})
            try:
                primary_coverage = float(primary_coverage_raw)
            except (TypeError, ValueError):
                primary_coverage = 0.0
            try:
                secondary_module_count = int(secondary_module_count_raw)
            except (TypeError, ValueError):
                secondary_module_count = 0
            try:
                secondary_coverage = float(secondary_coverage_raw)
            except (TypeError, ValueError):
                secondary_coverage = 0.0
            primary_module_coverage = _as_float_map(primary_module_coverage_raw)
            secondary_module_coverage = _as_float_map(secondary_module_coverage_raw)
            phase4_primary_coverage_rows.append(
                {
                    "batch_id": str(item.get("batch_id", "")).strip() or "batch_unknown",
                    "primary_coverage_ratio": primary_coverage,
                    "primary_module_coverage": primary_module_coverage,
                }
            )
            phase4_secondary_coverage_rows.append(
                {
                    "batch_id": str(item.get("batch_id", "")).strip() or "batch_unknown",
                    "secondary_module_count": secondary_module_count,
                    "secondary_coverage_ratio": secondary_coverage,
                    "secondary_module_coverage": secondary_module_coverage,
                }
            )

        if (phase4_primary_warn_ratio > 0 or phase4_primary_hold_ratio > 0) and phase4_primary_coverage_rows:
            lowest_primary_row = min(
                phase4_primary_coverage_rows,
                key=lambda row: (
                    float(row.get("primary_coverage_ratio", 0.0)),
                    str(row.get("batch_id", "")),
                ),
            )
            lowest_primary_coverage = float(lowest_primary_row.get("primary_coverage_ratio", 0.0))
            if phase4_primary_hold_ratio > 0 and lowest_primary_coverage < phase4_primary_hold_ratio:
                phase4_primary_warning_messages.append(
                    "phase4_primary_coverage="
                    f"{lowest_primary_coverage:.3f} below hold_threshold={phase4_primary_hold_ratio:.3f} "
                    f"(batch={lowest_primary_row.get('batch_id')})"
                )
                phase4_primary_warning_reasons.append("phase4_primary_coverage_below_hold_threshold")
                status = "HOLD"
            if lowest_primary_coverage < phase4_primary_warn_ratio:
                phase4_primary_warning_messages.append(
                    "phase4_primary_coverage="
                    f"{lowest_primary_coverage:.3f} below threshold={phase4_primary_warn_ratio:.3f} "
                    f"(batch={lowest_primary_row.get('batch_id')})"
                )
                phase4_primary_warning_reasons.append("phase4_primary_coverage_below_threshold")

        if phase4_primary_module_warn_thresholds:
            for row in phase4_primary_coverage_rows:
                batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
                module_coverage = _as_float_map(row.get("primary_module_coverage", {}))
                for module_name, threshold in phase4_primary_module_warn_thresholds.items():
                    if module_name not in module_coverage:
                        continue
                    coverage_ratio = float(module_coverage.get(module_name, 0.0))
                    if coverage_ratio < float(threshold):
                        phase4_primary_module_warning_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )

            if phase4_primary_module_warning_rows:
                lowest_module_row = min(
                    phase4_primary_module_warning_rows,
                    key=lambda row: (
                        float(row.get("coverage_ratio", 0.0)),
                        str(row.get("batch_id", "")),
                        str(row.get("module", "")),
                    ),
                )
                phase4_primary_warning_messages.append(
                    "phase4_primary_module_coverage="
                    f"{float(lowest_module_row.get('coverage_ratio', 0.0)):.3f} below threshold="
                    f"{float(lowest_module_row.get('threshold', 0.0)):.3f} "
                    f"(batch={lowest_module_row.get('batch_id')}, module={lowest_module_row.get('module')})"
                )
                phase4_primary_warning_reasons.append("phase4_primary_module_coverage_below_threshold")

        if phase4_primary_module_hold_thresholds:
            for row in phase4_primary_coverage_rows:
                batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
                module_coverage = _as_float_map(row.get("primary_module_coverage", {}))
                for module_name, threshold in phase4_primary_module_hold_thresholds.items():
                    if module_name not in module_coverage:
                        continue
                    coverage_ratio = float(module_coverage.get(module_name, 0.0))
                    if coverage_ratio < float(threshold):
                        phase4_primary_module_hold_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )

            if phase4_primary_module_hold_rows:
                lowest_module_hold_row = min(
                    phase4_primary_module_hold_rows,
                    key=lambda row: (
                        float(row.get("coverage_ratio", 0.0)),
                        str(row.get("batch_id", "")),
                        str(row.get("module", "")),
                    ),
                )
                phase4_primary_warning_messages.append(
                    "phase4_primary_module_coverage="
                    f"{float(lowest_module_hold_row.get('coverage_ratio', 0.0)):.3f} below hold_threshold="
                    f"{float(lowest_module_hold_row.get('threshold', 0.0)):.3f} "
                    f"(batch={lowest_module_hold_row.get('batch_id')}, module={lowest_module_hold_row.get('module')})"
                )
                phase4_primary_warning_reasons.append("phase4_primary_module_coverage_below_hold_threshold")
                status = "HOLD"

        if phase4_secondary_warn_ratio > 0 or phase4_secondary_hold_ratio > 0:
            evaluated_rows = [
                row for row in phase4_secondary_coverage_rows
                if int(row.get("secondary_module_count", 0)) >= phase4_secondary_warn_min_modules
            ]
            if evaluated_rows:
                lowest_row = min(
                    evaluated_rows,
                    key=lambda row: (
                        float(row.get("secondary_coverage_ratio", 0.0)),
                        str(row.get("batch_id", "")),
                    ),
                )
                lowest_coverage = float(lowest_row.get("secondary_coverage_ratio", 0.0))
                if phase4_secondary_hold_ratio > 0 and lowest_coverage < phase4_secondary_hold_ratio:
                    phase4_secondary_warning_messages.append(
                        "phase4_secondary_coverage="
                        f"{lowest_coverage:.3f} below hold_threshold={phase4_secondary_hold_ratio:.3f} "
                        f"(batch={lowest_row.get('batch_id')}, min_modules={phase4_secondary_warn_min_modules})"
                    )
                    phase4_secondary_warning_reasons.append("phase4_secondary_coverage_below_hold_threshold")
                    status = "HOLD"
                if lowest_coverage < phase4_secondary_warn_ratio:
                    phase4_secondary_warning_messages.append(
                        "phase4_secondary_coverage="
                        f"{lowest_coverage:.3f} below threshold={phase4_secondary_warn_ratio:.3f} "
                        f"(batch={lowest_row.get('batch_id')}, min_modules={phase4_secondary_warn_min_modules})"
                    )
                    phase4_secondary_warning_reasons.append("phase4_secondary_coverage_below_threshold")

        if phase4_secondary_module_warn_thresholds:
            for row in phase4_secondary_coverage_rows:
                batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
                module_coverage = _as_float_map(row.get("secondary_module_coverage", {}))
                for module_name, threshold in phase4_secondary_module_warn_thresholds.items():
                    if module_name not in module_coverage:
                        continue
                    coverage_ratio = float(module_coverage.get(module_name, 0.0))
                    if coverage_ratio < float(threshold):
                        phase4_secondary_module_warning_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )

            if phase4_secondary_module_warning_rows:
                lowest_module_row = min(
                    phase4_secondary_module_warning_rows,
                    key=lambda row: (
                        float(row.get("coverage_ratio", 0.0)),
                        str(row.get("batch_id", "")),
                        str(row.get("module", "")),
                    ),
                )
                phase4_secondary_warning_messages.append(
                    "phase4_secondary_module_coverage="
                    f"{float(lowest_module_row.get('coverage_ratio', 0.0)):.3f} below threshold="
                    f"{float(lowest_module_row.get('threshold', 0.0)):.3f} "
                    f"(batch={lowest_module_row.get('batch_id')}, module={lowest_module_row.get('module')})"
                )
                phase4_secondary_warning_reasons.append("phase4_secondary_module_coverage_below_threshold")

        if phase4_secondary_module_hold_thresholds:
            for row in phase4_secondary_coverage_rows:
                batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
                module_coverage = _as_float_map(row.get("secondary_module_coverage", {}))
                for module_name, threshold in phase4_secondary_module_hold_thresholds.items():
                    if module_name not in module_coverage:
                        continue
                    coverage_ratio = float(module_coverage.get(module_name, 0.0))
                    if coverage_ratio < float(threshold):
                        phase4_secondary_module_hold_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )

            if phase4_secondary_module_hold_rows:
                lowest_module_hold_row = min(
                    phase4_secondary_module_hold_rows,
                    key=lambda row: (
                        float(row.get("coverage_ratio", 0.0)),
                        str(row.get("batch_id", "")),
                        str(row.get("module", "")),
                    ),
                )
                phase4_secondary_warning_messages.append(
                    "phase4_secondary_module_coverage="
                    f"{float(lowest_module_hold_row.get('coverage_ratio', 0.0)):.3f} below hold_threshold="
                    f"{float(lowest_module_hold_row.get('threshold', 0.0)):.3f} "
                    f"(batch={lowest_module_hold_row.get('batch_id')}, module={lowest_module_hold_row.get('module')})"
                )
                phase4_secondary_warning_reasons.append("phase4_secondary_module_coverage_below_hold_threshold")
                status = "HOLD"

    if not pipeline_manifests and phase4_primary_coverage_summary:
        try:
            primary_evaluated_count = int(phase4_primary_coverage_summary.get("evaluated_manifest_count", 0) or 0)
        except (TypeError, ValueError):
            primary_evaluated_count = 0
        if primary_evaluated_count > 0:
            try:
                summary_primary_min_coverage = float(
                    phase4_primary_coverage_summary.get("min_coverage_ratio", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                summary_primary_min_coverage = 0.0
            summary_primary_lowest_batch = (
                str(phase4_primary_coverage_summary.get("lowest_batch_id", "")).strip() or "batch_unknown"
            )
            if not phase4_primary_coverage_rows:
                phase4_primary_coverage_rows.append(
                    {
                        "batch_id": summary_primary_lowest_batch,
                        "primary_coverage_ratio": summary_primary_min_coverage,
                        "primary_module_coverage": {},
                    }
                )
            if phase4_primary_hold_ratio > 0 and summary_primary_min_coverage < phase4_primary_hold_ratio:
                phase4_primary_warning_messages.append(
                    "phase4_primary_coverage="
                    f"{summary_primary_min_coverage:.3f} below hold_threshold={phase4_primary_hold_ratio:.3f} "
                    f"(batch={summary_primary_lowest_batch})"
                )
                phase4_primary_warning_reasons.append("phase4_primary_coverage_below_hold_threshold")
                status = "HOLD"
            if summary_primary_min_coverage < phase4_primary_warn_ratio:
                phase4_primary_warning_messages.append(
                    "phase4_primary_coverage="
                    f"{summary_primary_min_coverage:.3f} below threshold={phase4_primary_warn_ratio:.3f} "
                    f"(batch={summary_primary_lowest_batch})"
                )
                phase4_primary_warning_reasons.append("phase4_primary_coverage_below_threshold")

            primary_module_summary_raw = phase4_primary_coverage_summary.get("module_coverage_summary", {})
            primary_module_summary = (
                primary_module_summary_raw if isinstance(primary_module_summary_raw, dict) else {}
            )
            if phase4_primary_module_warn_thresholds and primary_module_summary:
                for module_name, threshold in phase4_primary_module_warn_thresholds.items():
                    module_row = primary_module_summary.get(module_name)
                    if not isinstance(module_row, dict):
                        continue
                    try:
                        coverage_ratio = float(module_row.get("min_coverage_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        coverage_ratio = 0.0
                    batch_id = str(module_row.get("lowest_batch_id", "")).strip() or "batch_unknown"
                    if coverage_ratio < float(threshold):
                        phase4_primary_module_warning_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )
                if phase4_primary_module_warning_rows:
                    lowest_module_row = min(
                        phase4_primary_module_warning_rows,
                        key=lambda row: (
                            float(row.get("coverage_ratio", 0.0)),
                            str(row.get("batch_id", "")),
                            str(row.get("module", "")),
                        ),
                    )
                    phase4_primary_warning_messages.append(
                        "phase4_primary_module_coverage="
                        f"{float(lowest_module_row.get('coverage_ratio', 0.0)):.3f} below threshold="
                        f"{float(lowest_module_row.get('threshold', 0.0)):.3f} "
                        f"(batch={lowest_module_row.get('batch_id')}, module={lowest_module_row.get('module')})"
                    )
                    phase4_primary_warning_reasons.append("phase4_primary_module_coverage_below_threshold")

            if phase4_primary_module_hold_thresholds and primary_module_summary:
                for module_name, threshold in phase4_primary_module_hold_thresholds.items():
                    module_row = primary_module_summary.get(module_name)
                    if not isinstance(module_row, dict):
                        continue
                    try:
                        coverage_ratio = float(module_row.get("min_coverage_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        coverage_ratio = 0.0
                    batch_id = str(module_row.get("lowest_batch_id", "")).strip() or "batch_unknown"
                    if coverage_ratio < float(threshold):
                        phase4_primary_module_hold_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )
                if phase4_primary_module_hold_rows:
                    lowest_module_hold_row = min(
                        phase4_primary_module_hold_rows,
                        key=lambda row: (
                            float(row.get("coverage_ratio", 0.0)),
                            str(row.get("batch_id", "")),
                            str(row.get("module", "")),
                        ),
                    )
                    phase4_primary_warning_messages.append(
                        "phase4_primary_module_coverage="
                        f"{float(lowest_module_hold_row.get('coverage_ratio', 0.0)):.3f} below hold_threshold="
                        f"{float(lowest_module_hold_row.get('threshold', 0.0)):.3f} "
                        f"(batch={lowest_module_hold_row.get('batch_id')}, module={lowest_module_hold_row.get('module')})"
                    )
                    phase4_primary_warning_reasons.append("phase4_primary_module_coverage_below_hold_threshold")
                    status = "HOLD"

    if not pipeline_manifests and phase4_secondary_coverage_summary:
        try:
            secondary_evaluated_count = int(
                phase4_secondary_coverage_summary.get("evaluated_manifest_count", 0) or 0
            )
        except (TypeError, ValueError):
            secondary_evaluated_count = 0
        if secondary_evaluated_count > 0:
            try:
                summary_secondary_min_coverage = float(
                    phase4_secondary_coverage_summary.get("min_coverage_ratio", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                summary_secondary_min_coverage = 0.0
            summary_secondary_lowest_batch = (
                str(phase4_secondary_coverage_summary.get("lowest_batch_id", "")).strip() or "batch_unknown"
            )
            try:
                summary_secondary_lowest_batch_module_count = int(
                    phase4_secondary_coverage_summary.get("lowest_batch_secondary_module_count", 0) or 0
                )
            except (TypeError, ValueError):
                summary_secondary_lowest_batch_module_count = 0
            secondary_coverage_by_min_modules_raw = phase4_secondary_coverage_summary.get(
                "secondary_coverage_by_min_modules", {}
            )
            secondary_coverage_by_min_modules = (
                secondary_coverage_by_min_modules_raw
                if isinstance(secondary_coverage_by_min_modules_raw, dict)
                else {}
            )
            min_modules_key = str(phase4_secondary_warn_min_modules)
            summary_by_min_modules_raw = secondary_coverage_by_min_modules.get(min_modules_key, {})
            summary_by_min_modules = (
                summary_by_min_modules_raw if isinstance(summary_by_min_modules_raw, dict) else {}
            )
            summary_secondary_effective_min_coverage = summary_secondary_min_coverage
            summary_secondary_effective_batch = summary_secondary_lowest_batch
            summary_secondary_effective_module_count = summary_secondary_lowest_batch_module_count
            summary_secondary_effective_evaluated_count = 0
            if summary_by_min_modules:
                try:
                    summary_secondary_effective_evaluated_count = int(
                        summary_by_min_modules.get("evaluated_manifest_count", 0) or 0
                    )
                except (TypeError, ValueError):
                    summary_secondary_effective_evaluated_count = 0
                try:
                    summary_secondary_effective_min_coverage = float(
                        summary_by_min_modules.get("min_coverage_ratio", summary_secondary_min_coverage) or 0.0
                    )
                except (TypeError, ValueError):
                    summary_secondary_effective_min_coverage = summary_secondary_min_coverage
                summary_secondary_effective_batch = (
                    str(summary_by_min_modules.get("lowest_batch_id", "")).strip()
                    or summary_secondary_lowest_batch
                )
                try:
                    summary_secondary_effective_module_count = int(
                        summary_by_min_modules.get("lowest_batch_secondary_module_count", 0) or 0
                    )
                except (TypeError, ValueError):
                    summary_secondary_effective_module_count = 0
            elif phase4_secondary_warn_min_modules <= 1:
                summary_secondary_effective_evaluated_count = secondary_evaluated_count
                if summary_secondary_effective_module_count <= 0:
                    summary_secondary_effective_module_count = 1
            elif summary_secondary_lowest_batch_module_count >= phase4_secondary_warn_min_modules:
                summary_secondary_effective_evaluated_count = secondary_evaluated_count

            if not phase4_secondary_coverage_rows:
                phase4_secondary_coverage_rows.append(
                    {
                        "batch_id": summary_secondary_effective_batch,
                        "secondary_module_count": summary_secondary_effective_module_count,
                        "secondary_coverage_ratio": summary_secondary_effective_min_coverage,
                        "secondary_module_coverage": {},
                    }
                )
            if summary_secondary_effective_evaluated_count > 0:
                if (
                    phase4_secondary_hold_ratio > 0
                    and summary_secondary_effective_min_coverage < phase4_secondary_hold_ratio
                ):
                    phase4_secondary_warning_messages.append(
                        "phase4_secondary_coverage="
                        f"{summary_secondary_effective_min_coverage:.3f} below hold_threshold={phase4_secondary_hold_ratio:.3f} "
                        f"(batch={summary_secondary_effective_batch}, min_modules={phase4_secondary_warn_min_modules})"
                    )
                    phase4_secondary_warning_reasons.append("phase4_secondary_coverage_below_hold_threshold")
                    status = "HOLD"
                if summary_secondary_effective_min_coverage < phase4_secondary_warn_ratio:
                    phase4_secondary_warning_messages.append(
                        "phase4_secondary_coverage="
                        f"{summary_secondary_effective_min_coverage:.3f} below threshold={phase4_secondary_warn_ratio:.3f} "
                        f"(batch={summary_secondary_effective_batch}, min_modules={phase4_secondary_warn_min_modules})"
                    )
                    phase4_secondary_warning_reasons.append("phase4_secondary_coverage_below_threshold")

            secondary_module_summary_raw = phase4_secondary_coverage_summary.get("module_coverage_summary", {})
            secondary_module_summary = (
                secondary_module_summary_raw if isinstance(secondary_module_summary_raw, dict) else {}
            )
            if phase4_secondary_module_warn_thresholds and secondary_module_summary:
                for module_name, threshold in phase4_secondary_module_warn_thresholds.items():
                    module_row = secondary_module_summary.get(module_name)
                    if not isinstance(module_row, dict):
                        continue
                    try:
                        coverage_ratio = float(module_row.get("min_coverage_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        coverage_ratio = 0.0
                    batch_id = str(module_row.get("lowest_batch_id", "")).strip() or "batch_unknown"
                    if coverage_ratio < float(threshold):
                        phase4_secondary_module_warning_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )
                if phase4_secondary_module_warning_rows:
                    lowest_module_row = min(
                        phase4_secondary_module_warning_rows,
                        key=lambda row: (
                            float(row.get("coverage_ratio", 0.0)),
                            str(row.get("batch_id", "")),
                            str(row.get("module", "")),
                        ),
                    )
                    phase4_secondary_warning_messages.append(
                        "phase4_secondary_module_coverage="
                        f"{float(lowest_module_row.get('coverage_ratio', 0.0)):.3f} below threshold="
                        f"{float(lowest_module_row.get('threshold', 0.0)):.3f} "
                        f"(batch={lowest_module_row.get('batch_id')}, module={lowest_module_row.get('module')})"
                    )
                    phase4_secondary_warning_reasons.append("phase4_secondary_module_coverage_below_threshold")

            if phase4_secondary_module_hold_thresholds and secondary_module_summary:
                for module_name, threshold in phase4_secondary_module_hold_thresholds.items():
                    module_row = secondary_module_summary.get(module_name)
                    if not isinstance(module_row, dict):
                        continue
                    try:
                        coverage_ratio = float(module_row.get("min_coverage_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        coverage_ratio = 0.0
                    batch_id = str(module_row.get("lowest_batch_id", "")).strip() or "batch_unknown"
                    if coverage_ratio < float(threshold):
                        phase4_secondary_module_hold_rows.append(
                            {
                                "batch_id": batch_id,
                                "module": module_name,
                                "coverage_ratio": coverage_ratio,
                                "threshold": float(threshold),
                            }
                        )
                if phase4_secondary_module_hold_rows:
                    lowest_module_hold_row = min(
                        phase4_secondary_module_hold_rows,
                        key=lambda row: (
                            float(row.get("coverage_ratio", 0.0)),
                            str(row.get("batch_id", "")),
                            str(row.get("module", "")),
                        ),
                    )
                    phase4_secondary_warning_messages.append(
                        "phase4_secondary_module_coverage="
                        f"{float(lowest_module_hold_row.get('coverage_ratio', 0.0)):.3f} below hold_threshold="
                        f"{float(lowest_module_hold_row.get('threshold', 0.0)):.3f} "
                        f"(batch={lowest_module_hold_row.get('batch_id')}, module={lowest_module_hold_row.get('module')})"
                    )
                    phase4_secondary_warning_reasons.append("phase4_secondary_module_coverage_below_hold_threshold")
                    status = "HOLD"

    if phase4_primary_warning_messages:
        phase4_primary_warning = "; ".join(phase4_primary_warning_messages)
        phase4_primary_warning_reasons = list(dict.fromkeys(phase4_primary_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"
    if phase4_secondary_warning_messages:
        phase4_secondary_warning = "; ".join(phase4_secondary_warning_messages)
        phase4_secondary_warning_reasons = list(dict.fromkeys(phase4_secondary_warning_reasons))
        if status in {"PASS", "INFO"}:
            status = "WARN"
    phase4_primary_module_warning_summary = _summarize_phase4_module_violation_rows(
        phase4_primary_module_warning_rows
    )
    phase4_primary_module_hold_summary = _summarize_phase4_module_violation_rows(
        phase4_primary_module_hold_rows
    )
    phase4_primary_module_warning_summary_text = _format_phase4_module_violation_summary(
        phase4_primary_module_warning_summary
    )
    phase4_primary_module_hold_summary_text = _format_phase4_module_violation_summary(
        phase4_primary_module_hold_summary
    )
    phase4_secondary_module_warning_summary = _summarize_phase4_secondary_module_violation_rows(
        phase4_secondary_module_warning_rows
    )
    phase4_secondary_module_hold_summary = _summarize_phase4_secondary_module_violation_rows(
        phase4_secondary_module_hold_rows
    )
    phase4_secondary_module_warning_summary_text = _format_phase4_secondary_module_violation_summary(
        phase4_secondary_module_warning_summary
    )
    phase4_secondary_module_hold_summary_text = _format_phase4_secondary_module_violation_summary(
        phase4_secondary_module_hold_summary
    )

    reason_code_diff = summary_payload.get("reason_code_diff", {})
    if not isinstance(reason_code_diff, dict):
        reason_code_diff = {}
    only_in_a = reason_code_diff.get("codes_only_in_a", [])
    only_in_b = reason_code_diff.get("codes_only_in_b", [])
    common_codes = reason_code_diff.get("codes_common", [])
    if not isinstance(only_in_a, list):
        only_in_a = []
    if not isinstance(only_in_b, list):
        only_in_b = []
    if not isinstance(common_codes, list):
        common_codes = []
    only_in_a = [str(code).strip() for code in only_in_a if str(code).strip()]
    only_in_b = [str(code).strip() for code in only_in_b if str(code).strip()]
    common_codes = [str(code).strip() for code in common_codes if str(code).strip()]

    title = f"[{status}] {release_prefix}" if release_prefix else f"[{status}] Release Summary"
    workflow_name = str(args.workflow_name).strip()
    run_url = str(args.run_url).strip()

    summary_line = (
        f"summary_count={summary_count}, "
        f"pipeline_manifest_count={pipeline_manifest_count}, "
        f"sds_versions={','.join(sds_versions_list) if sds_versions_list else 'n/a'}, "
        f"final={','.join(f'{k}:{v}' for k, v in sorted(final_counts.items())) or 'n/a'}"
    )
    if timing_total_ms is not None:
        summary_line += f", timing_total_ms={timing_total_ms}"
    pipeline_line = (
        f"pipeline_overall={','.join(f'{k}:{v}' for k, v in sorted(pipeline_overall_counts.items())) or 'n/a'}, "
        f"pipeline_trend={','.join(f'{k}:{v}' for k, v in sorted(pipeline_trend_counts.items())) or 'n/a'}"
    )
    reason_line = (
        f"codes_only_in_a={','.join(_truncate_list(only_in_a, max_codes)) or 'n/a'}; "
        f"codes_only_in_b={','.join(_truncate_list(only_in_b, max_codes)) or 'n/a'}; "
        f"codes_common={','.join(_truncate_list(common_codes, max_codes)) or 'n/a'}"
    )

    text_lines: list[str] = [title, summary_line, pipeline_line]
    if warning:
        text_lines.append(f"warning={warning}")
    if timing_warning:
        text_lines.append(f"timing_warning={timing_warning}")
        text_lines.append(
            "timing_warning_severity="
            f"{timing_warning_severity}; reasons={','.join(timing_warning_reasons) or 'n/a'}"
        )
        if slowest_stages_text != "n/a":
            text_lines.append(f"slowest_stages_ms={slowest_stages_text}")
        if timing_threshold:
            text_lines.append(
                "timing_threshold="
                + ",".join(
                    [
                        f"warn_ms={timing_threshold.get('warn_ms')}",
                        f"current_ms={timing_threshold.get('current_ms')}",
                        f"ratio={timing_threshold.get('ratio')}",
                        f"exceeded={timing_threshold.get('exceeded')}",
                    ]
                )
            )
    if timing_regression:
        text_lines.append(
            "timing_regression="
            + ",".join(
                [
                    f"baseline_source={timing_regression_baseline_source}",
                    f"baseline_ms={timing_regression.get('baseline_ms')}",
                    f"current_ms={timing_regression.get('current_ms')}",
                    f"delta_ms={timing_regression.get('delta_ms')}",
                    f"delta_ratio={timing_regression.get('delta_ratio')}",
                    f"warn_ratio={timing_regression.get('warn_ratio')}",
                    f"ratio_multiple={timing_regression.get('ratio_multiple')}",
                    f"exceeded={timing_regression.get('exceeded')}",
                ]
            )
        )
    if phase4_primary_warning:
        text_lines.append(f"phase4_primary_warning={phase4_primary_warning}")
        text_lines.append(
            "phase4_primary_warning_reasons="
            f"{','.join(phase4_primary_warning_reasons) or 'n/a'}"
        )
        if phase4_primary_module_warning_summary_text != "n/a":
            text_lines.append(
                "phase4_primary_module_warning_summary="
                f"{phase4_primary_module_warning_summary_text}"
            )
        if phase4_primary_module_hold_summary_text != "n/a":
            text_lines.append(
                "phase4_primary_module_hold_summary="
                f"{phase4_primary_module_hold_summary_text}"
            )
    if phase4_secondary_warning:
        text_lines.append(f"phase4_secondary_warning={phase4_secondary_warning}")
        text_lines.append(
            "phase4_secondary_warning_reasons="
            f"{','.join(phase4_secondary_warning_reasons) or 'n/a'}"
        )
        if phase4_secondary_module_warning_summary_text != "n/a":
            text_lines.append(
                "phase4_secondary_module_warning_summary="
                f"{phase4_secondary_module_warning_summary_text}"
            )
        if phase4_secondary_module_hold_summary_text != "n/a":
            text_lines.append(
                "phase4_secondary_module_hold_summary="
                f"{phase4_secondary_module_hold_summary_text}"
            )
    if phase3_vehicle_dynamics_summary_text != "n/a":
        text_lines.append(
            "phase3_vehicle_dynamics_summary="
            f"{phase3_vehicle_dynamics_summary_text}"
        )
    if phase3_core_sim_summary_text != "n/a":
        text_lines.append(
            "phase3_core_sim_summary="
            f"{phase3_core_sim_summary_text}"
        )
    if phase3_core_sim_matrix_summary_text != "n/a":
        text_lines.append(
            "phase3_core_sim_matrix_summary="
            f"{phase3_core_sim_matrix_summary_text}"
        )
    if phase3_core_sim_matrix_warning:
        text_lines.append(f"phase3_core_sim_matrix_warning={phase3_core_sim_matrix_warning}")
        text_lines.append(
            "phase3_core_sim_matrix_warning_reasons="
            f"{','.join(phase3_core_sim_matrix_warning_reasons) or 'n/a'}"
        )
    if phase3_core_sim_summary_text != "n/a":
        text_lines.append(
            "phase3_core_sim_threshold_drift_detected="
            f"{1 if phase3_core_sim_threshold_drift_detected else 0}"
        )
        text_lines.append(
            "phase3_core_sim_threshold_drift_severity="
            f"{phase3_core_sim_threshold_drift_severity}"
        )
        text_lines.append(
            "phase3_core_sim_threshold_drift_summary="
            f"{phase3_core_sim_threshold_drift_summary_text}"
        )
        text_lines.append(
            "phase3_core_sim_threshold_drift_reasons="
            f"{','.join(phase3_core_sim_threshold_drift_reasons) or 'n/a'}"
        )
    if phase3_core_sim_warning:
        text_lines.append(f"phase3_core_sim_warning={phase3_core_sim_warning}")
        text_lines.append(
            "phase3_core_sim_warning_reasons="
            f"{','.join(phase3_core_sim_warning_reasons) or 'n/a'}"
        )
    if phase3_lane_risk_summary_text != "n/a":
        text_lines.append(
            "phase3_lane_risk_summary="
            f"{phase3_lane_risk_summary_text}"
        )
        text_lines.append(
            "phase3_lane_risk_threshold_drift_detected="
            f"{1 if phase3_lane_risk_threshold_drift_detected else 0}"
        )
        text_lines.append(
            "phase3_lane_risk_threshold_drift_severity="
            f"{phase3_lane_risk_threshold_drift_severity}"
        )
        text_lines.append(
            "phase3_lane_risk_threshold_drift_summary="
            f"{phase3_lane_risk_threshold_drift_summary_text}"
        )
        text_lines.append(
            "phase3_lane_risk_threshold_drift_reasons="
            f"{','.join(phase3_lane_risk_threshold_drift_reasons) or 'n/a'}"
        )
    if phase3_dataset_traffic_summary_text != "n/a":
        text_lines.append(
            "phase3_dataset_traffic_summary="
            f"{phase3_dataset_traffic_summary_text}"
        )
    if phase3_dataset_traffic_warning:
        text_lines.append(f"phase3_dataset_traffic_warning={phase3_dataset_traffic_warning}")
        text_lines.append(
            "phase3_dataset_traffic_warning_reasons="
            f"{','.join(phase3_dataset_traffic_warning_reasons) or 'n/a'}"
        )
    if phase3_dataset_traffic_summary_text != "n/a":
        text_lines.append(
            "phase3_dataset_traffic_threshold_drift_detected="
            f"{1 if phase3_dataset_traffic_threshold_drift_detected else 0}"
        )
        text_lines.append(
            "phase3_dataset_traffic_threshold_drift_severity="
            f"{phase3_dataset_traffic_threshold_drift_severity}"
        )
        text_lines.append(
            "phase3_dataset_traffic_threshold_drift_summary="
            f"{phase3_dataset_traffic_threshold_drift_summary_text}"
        )
        text_lines.append(
            "phase3_dataset_traffic_threshold_drift_reasons="
            f"{','.join(phase3_dataset_traffic_threshold_drift_reasons) or 'n/a'}"
        )
    if phase2_log_replay_summary_text != "n/a":
        text_lines.append(
            "phase2_log_replay_summary="
            f"{phase2_log_replay_summary_text}"
        )
    if phase2_log_replay_warning:
        text_lines.append(f"phase2_log_replay_warning={phase2_log_replay_warning}")
        text_lines.append(
            "phase2_log_replay_warning_reasons="
            f"{','.join(phase2_log_replay_warning_reasons) or 'n/a'}"
        )
    if phase2_map_routing_summary_text != "n/a":
        text_lines.append(
            "phase2_map_routing_summary="
            f"{phase2_map_routing_summary_text}"
        )
    if phase2_sensor_fidelity_summary_text != "n/a":
        text_lines.append(
            "phase2_sensor_fidelity_summary="
            f"{phase2_sensor_fidelity_summary_text}"
        )
    if phase2_map_routing_warning:
        text_lines.append(f"phase2_map_routing_warning={phase2_map_routing_warning}")
        text_lines.append(
            "phase2_map_routing_warning_reasons="
            f"{','.join(phase2_map_routing_warning_reasons) or 'n/a'}"
        )
    if phase2_sensor_fidelity_warning:
        text_lines.append(f"phase2_sensor_fidelity_warning={phase2_sensor_fidelity_warning}")
        text_lines.append(
            "phase2_sensor_fidelity_warning_reasons="
            f"{','.join(phase2_sensor_fidelity_warning_reasons) or 'n/a'}"
        )
    if phase3_lane_risk_warning:
        text_lines.append(f"phase3_lane_risk_warning={phase3_lane_risk_warning}")
        text_lines.append(
            "phase3_lane_risk_warning_reasons="
            f"{','.join(phase3_lane_risk_warning_reasons) or 'n/a'}"
        )
    if phase3_vehicle_dynamics_warning:
        text_lines.append(f"phase3_vehicle_dynamics_warning={phase3_vehicle_dynamics_warning}")
        text_lines.append(
            "phase3_vehicle_dynamics_warning_reasons="
            f"{','.join(phase3_vehicle_dynamics_warning_reasons) or 'n/a'}"
        )
    if phase3_vehicle_dynamics_violation_rows_text != "n/a":
        text_lines.append(
            "phase3_vehicle_dynamics_violation_rows="
            f"{phase3_vehicle_dynamics_violation_rows_text}"
        )
    if phase3_vehicle_dynamics_violation_summary_text != "n/a":
        text_lines.append(
            "phase3_vehicle_dynamics_violation_summary="
            f"{phase3_vehicle_dynamics_violation_summary_text}"
        )
    if runtime_native_smoke_summary_text != "n/a":
        text_lines.append(
            "runtime_native_smoke_summary="
            f"{runtime_native_smoke_summary_text}"
        )
    if runtime_native_summary_compare_summary_text != "n/a":
        text_lines.append(
            "runtime_native_summary_compare_summary="
            f"{runtime_native_summary_compare_summary_text}"
        )
    if runtime_native_evidence_compare_summary_text != "n/a":
        text_lines.append(
            "runtime_native_evidence_compare_summary="
            f"{runtime_native_evidence_compare_summary_text}"
        )
    if runtime_native_evidence_compare_interop_import_mode_diff_counts_text != "n/a":
        text_lines.append(
            "runtime_native_evidence_compare_interop_import_mode_diff_counts="
            f"{runtime_native_evidence_compare_interop_import_mode_diff_counts_text}"
        )
    if runtime_native_evidence_compare_warning:
        text_lines.append(
            "runtime_native_evidence_compare_warning="
            f"{runtime_native_evidence_compare_warning}"
        )
        text_lines.append(
            "runtime_native_evidence_compare_warning_reasons="
            f"{','.join(runtime_native_evidence_compare_warning_reasons) or 'n/a'}"
        )
    if runtime_native_smoke_warning:
        text_lines.append(f"runtime_native_smoke_warning={runtime_native_smoke_warning}")
        text_lines.append(
            "runtime_native_smoke_warning_reasons="
            f"{','.join(runtime_native_smoke_warning_reasons) or 'n/a'}"
        )
    if runtime_evidence_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_summary="
            f"{runtime_evidence_summary_text}"
        )
    if runtime_evidence_probe_args_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_probe_args_summary="
            f"{runtime_evidence_probe_args_summary_text}"
        )
    if runtime_evidence_scenario_contract_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_scenario_contract_summary="
            f"{runtime_evidence_scenario_contract_summary_text}"
        )
    if runtime_evidence_scene_result_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_scene_result_summary="
            f"{runtime_evidence_scene_result_summary_text}"
        )
    if runtime_evidence_interop_contract_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_interop_contract_summary="
            f"{runtime_evidence_interop_contract_summary_text}"
        )
    if runtime_evidence_interop_contract_warning:
        text_lines.append(
            "runtime_evidence_interop_contract_warning="
            f"{runtime_evidence_interop_contract_warning}"
        )
        text_lines.append(
            "runtime_evidence_interop_contract_warning_reasons="
            f"{','.join(runtime_evidence_interop_contract_warning_reasons) or 'n/a'}"
        )
    if runtime_evidence_interop_export_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_interop_export_summary="
            f"{runtime_evidence_interop_export_summary_text}"
        )
    if runtime_evidence_interop_import_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_interop_import_summary="
            f"{runtime_evidence_interop_import_summary_text}"
        )
    if runtime_evidence_interop_import_modes_text != "n/a":
        text_lines.append(
            "runtime_evidence_interop_import_modes="
            f"{runtime_evidence_interop_import_modes_text}"
        )
    if runtime_evidence_interop_import_inconsistent_records_text != "n/a":
        text_lines.append(
            "runtime_evidence_interop_import_inconsistent_records="
            f"{runtime_evidence_interop_import_inconsistent_records_text}"
        )
    if runtime_lane_execution_summary_text != "n/a":
        text_lines.append(
            "runtime_lane_execution_summary="
            f"{runtime_lane_execution_summary_text}"
        )
    if runtime_lane_phase2_rig_sweep_radar_alignment_summary_text != "n/a":
        text_lines.append(
            "runtime_lane_phase2_rig_sweep_radar_alignment_summary="
            f"{runtime_lane_phase2_rig_sweep_radar_alignment_summary_text}"
        )
    if runtime_evidence_compare_summary_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_summary="
            f"{runtime_evidence_compare_summary_text}"
        )
    if runtime_evidence_compare_interop_import_mode_diff_counts_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_mode_diff_counts="
            f"{runtime_evidence_compare_interop_import_mode_diff_counts_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diffs_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diffs="
            f"{runtime_evidence_compare_interop_import_profile_diffs_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_counts_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_counts="
            f"{runtime_evidence_compare_interop_import_profile_diff_counts_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_breakdown_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_breakdown="
            f"{runtime_evidence_compare_interop_import_profile_diff_breakdown_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text}"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text != "n/a":
        text_lines.append(
            "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts="
            f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text}"
        )
    if runtime_evidence_compare_warning:
        text_lines.append(f"runtime_evidence_compare_warning={runtime_evidence_compare_warning}")
        text_lines.append(
            "runtime_evidence_compare_warning_reasons="
            f"{','.join(runtime_evidence_compare_warning_reasons) or 'n/a'}"
        )
    if runtime_lane_execution_summary_text != "n/a" or runtime_evidence_compare_summary_text != "n/a":
        text_lines.append(
            "runtime_threshold_drift_detected="
            f"{1 if runtime_threshold_drift_detected else 0}"
        )
        text_lines.append(f"runtime_threshold_drift_severity={runtime_threshold_drift_severity}")
        text_lines.append(f"runtime_threshold_drift_summary={runtime_threshold_drift_summary_text}")
        text_lines.append(
            "runtime_threshold_drift_reasons="
            f"{','.join(runtime_threshold_drift_reasons) or 'n/a'}"
        )
        text_lines.append(
            "runtime_threshold_drift_hold_detected="
            f"{1 if runtime_threshold_drift_hold_detected else 0}"
        )
    if threshold_drift_hold_policy_failure_detected or threshold_drift_hold_policy_failure_count > 0:
        text_lines.append(
            "threshold_drift_hold_policy_failure_detected="
            f"{1 if threshold_drift_hold_policy_failure_detected else 0}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failure_count="
            f"{threshold_drift_hold_policy_failure_count}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failure_summary="
            f"{threshold_drift_hold_policy_failure_summary_text}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failures="
            f"{threshold_drift_hold_policy_failures_text}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failure_scope_counts="
            f"{threshold_drift_hold_policy_failure_scope_counts_text}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failure_scope_reason_key_counts="
            f"{threshold_drift_hold_policy_failure_scope_reason_key_counts_text}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failure_reason_keys="
            f"{threshold_drift_hold_policy_failure_reason_keys_text}"
        )
        text_lines.append(
            "threshold_drift_hold_policy_failure_reason_key_counts="
            f"{threshold_drift_hold_policy_failure_reason_key_counts_text}"
        )
    if runtime_lane_execution_warning:
        text_lines.append(f"runtime_lane_execution_warning={runtime_lane_execution_warning}")
        text_lines.append(
            "runtime_lane_execution_warning_reasons="
            f"{','.join(runtime_lane_execution_warning_reasons) or 'n/a'}"
        )
        if runtime_lane_execution_evidence_missing_runtimes_text != "n/a":
            text_lines.append(
                "runtime_lane_execution_evidence_missing_runtimes="
                f"{runtime_lane_execution_evidence_missing_runtimes_text}"
            )
        if runtime_lane_execution_failed_rows_text != "n/a":
            text_lines.append(
                "runtime_lane_execution_failed_rows="
                f"{runtime_lane_execution_failed_rows_text}"
            )
    if runtime_lane_phase2_rig_sweep_radar_alignment_warning:
        text_lines.append(
            "runtime_lane_phase2_rig_sweep_radar_alignment_warning="
            f"{runtime_lane_phase2_rig_sweep_radar_alignment_warning}"
        )
        text_lines.append(
            "runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons="
            f"{','.join(runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons) or 'n/a'}"
        )
    if runtime_evidence_warning:
        text_lines.append(f"runtime_evidence_warning={runtime_evidence_warning}")
        text_lines.append(
            "runtime_evidence_warning_reasons="
            f"{','.join(runtime_evidence_warning_reasons) or 'n/a'}"
        )
        if runtime_evidence_failed_records_text != "n/a":
            text_lines.append(
                "runtime_evidence_failed_records="
                f"{runtime_evidence_failed_records_text}"
            )
    if reason_code_diff:
        text_lines.append(reason_line)
    if run_url:
        text_lines.append(f"run_url={run_url}")
    text_body = "\n".join(text_lines)

    blocks: list[dict[str, Any]] = []
    blocks.append({"type": "header", "text": {"type": "plain_text", "text": title[:150]}})
    if workflow_name:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*workflow:* {workflow_name}"}],
            }
        )
    summary_fields: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*summary_count*\n{summary_count}"},
        {"type": "mrkdwn", "text": f"*pipeline_manifest_count*\n{pipeline_manifest_count}"},
        {
            "type": "mrkdwn",
            "text": f"*sds_versions*\n{', '.join(sds_versions_list) if sds_versions_list else 'n/a'}",
        },
        {
            "type": "mrkdwn",
            "text": f"*final_counts*\n{', '.join(f'{k}:{v}' for k, v in sorted(final_counts.items())) or 'n/a'}",
        },
        {
            "type": "mrkdwn",
            "text": f"*pipeline_overall*\n{', '.join(f'{k}:{v}' for k, v in sorted(pipeline_overall_counts.items())) or 'n/a'}",
        },
        {
            "type": "mrkdwn",
            "text": f"*pipeline_trend*\n{', '.join(f'{k}:{v}' for k, v in sorted(pipeline_trend_counts.items())) or 'n/a'}",
        },
    ]
    if timing_total_ms is not None:
        summary_fields.append({"type": "mrkdwn", "text": f"*timing_total_ms*\n{timing_total_ms}"})
    if timing_warning_severity:
        summary_fields.append({"type": "mrkdwn", "text": f"*timing_warning_severity*\n{timing_warning_severity}"})
    try:
        runtime_evidence_artifact_count_display = int(runtime_evidence_summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_artifact_count_display = 0
    try:
        runtime_evidence_failed_count_display = int(runtime_evidence_summary.get("failed_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_failed_count_display = 0
    try:
        runtime_lane_execution_artifact_count_display = int(runtime_lane_execution_summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        runtime_lane_execution_artifact_count_display = 0
    try:
        runtime_lane_execution_failed_count_display = int(runtime_lane_execution_summary.get("fail_count", 0))
    except (TypeError, ValueError):
        runtime_lane_execution_failed_count_display = 0
    try:
        runtime_evidence_compare_artifact_count_display = int(runtime_evidence_compare_summary.get("artifact_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_compare_artifact_count_display = 0
    try:
        runtime_evidence_compare_with_diffs_count_display = int(
            runtime_evidence_compare_summary.get("artifacts_with_diffs_count", 0)
        )
    except (TypeError, ValueError):
        runtime_evidence_compare_with_diffs_count_display = 0
    if runtime_evidence_artifact_count_display > 0:
        summary_fields.append(
            {
                "type": "mrkdwn",
                "text": f"*runtime_evidence_artifacts*\n{runtime_evidence_artifact_count_display}",
            }
        )
        summary_fields.append(
            {
                "type": "mrkdwn",
                "text": f"*runtime_evidence_failed*\n{runtime_evidence_failed_count_display}",
            }
        )
    if runtime_lane_execution_artifact_count_display > 0:
        summary_fields.append(
            {
                "type": "mrkdwn",
                "text": f"*runtime_lane_execution_artifacts*\n{runtime_lane_execution_artifact_count_display}",
            }
        )
        summary_fields.append(
            {
                "type": "mrkdwn",
                "text": f"*runtime_lane_execution_failed*\n{runtime_lane_execution_failed_count_display}",
            }
        )
    if runtime_evidence_compare_artifact_count_display > 0:
        summary_fields.append(
            {
                "type": "mrkdwn",
                "text": f"*runtime_compare_artifacts*\n{runtime_evidence_compare_artifact_count_display}",
            }
        )
        summary_fields.append(
            {
                "type": "mrkdwn",
                "text": f"*runtime_compare_with_diffs*\n{runtime_evidence_compare_with_diffs_count_display}",
            }
        )

    blocks.append({"type": "section", "fields": summary_fields})
    if reason_code_diff:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*hold reason code diff*\n"
                    + f"- only_in_a: {', '.join(_truncate_list(only_in_a, max_codes)) or 'n/a'}\n"
                    + f"- only_in_b: {', '.join(_truncate_list(only_in_b, max_codes)) or 'n/a'}\n"
                    + f"- common: {', '.join(_truncate_list(common_codes, max_codes)) or 'n/a'}",
                },
            }
        )
    if warning:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*warning*\n{warning}"}})
    if timing_warning:
        timing_lines = [f"*timing warning*\n{timing_warning}"]
        timing_lines.append(
            f"- severity: {timing_warning_severity or 'WARN'}; reasons: {', '.join(timing_warning_reasons) or 'n/a'}"
        )
        if slowest_stages_text != "n/a":
            timing_lines.append(f"- slowest_stages_ms: {slowest_stages_text}")
        if timing_threshold:
            timing_lines.append(
                "- timing_threshold: "
                + ", ".join(
                    [
                        f"warn_ms={timing_threshold.get('warn_ms')}",
                        f"current_ms={timing_threshold.get('current_ms')}",
                        f"ratio={timing_threshold.get('ratio')}",
                        f"exceeded={timing_threshold.get('exceeded')}",
                    ]
                )
            )
        if timing_regression:
            timing_lines.append(
                "- timing_regression: "
                + ", ".join(
                    [
                        f"baseline_source={timing_regression_baseline_source}",
                        f"baseline_ms={timing_regression.get('baseline_ms')}",
                        f"current_ms={timing_regression.get('current_ms')}",
                        f"delta_ms={timing_regression.get('delta_ms')}",
                        f"delta_ratio={timing_regression.get('delta_ratio')}",
                        f"warn_ratio={timing_regression.get('warn_ratio')}",
                        f"ratio_multiple={timing_regression.get('ratio_multiple')}",
                        f"exceeded={timing_regression.get('exceeded')}",
                    ]
                )
            )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(timing_lines)},
            }
        )
    if phase4_primary_warning:
        phase4_primary_lines = ["*phase4 primary warning*"]
        phase4_primary_lines.append(phase4_primary_warning)
        phase4_primary_lines.append(
            f"- reasons: {', '.join(phase4_primary_warning_reasons) or 'n/a'}"
        )
        if phase4_primary_module_warning_summary_text != "n/a":
            phase4_primary_lines.append(
                f"- module_warning_summary: {phase4_primary_module_warning_summary_text}"
            )
        if phase4_primary_module_hold_summary_text != "n/a":
            phase4_primary_lines.append(
                f"- module_hold_summary: {phase4_primary_module_hold_summary_text}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase4_primary_lines),
                },
            }
        )
    if phase4_secondary_warning:
        phase4_secondary_lines = ["*phase4 secondary warning*"]
        phase4_secondary_lines.append(phase4_secondary_warning)
        phase4_secondary_lines.append(
            f"- reasons: {', '.join(phase4_secondary_warning_reasons) or 'n/a'}"
        )
        if phase4_secondary_module_warning_summary_text != "n/a":
            phase4_secondary_lines.append(
                f"- module_warning_summary: {phase4_secondary_module_warning_summary_text}"
            )
        if phase4_secondary_module_hold_summary_text != "n/a":
            phase4_secondary_lines.append(
                f"- module_hold_summary: {phase4_secondary_module_hold_summary_text}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase4_secondary_lines),
                },
            }
        )
    if phase3_vehicle_dynamics_summary_text != "n/a":
        phase3_vehicle_dynamics_lines = ["*phase3 vehicle dynamics*"]
        phase3_vehicle_dynamics_lines.append(f"- summary: {phase3_vehicle_dynamics_summary_text}")
        if phase3_vehicle_dynamics_warning:
            phase3_vehicle_dynamics_lines.append(f"- warning: {phase3_vehicle_dynamics_warning}")
            phase3_vehicle_dynamics_lines.append(
                f"- reasons: {', '.join(phase3_vehicle_dynamics_warning_reasons) or 'n/a'}"
            )
        if phase3_vehicle_dynamics_violation_rows_text != "n/a":
            phase3_vehicle_dynamics_lines.append(
                f"- violations: {phase3_vehicle_dynamics_violation_rows_text}"
            )
        if phase3_vehicle_dynamics_violation_summary_text != "n/a":
            phase3_vehicle_dynamics_lines.append(
                f"- violation_summary: {phase3_vehicle_dynamics_violation_summary_text}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase3_vehicle_dynamics_lines),
                },
            }
        )
    if phase3_core_sim_summary_text != "n/a":
        phase3_core_sim_lines = ["*phase3 core sim*"]
        phase3_core_sim_lines.append(f"- summary: {phase3_core_sim_summary_text}")
        phase3_core_sim_lines.append(
            f"- threshold_drift_detected: {1 if phase3_core_sim_threshold_drift_detected else 0}"
        )
        phase3_core_sim_lines.append(
            f"- threshold_drift_severity: {phase3_core_sim_threshold_drift_severity}"
        )
        phase3_core_sim_lines.append(
            f"- threshold_drift: {phase3_core_sim_threshold_drift_summary_text}"
        )
        phase3_core_sim_lines.append(
            "- threshold_drift_reasons: "
            f"{', '.join(phase3_core_sim_threshold_drift_reasons) or 'n/a'}"
        )
        if phase3_core_sim_warning:
            phase3_core_sim_lines.append(f"- warning: {phase3_core_sim_warning}")
            phase3_core_sim_lines.append(
                f"- reasons: {', '.join(phase3_core_sim_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase3_core_sim_lines),
                },
            }
        )
    if phase3_core_sim_matrix_summary_text != "n/a":
        phase3_core_sim_matrix_lines = ["*phase3 core sim matrix*"]
        phase3_core_sim_matrix_lines.append(f"- summary: {phase3_core_sim_matrix_summary_text}")
        if phase3_core_sim_matrix_warning:
            phase3_core_sim_matrix_lines.append(f"- warning: {phase3_core_sim_matrix_warning}")
            phase3_core_sim_matrix_lines.append(
                f"- reasons: {', '.join(phase3_core_sim_matrix_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase3_core_sim_matrix_lines),
                },
            }
        )
    if phase3_lane_risk_summary_text != "n/a":
        phase3_lane_risk_lines = ["*phase3 lane risk*"]
        phase3_lane_risk_lines.append(f"- summary: {phase3_lane_risk_summary_text}")
        phase3_lane_risk_lines.append(
            f"- threshold_drift_detected: {1 if phase3_lane_risk_threshold_drift_detected else 0}"
        )
        phase3_lane_risk_lines.append(
            f"- threshold_drift_severity: {phase3_lane_risk_threshold_drift_severity}"
        )
        phase3_lane_risk_lines.append(
            f"- threshold_drift: {phase3_lane_risk_threshold_drift_summary_text}"
        )
        phase3_lane_risk_lines.append(
            "- threshold_drift_reasons: "
            f"{', '.join(phase3_lane_risk_threshold_drift_reasons) or 'n/a'}"
        )
        if phase3_lane_risk_warning:
            phase3_lane_risk_lines.append(f"- warning: {phase3_lane_risk_warning}")
            phase3_lane_risk_lines.append(
                f"- reasons: {', '.join(phase3_lane_risk_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase3_lane_risk_lines),
                },
            }
        )
    if phase3_dataset_traffic_summary_text != "n/a":
        phase3_dataset_traffic_lines = ["*phase3 dataset traffic*"]
        phase3_dataset_traffic_lines.append(f"- summary: {phase3_dataset_traffic_summary_text}")
        phase3_dataset_traffic_lines.append(
            f"- threshold_drift_detected: {1 if phase3_dataset_traffic_threshold_drift_detected else 0}"
        )
        phase3_dataset_traffic_lines.append(
            f"- threshold_drift_severity: {phase3_dataset_traffic_threshold_drift_severity}"
        )
        phase3_dataset_traffic_lines.append(
            f"- threshold_drift: {phase3_dataset_traffic_threshold_drift_summary_text}"
        )
        phase3_dataset_traffic_lines.append(
            "- threshold_drift_reasons: "
            f"{', '.join(phase3_dataset_traffic_threshold_drift_reasons) or 'n/a'}"
        )
        if phase3_dataset_traffic_warning:
            phase3_dataset_traffic_lines.append(f"- warning: {phase3_dataset_traffic_warning}")
            phase3_dataset_traffic_lines.append(
                f"- reasons: {', '.join(phase3_dataset_traffic_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase3_dataset_traffic_lines),
                },
            }
        )
    if phase2_log_replay_summary_text != "n/a":
        phase2_log_replay_lines = ["*phase2 log replay*"]
        phase2_log_replay_lines.append(f"- summary: {phase2_log_replay_summary_text}")
        if phase2_log_replay_warning:
            phase2_log_replay_lines.append(f"- warning: {phase2_log_replay_warning}")
            phase2_log_replay_lines.append(
                f"- reasons: {', '.join(phase2_log_replay_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase2_log_replay_lines),
                },
            }
        )
    if phase2_map_routing_summary_text != "n/a":
        phase2_map_routing_lines = ["*phase2 map routing*"]
        phase2_map_routing_lines.append(f"- summary: {phase2_map_routing_summary_text}")
        if phase2_map_routing_warning:
            phase2_map_routing_lines.append(f"- warning: {phase2_map_routing_warning}")
            phase2_map_routing_lines.append(
                f"- reasons: {', '.join(phase2_map_routing_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase2_map_routing_lines),
                },
            }
        )
    if phase2_sensor_fidelity_summary_text != "n/a":
        phase2_sensor_fidelity_lines = ["*phase2 sensor fidelity*"]
        phase2_sensor_fidelity_lines.append(f"- summary: {phase2_sensor_fidelity_summary_text}")
        if phase2_sensor_fidelity_warning:
            phase2_sensor_fidelity_lines.append(f"- warning: {phase2_sensor_fidelity_warning}")
            phase2_sensor_fidelity_lines.append(
                f"- reasons: {', '.join(phase2_sensor_fidelity_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(phase2_sensor_fidelity_lines),
                },
            }
        )
    if runtime_native_smoke_summary_text != "n/a":
        runtime_native_smoke_lines = ["*runtime native smoke*"]
        runtime_native_smoke_lines.append(f"- summary: {runtime_native_smoke_summary_text}")
        if runtime_native_smoke_warning:
            runtime_native_smoke_lines.append(f"- warning: {runtime_native_smoke_warning}")
            runtime_native_smoke_lines.append(
                f"- reasons: {', '.join(runtime_native_smoke_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(runtime_native_smoke_lines),
                },
            }
        )
    if runtime_native_summary_compare_summary_text != "n/a":
        runtime_native_summary_compare_lines = ["*runtime native summary compare*"]
        runtime_native_summary_compare_lines.append(
            f"- summary: {runtime_native_summary_compare_summary_text}"
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(runtime_native_summary_compare_lines),
                },
            }
        )
    if runtime_native_evidence_compare_summary_text != "n/a":
        runtime_native_evidence_compare_lines = ["*runtime native evidence compare*"]
        runtime_native_evidence_compare_lines.append(
            f"- summary: {runtime_native_evidence_compare_summary_text}"
        )
        if runtime_native_evidence_compare_interop_import_mode_diff_counts_text != "n/a":
            runtime_native_evidence_compare_lines.append(
                "- interop_import_mode_diff_counts: "
                f"{runtime_native_evidence_compare_interop_import_mode_diff_counts_text}"
            )
        if runtime_native_evidence_compare_warning:
            runtime_native_evidence_compare_lines.append(
                f"- warning: {runtime_native_evidence_compare_warning}"
            )
            runtime_native_evidence_compare_lines.append(
                "- reasons: "
                f"{', '.join(runtime_native_evidence_compare_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(runtime_native_evidence_compare_lines),
                },
            }
        )
    if runtime_evidence_summary_text != "n/a":
        runtime_evidence_lines = ["*runtime evidence*"]
        runtime_evidence_lines.append(f"- summary: {runtime_evidence_summary_text}")
        if runtime_evidence_probe_args_summary_text != "n/a":
            runtime_evidence_lines.append(f"- probe_args: {runtime_evidence_probe_args_summary_text}")
        if runtime_evidence_scenario_contract_summary_text != "n/a":
            runtime_evidence_lines.append(
                f"- scenario_contract: {runtime_evidence_scenario_contract_summary_text}"
            )
        if runtime_evidence_scene_result_summary_text != "n/a":
            runtime_evidence_lines.append(
                f"- scene_result: {runtime_evidence_scene_result_summary_text}"
            )
        if runtime_evidence_interop_contract_summary_text != "n/a":
            runtime_evidence_lines.append(
                f"- interop_contract: {runtime_evidence_interop_contract_summary_text}"
            )
        if runtime_evidence_interop_contract_warning:
            runtime_evidence_lines.append(
                f"- interop_contract_warning: {runtime_evidence_interop_contract_warning}"
            )
            runtime_evidence_lines.append(
                "- interop_contract_warning_reasons: "
                f"{', '.join(runtime_evidence_interop_contract_warning_reasons) or 'n/a'}"
            )
        if runtime_evidence_interop_export_summary_text != "n/a":
            runtime_evidence_lines.append(
                f"- interop_export: {runtime_evidence_interop_export_summary_text}"
            )
        if runtime_evidence_interop_import_summary_text != "n/a":
            runtime_evidence_lines.append(
                f"- interop_import: {runtime_evidence_interop_import_summary_text}"
            )
        if runtime_evidence_interop_import_modes_text != "n/a":
            runtime_evidence_lines.append(
                f"- interop_import_modes: {runtime_evidence_interop_import_modes_text}"
            )
        if runtime_evidence_interop_import_inconsistent_records_text != "n/a":
            runtime_evidence_lines.append(
                "- interop_import_inconsistent_records: "
                f"{runtime_evidence_interop_import_inconsistent_records_text}"
            )
        if runtime_evidence_warning:
            runtime_evidence_lines.append(f"- warning: {runtime_evidence_warning}")
            runtime_evidence_lines.append(
                f"- reasons: {', '.join(runtime_evidence_warning_reasons) or 'n/a'}"
            )
        if runtime_evidence_failed_records_text != "n/a":
            runtime_evidence_lines.append(
                f"- failed_records: {runtime_evidence_failed_records_text}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(runtime_evidence_lines),
                },
            }
        )
    if runtime_lane_execution_summary_text != "n/a":
        runtime_lane_execution_lines = ["*runtime lane execution*"]
        runtime_lane_execution_lines.append(f"- summary: {runtime_lane_execution_summary_text}")
        if runtime_lane_phase2_rig_sweep_radar_alignment_summary_text != "n/a":
            runtime_lane_execution_lines.append(
                "- radar_alignment_summary: "
                f"{runtime_lane_phase2_rig_sweep_radar_alignment_summary_text}"
            )
        runtime_lane_execution_lines.append(
            f"- threshold_drift_detected: {1 if runtime_threshold_drift_detected else 0}"
        )
        runtime_lane_execution_lines.append(f"- threshold_drift_severity: {runtime_threshold_drift_severity}")
        runtime_lane_execution_lines.append(f"- threshold_drift: {runtime_threshold_drift_summary_text}")
        runtime_lane_execution_lines.append(
            f"- threshold_drift_reasons: {', '.join(runtime_threshold_drift_reasons) or 'n/a'}"
        )
        runtime_lane_execution_lines.append(
            f"- threshold_drift_hold_detected: {1 if runtime_threshold_drift_hold_detected else 0}"
        )
        if runtime_lane_execution_warning:
            runtime_lane_execution_lines.append(f"- warning: {runtime_lane_execution_warning}")
            runtime_lane_execution_lines.append(
                f"- reasons: {', '.join(runtime_lane_execution_warning_reasons) or 'n/a'}"
            )
            if runtime_lane_execution_evidence_missing_runtimes_text != "n/a":
                runtime_lane_execution_lines.append(
                    f"- evidence_missing_runtimes: {runtime_lane_execution_evidence_missing_runtimes_text}"
                )
        if runtime_lane_phase2_rig_sweep_radar_alignment_warning:
            runtime_lane_execution_lines.append(
                "- radar_alignment_warning: "
                f"{runtime_lane_phase2_rig_sweep_radar_alignment_warning}"
            )
            runtime_lane_execution_lines.append(
                "- radar_alignment_warning_reasons: "
                f"{', '.join(runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons) or 'n/a'}"
            )
        if runtime_lane_execution_failed_rows_text != "n/a":
            runtime_lane_execution_lines.append(
                f"- failed_rows: {runtime_lane_execution_failed_rows_text}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(runtime_lane_execution_lines),
                },
            }
        )
    if threshold_drift_hold_policy_failure_detected or threshold_drift_hold_policy_failure_count > 0:
        threshold_drift_hold_policy_lines = ["*threshold drift hold policy*"]
        threshold_drift_hold_policy_lines.append(
            f"- detected: {1 if threshold_drift_hold_policy_failure_detected else 0}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- count: {threshold_drift_hold_policy_failure_count}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- summary: {threshold_drift_hold_policy_failure_summary_text}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- failures: {threshold_drift_hold_policy_failures_text}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- scope_counts: {threshold_drift_hold_policy_failure_scope_counts_text}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- scope_reason_key_counts: {threshold_drift_hold_policy_failure_scope_reason_key_counts_text}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- reason_keys: {threshold_drift_hold_policy_failure_reason_keys_text}"
        )
        threshold_drift_hold_policy_lines.append(
            f"- reason_key_counts: {threshold_drift_hold_policy_failure_reason_key_counts_text}"
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(threshold_drift_hold_policy_lines),
                },
            }
        )
    if runtime_evidence_compare_summary_text != "n/a":
        runtime_evidence_compare_lines = ["*runtime evidence compare*"]
        runtime_evidence_compare_lines.append(f"- summary: {runtime_evidence_compare_summary_text}")
        if runtime_evidence_compare_interop_import_mode_diff_counts_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_mode_diff_counts: "
                f"{runtime_evidence_compare_interop_import_mode_diff_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diffs_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diffs: "
                f"{runtime_evidence_compare_interop_import_profile_diffs_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_counts_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_counts: "
                f"{runtime_evidence_compare_interop_import_profile_diff_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_breakdown_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_breakdown: "
                f"{runtime_evidence_compare_interop_import_profile_diff_breakdown_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_deltas: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_deltas_by_label_pair: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_deltas_by_profile: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_deltas_by_label_pair_profile: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_directions: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_key_extremes: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_hotspots: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_hotspot_recommendations: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_hotspot_priority_counts: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_hotspot_action_counts: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text != "n/a":
            runtime_evidence_compare_lines.append(
                "- interop_import_profile_diff_numeric_delta_hotspot_reason_counts: "
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text}"
            )
        if runtime_evidence_compare_warning:
            runtime_evidence_compare_lines.append(f"- warning: {runtime_evidence_compare_warning}")
            runtime_evidence_compare_lines.append(
                f"- reasons: {', '.join(runtime_evidence_compare_warning_reasons) or 'n/a'}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(runtime_evidence_compare_lines),
                },
            }
        )
    if run_url:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{run_url}|Open CI run>"},
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "release_notification_v1",
        "generated_at": utc_now_iso(),
        "source_summary_json": str(summary_path),
        "status": status,
        "release_prefix": release_prefix,
        "summary_count": summary_count,
        "pipeline_manifest_count": pipeline_manifest_count,
        "sds_versions": sds_versions_list,
        "final_result_counts": final_counts,
        "pipeline_overall_counts": pipeline_overall_counts,
        "pipeline_trend_counts": pipeline_trend_counts,
        "timing_ms": timing_ms,
        "slowest_stages_ms": slowest_stages_ms,
        "timing_threshold": timing_threshold,
        "timing_total_warn_ms": timing_total_warn_ms,
        "timing_regression_baseline_ms": timing_regression_baseline_ms,
        "timing_regression_baseline_source": timing_regression_baseline_source,
        "timing_regression_warn_ratio": timing_regression_warn_ratio,
        "timing_regression_history_window": timing_regression_history_window,
        "timing_regression_history_outlier_method": timing_regression_history_outlier_method,
        "timing_regression_history_trim_ratio": timing_regression_history_trim_ratio,
        "timing_regression_history_filter": timing_regression_history_filter,
        "timing_regression_history_samples_raw_ms": timing_regression_history_samples_raw_ms,
        "timing_regression_history_samples_ms": timing_regression_history_samples_ms,
        "timing_regression": timing_regression,
        "timing_warning": timing_warning,
        "timing_warning_severity": timing_warning_severity,
        "timing_warning_reasons": timing_warning_reasons,
        "phase3_vehicle_final_speed_warn_max": phase3_vehicle_final_speed_warn_max,
        "phase3_vehicle_final_speed_hold_max": phase3_vehicle_final_speed_hold_max,
        "phase3_vehicle_final_position_warn_max": phase3_vehicle_final_position_warn_max,
        "phase3_vehicle_final_position_hold_max": phase3_vehicle_final_position_hold_max,
        "phase3_vehicle_delta_speed_warn_max": phase3_vehicle_delta_speed_warn_max,
        "phase3_vehicle_delta_speed_hold_max": phase3_vehicle_delta_speed_hold_max,
        "phase3_vehicle_delta_position_warn_max": phase3_vehicle_delta_position_warn_max,
        "phase3_vehicle_delta_position_hold_max": phase3_vehicle_delta_position_hold_max,
        "phase3_vehicle_final_heading_abs_warn_max": phase3_vehicle_final_heading_abs_warn_max,
        "phase3_vehicle_final_heading_abs_hold_max": phase3_vehicle_final_heading_abs_hold_max,
        "phase3_vehicle_final_lateral_position_abs_warn_max": phase3_vehicle_final_lateral_position_abs_warn_max,
        "phase3_vehicle_final_lateral_position_abs_hold_max": phase3_vehicle_final_lateral_position_abs_hold_max,
        "phase3_vehicle_delta_heading_abs_warn_max": phase3_vehicle_delta_heading_abs_warn_max,
        "phase3_vehicle_delta_heading_abs_hold_max": phase3_vehicle_delta_heading_abs_hold_max,
        "phase3_vehicle_delta_lateral_position_abs_warn_max": (
            phase3_vehicle_delta_lateral_position_abs_warn_max
        ),
        "phase3_vehicle_delta_lateral_position_abs_hold_max": (
            phase3_vehicle_delta_lateral_position_abs_hold_max
        ),
        "phase3_vehicle_yaw_rate_abs_warn_max": phase3_vehicle_yaw_rate_abs_warn_max,
        "phase3_vehicle_yaw_rate_abs_hold_max": phase3_vehicle_yaw_rate_abs_hold_max,
        "phase3_vehicle_delta_yaw_rate_abs_warn_max": phase3_vehicle_delta_yaw_rate_abs_warn_max,
        "phase3_vehicle_delta_yaw_rate_abs_hold_max": phase3_vehicle_delta_yaw_rate_abs_hold_max,
        "phase3_vehicle_lateral_velocity_abs_warn_max": phase3_vehicle_lateral_velocity_abs_warn_max,
        "phase3_vehicle_lateral_velocity_abs_hold_max": phase3_vehicle_lateral_velocity_abs_hold_max,
        "phase3_vehicle_accel_abs_warn_max": phase3_vehicle_accel_abs_warn_max,
        "phase3_vehicle_accel_abs_hold_max": phase3_vehicle_accel_abs_hold_max,
        "phase3_vehicle_lateral_accel_abs_warn_max": phase3_vehicle_lateral_accel_abs_warn_max,
        "phase3_vehicle_lateral_accel_abs_hold_max": phase3_vehicle_lateral_accel_abs_hold_max,
        "phase3_vehicle_yaw_accel_abs_warn_max": phase3_vehicle_yaw_accel_abs_warn_max,
        "phase3_vehicle_yaw_accel_abs_hold_max": phase3_vehicle_yaw_accel_abs_hold_max,
        "phase3_vehicle_jerk_abs_warn_max": phase3_vehicle_jerk_abs_warn_max,
        "phase3_vehicle_jerk_abs_hold_max": phase3_vehicle_jerk_abs_hold_max,
        "phase3_vehicle_lateral_jerk_abs_warn_max": phase3_vehicle_lateral_jerk_abs_warn_max,
        "phase3_vehicle_lateral_jerk_abs_hold_max": phase3_vehicle_lateral_jerk_abs_hold_max,
        "phase3_vehicle_yaw_jerk_abs_warn_max": phase3_vehicle_yaw_jerk_abs_warn_max,
        "phase3_vehicle_yaw_jerk_abs_hold_max": phase3_vehicle_yaw_jerk_abs_hold_max,
        "phase3_vehicle_lateral_position_abs_warn_max": phase3_vehicle_lateral_position_abs_warn_max,
        "phase3_vehicle_lateral_position_abs_hold_max": phase3_vehicle_lateral_position_abs_hold_max,
        "phase3_vehicle_road_grade_abs_warn_max": phase3_vehicle_road_grade_abs_warn_max,
        "phase3_vehicle_road_grade_abs_hold_max": phase3_vehicle_road_grade_abs_hold_max,
        "phase3_vehicle_grade_force_warn_max": phase3_vehicle_grade_force_warn_max,
        "phase3_vehicle_grade_force_hold_max": phase3_vehicle_grade_force_hold_max,
        "phase3_vehicle_control_overlap_ratio_warn_max": phase3_vehicle_control_overlap_ratio_warn_max,
        "phase3_vehicle_control_overlap_ratio_hold_max": phase3_vehicle_control_overlap_ratio_hold_max,
        "phase3_vehicle_control_steering_rate_warn_max": phase3_vehicle_control_steering_rate_warn_max,
        "phase3_vehicle_control_steering_rate_hold_max": phase3_vehicle_control_steering_rate_hold_max,
        "phase3_vehicle_control_throttle_plus_brake_warn_max": (
            phase3_vehicle_control_throttle_plus_brake_warn_max
        ),
        "phase3_vehicle_control_throttle_plus_brake_hold_max": (
            phase3_vehicle_control_throttle_plus_brake_hold_max
        ),
        "phase3_vehicle_speed_tracking_error_warn_max": phase3_vehicle_speed_tracking_error_warn_max,
        "phase3_vehicle_speed_tracking_error_hold_max": phase3_vehicle_speed_tracking_error_hold_max,
        "phase3_vehicle_speed_tracking_error_abs_warn_max": phase3_vehicle_speed_tracking_error_abs_warn_max,
        "phase3_vehicle_speed_tracking_error_abs_hold_max": phase3_vehicle_speed_tracking_error_abs_hold_max,
        "phase3_core_sim_min_ttc_same_lane_warn_min": phase3_core_sim_min_ttc_same_lane_warn_min,
        "phase3_core_sim_min_ttc_same_lane_hold_min": phase3_core_sim_min_ttc_same_lane_hold_min,
        "phase3_core_sim_min_ttc_any_lane_warn_min": phase3_core_sim_min_ttc_any_lane_warn_min,
        "phase3_core_sim_min_ttc_any_lane_hold_min": phase3_core_sim_min_ttc_any_lane_hold_min,
        "phase3_core_sim_collision_warn_max": phase3_core_sim_collision_warn_max,
        "phase3_core_sim_collision_hold_max": phase3_core_sim_collision_hold_max,
        "phase3_core_sim_timeout_warn_max": phase3_core_sim_timeout_warn_max,
        "phase3_core_sim_timeout_hold_max": phase3_core_sim_timeout_hold_max,
        "phase3_core_sim_gate_hold_warn_max": phase3_core_sim_gate_hold_warn_max,
        "phase3_core_sim_gate_hold_hold_max": phase3_core_sim_gate_hold_hold_max,
        "phase3_core_sim_matrix_min_ttc_same_lane_warn_min": (
            phase3_core_sim_matrix_min_ttc_same_lane_warn_min
        ),
        "phase3_core_sim_matrix_min_ttc_same_lane_hold_min": (
            phase3_core_sim_matrix_min_ttc_same_lane_hold_min
        ),
        "phase3_core_sim_matrix_min_ttc_any_lane_warn_min": (
            phase3_core_sim_matrix_min_ttc_any_lane_warn_min
        ),
        "phase3_core_sim_matrix_min_ttc_any_lane_hold_min": (
            phase3_core_sim_matrix_min_ttc_any_lane_hold_min
        ),
        "phase3_core_sim_matrix_failed_cases_warn_max": phase3_core_sim_matrix_failed_cases_warn_max,
        "phase3_core_sim_matrix_failed_cases_hold_max": phase3_core_sim_matrix_failed_cases_hold_max,
        "phase3_core_sim_matrix_collision_cases_warn_max": (
            phase3_core_sim_matrix_collision_cases_warn_max
        ),
        "phase3_core_sim_matrix_collision_cases_hold_max": (
            phase3_core_sim_matrix_collision_cases_hold_max
        ),
        "phase3_core_sim_matrix_timeout_cases_warn_max": phase3_core_sim_matrix_timeout_cases_warn_max,
        "phase3_core_sim_matrix_timeout_cases_hold_max": phase3_core_sim_matrix_timeout_cases_hold_max,
        "phase3_lane_risk_min_ttc_same_lane_warn_min": phase3_lane_risk_min_ttc_same_lane_warn_min,
        "phase3_lane_risk_min_ttc_same_lane_hold_min": phase3_lane_risk_min_ttc_same_lane_hold_min,
        "phase3_lane_risk_min_ttc_adjacent_lane_warn_min": phase3_lane_risk_min_ttc_adjacent_lane_warn_min,
        "phase3_lane_risk_min_ttc_adjacent_lane_hold_min": phase3_lane_risk_min_ttc_adjacent_lane_hold_min,
        "phase3_lane_risk_min_ttc_any_lane_warn_min": phase3_lane_risk_min_ttc_any_lane_warn_min,
        "phase3_lane_risk_min_ttc_any_lane_hold_min": phase3_lane_risk_min_ttc_any_lane_hold_min,
        "phase3_lane_risk_ttc_under_3s_same_lane_warn_max": phase3_lane_risk_ttc_under_3s_same_lane_warn_max,
        "phase3_lane_risk_ttc_under_3s_same_lane_hold_max": phase3_lane_risk_ttc_under_3s_same_lane_hold_max,
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max": (
            phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max
        ),
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max": (
            phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max
        ),
        "phase3_lane_risk_ttc_under_3s_any_lane_warn_max": phase3_lane_risk_ttc_under_3s_any_lane_warn_max,
        "phase3_lane_risk_ttc_under_3s_any_lane_hold_max": phase3_lane_risk_ttc_under_3s_any_lane_hold_max,
        "phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max": (
            phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max
        ),
        "phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max": (
            phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max
        ),
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max": (
            phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max
        ),
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max": (
            phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max
        ),
        "phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max": phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max,
        "phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max": phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max,
        "phase3_lane_risk_ttc_under_3s_same_lane_ratio": phase3_lane_risk_ttc_under_3s_same_lane_ratio,
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio": phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio,
        "phase3_lane_risk_ttc_under_3s_any_lane_ratio": phase3_lane_risk_ttc_under_3s_any_lane_ratio,
        "phase3_dataset_traffic_run_summary_warn_min": phase3_dataset_traffic_run_summary_warn_min,
        "phase3_dataset_traffic_run_summary_hold_min": phase3_dataset_traffic_run_summary_hold_min,
        "phase3_dataset_traffic_profile_count_warn_min": phase3_dataset_traffic_profile_count_warn_min,
        "phase3_dataset_traffic_profile_count_hold_min": phase3_dataset_traffic_profile_count_hold_min,
        "phase3_dataset_traffic_actor_pattern_count_warn_min": (
            phase3_dataset_traffic_actor_pattern_count_warn_min
        ),
        "phase3_dataset_traffic_actor_pattern_count_hold_min": (
            phase3_dataset_traffic_actor_pattern_count_hold_min
        ),
        "phase3_dataset_traffic_avg_npc_count_warn_min": phase3_dataset_traffic_avg_npc_count_warn_min,
        "phase3_dataset_traffic_avg_npc_count_hold_min": phase3_dataset_traffic_avg_npc_count_hold_min,
        "phase2_map_routing_unreachable_lanes_warn_max": phase2_map_routing_unreachable_lanes_warn_max,
        "phase2_map_routing_unreachable_lanes_hold_max": phase2_map_routing_unreachable_lanes_hold_max,
        "phase2_map_routing_non_reciprocal_links_warn_max": phase2_map_routing_non_reciprocal_links_warn_max,
        "phase2_map_routing_non_reciprocal_links_hold_max": phase2_map_routing_non_reciprocal_links_hold_max,
        "phase2_map_routing_continuity_gap_warn_max": phase2_map_routing_continuity_gap_warn_max,
        "phase2_map_routing_continuity_gap_hold_max": phase2_map_routing_continuity_gap_hold_max,
        "phase2_sensor_fidelity_score_avg_warn_min": phase2_sensor_fidelity_score_avg_warn_min,
        "phase2_sensor_fidelity_score_avg_hold_min": phase2_sensor_fidelity_score_avg_hold_min,
        "phase2_sensor_frame_count_avg_warn_min": phase2_sensor_frame_count_avg_warn_min,
        "phase2_sensor_frame_count_avg_hold_min": phase2_sensor_frame_count_avg_hold_min,
        "phase2_sensor_camera_noise_stddev_px_avg_warn_max": (
            phase2_sensor_camera_noise_stddev_px_avg_warn_max
        ),
        "phase2_sensor_camera_noise_stddev_px_avg_hold_max": (
            phase2_sensor_camera_noise_stddev_px_avg_hold_max
        ),
        "phase2_sensor_lidar_point_count_avg_warn_min": phase2_sensor_lidar_point_count_avg_warn_min,
        "phase2_sensor_lidar_point_count_avg_hold_min": phase2_sensor_lidar_point_count_avg_hold_min,
        "phase2_sensor_radar_false_positive_rate_avg_warn_max": (
            phase2_sensor_radar_false_positive_rate_avg_warn_max
        ),
        "phase2_sensor_radar_false_positive_rate_avg_hold_max": (
            phase2_sensor_radar_false_positive_rate_avg_hold_max
        ),
        "phase2_log_replay_fail_warn_max": phase2_log_replay_fail_warn_max,
        "phase2_log_replay_fail_hold_max": phase2_log_replay_fail_hold_max,
        "phase2_log_replay_missing_summary_warn_max": phase2_log_replay_missing_summary_warn_max,
        "phase2_log_replay_missing_summary_hold_max": phase2_log_replay_missing_summary_hold_max,
        "runtime_native_smoke_fail_warn_max": runtime_native_smoke_fail_warn_max,
        "runtime_native_smoke_fail_hold_max": runtime_native_smoke_fail_hold_max,
        "runtime_native_smoke_partial_warn_max": runtime_native_smoke_partial_warn_max,
        "runtime_native_smoke_partial_hold_max": runtime_native_smoke_partial_hold_max,
        "phase2_log_replay_summary": phase2_log_replay_summary,
        "phase2_log_replay_summary_text": phase2_log_replay_summary_text,
        "phase2_map_routing_summary": phase2_map_routing_summary,
        "phase2_map_routing_summary_text": phase2_map_routing_summary_text,
        "phase2_sensor_fidelity_summary": phase2_sensor_fidelity_summary,
        "phase2_sensor_fidelity_summary_text": phase2_sensor_fidelity_summary_text,
        "phase2_log_replay_warning": phase2_log_replay_warning,
        "phase2_log_replay_warning_messages": phase2_log_replay_warning_messages,
        "phase2_log_replay_warning_reasons": phase2_log_replay_warning_reasons,
        "phase2_map_routing_warning": phase2_map_routing_warning,
        "phase2_map_routing_warning_messages": phase2_map_routing_warning_messages,
        "phase2_map_routing_warning_reasons": phase2_map_routing_warning_reasons,
        "phase2_sensor_fidelity_warning": phase2_sensor_fidelity_warning,
        "phase2_sensor_fidelity_warning_messages": phase2_sensor_fidelity_warning_messages,
        "phase2_sensor_fidelity_warning_reasons": phase2_sensor_fidelity_warning_reasons,
        "phase3_vehicle_dynamics_summary": phase3_vehicle_dynamics_summary,
        "phase3_vehicle_dynamics_summary_text": phase3_vehicle_dynamics_summary_text,
        "phase3_core_sim_summary": phase3_core_sim_summary,
        "phase3_core_sim_summary_text": phase3_core_sim_summary_text,
        "phase3_core_sim_matrix_summary": phase3_core_sim_matrix_summary,
        "phase3_core_sim_matrix_summary_text": phase3_core_sim_matrix_summary_text,
        "phase3_core_sim_matrix_warning": phase3_core_sim_matrix_warning,
        "phase3_core_sim_matrix_warning_messages": phase3_core_sim_matrix_warning_messages,
        "phase3_core_sim_matrix_warning_reasons": phase3_core_sim_matrix_warning_reasons,
        "phase3_core_sim_gate_min_ttc_same_lane_sec_counts": (
            phase3_core_sim_gate_min_ttc_same_lane_sec_counts
        ),
        "phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text": (
            phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text
        ),
        "phase3_core_sim_gate_min_ttc_any_lane_sec_counts": (
            phase3_core_sim_gate_min_ttc_any_lane_sec_counts
        ),
        "phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text": (
            phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text
        ),
        "phase3_core_sim_warning": phase3_core_sim_warning,
        "phase3_core_sim_warning_messages": phase3_core_sim_warning_messages,
        "phase3_core_sim_warning_reasons": phase3_core_sim_warning_reasons,
        "phase3_core_sim_min_ttc_same_lane_warn_min_mismatch": (
            phase3_core_sim_min_ttc_same_lane_warn_min_mismatch
        ),
        "phase3_core_sim_min_ttc_same_lane_hold_min_mismatch": (
            phase3_core_sim_min_ttc_same_lane_hold_min_mismatch
        ),
        "phase3_core_sim_min_ttc_any_lane_warn_min_mismatch": (
            phase3_core_sim_min_ttc_any_lane_warn_min_mismatch
        ),
        "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch": (
            phase3_core_sim_min_ttc_any_lane_hold_min_mismatch
        ),
        "phase3_core_sim_threshold_drift_detected": phase3_core_sim_threshold_drift_detected,
        "phase3_core_sim_threshold_drift_severity": phase3_core_sim_threshold_drift_severity,
        "phase3_core_sim_threshold_drift_summary_text": (
            phase3_core_sim_threshold_drift_summary_text
        ),
        "phase3_core_sim_threshold_drift_reasons": phase3_core_sim_threshold_drift_reasons,
        "phase3_lane_risk_summary": phase3_lane_risk_summary,
        "phase3_lane_risk_summary_text": phase3_lane_risk_summary_text,
        "phase3_lane_risk_gate_min_ttc_same_lane_sec_counts": (
            phase3_lane_risk_gate_min_ttc_same_lane_sec_counts
        ),
        "phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text": (
            phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text
        ),
        "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts": (
            phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts
        ),
        "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text": (
            phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text
        ),
        "phase3_lane_risk_gate_min_ttc_any_lane_sec_counts": (
            phase3_lane_risk_gate_min_ttc_any_lane_sec_counts
        ),
        "phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text": (
            phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text
        ),
        "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts": (
            phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts
        ),
        "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text": (
            phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text
        ),
        "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts": (
            phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts
        ),
        "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text": (
            phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text
        ),
        "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts": (
            phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts
        ),
        "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text": (
            phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text
        ),
        "phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch": (
            phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch
        ),
        "phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch": (
            phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch
        ),
        "phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch": (
            phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch
        ),
        "phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch": (
            phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch
        ),
        "phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch": (
            phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch
        ),
        "phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch": (
            phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch
        ),
        "phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch": (
            phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch
        ),
        "phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch": (
            phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch
        ),
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch": (
            phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch
        ),
        "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch": (
            phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch
        ),
        "phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch": (
            phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch
        ),
        "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch": (
            phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch
        ),
        "phase3_lane_risk_threshold_drift_detected": phase3_lane_risk_threshold_drift_detected,
        "phase3_lane_risk_threshold_drift_severity": phase3_lane_risk_threshold_drift_severity,
        "phase3_lane_risk_threshold_drift_summary_text": (
            phase3_lane_risk_threshold_drift_summary_text
        ),
        "phase3_lane_risk_threshold_drift_reasons": phase3_lane_risk_threshold_drift_reasons,
        "phase3_dataset_traffic_summary": phase3_dataset_traffic_summary,
        "phase3_dataset_traffic_summary_text": phase3_dataset_traffic_summary_text,
        "phase3_dataset_traffic_gate_min_run_summary_count_counts": (
            phase3_dataset_traffic_gate_min_run_summary_count_counts
        ),
        "phase3_dataset_traffic_gate_min_run_summary_count_counts_text": (
            phase3_dataset_traffic_gate_min_run_summary_count_counts_text
        ),
        "phase3_dataset_traffic_gate_min_traffic_profile_count_counts": (
            phase3_dataset_traffic_gate_min_traffic_profile_count_counts
        ),
        "phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text": (
            phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text
        ),
        "phase3_dataset_traffic_gate_min_actor_pattern_count_counts": (
            phase3_dataset_traffic_gate_min_actor_pattern_count_counts
        ),
        "phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text": (
            phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text
        ),
        "phase3_dataset_traffic_gate_min_avg_npc_count_counts": (
            phase3_dataset_traffic_gate_min_avg_npc_count_counts
        ),
        "phase3_dataset_traffic_gate_min_avg_npc_count_counts_text": (
            phase3_dataset_traffic_gate_min_avg_npc_count_counts_text
        ),
        "phase3_dataset_traffic_warning": phase3_dataset_traffic_warning,
        "phase3_dataset_traffic_warning_messages": phase3_dataset_traffic_warning_messages,
        "phase3_dataset_traffic_warning_reasons": phase3_dataset_traffic_warning_reasons,
        "phase3_dataset_traffic_run_summary_warn_min_mismatch": (
            phase3_dataset_traffic_run_summary_warn_min_mismatch
        ),
        "phase3_dataset_traffic_run_summary_hold_min_mismatch": (
            phase3_dataset_traffic_run_summary_hold_min_mismatch
        ),
        "phase3_dataset_traffic_profile_count_warn_min_mismatch": (
            phase3_dataset_traffic_profile_count_warn_min_mismatch
        ),
        "phase3_dataset_traffic_profile_count_hold_min_mismatch": (
            phase3_dataset_traffic_profile_count_hold_min_mismatch
        ),
        "phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch": (
            phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch
        ),
        "phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch": (
            phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch
        ),
        "phase3_dataset_traffic_avg_npc_count_warn_min_mismatch": (
            phase3_dataset_traffic_avg_npc_count_warn_min_mismatch
        ),
        "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch": (
            phase3_dataset_traffic_avg_npc_count_hold_min_mismatch
        ),
        "phase3_dataset_traffic_threshold_drift_detected": phase3_dataset_traffic_threshold_drift_detected,
        "phase3_dataset_traffic_threshold_drift_severity": phase3_dataset_traffic_threshold_drift_severity,
        "phase3_dataset_traffic_threshold_drift_summary_text": (
            phase3_dataset_traffic_threshold_drift_summary_text
        ),
        "phase3_dataset_traffic_threshold_drift_reasons": phase3_dataset_traffic_threshold_drift_reasons,
        "phase3_lane_risk_warning": phase3_lane_risk_warning,
        "phase3_lane_risk_warning_messages": phase3_lane_risk_warning_messages,
        "phase3_lane_risk_warning_reasons": phase3_lane_risk_warning_reasons,
        "phase3_vehicle_dynamics_warning": phase3_vehicle_dynamics_warning,
        "phase3_vehicle_dynamics_warning_messages": phase3_vehicle_dynamics_warning_messages,
        "phase3_vehicle_dynamics_warning_reasons": phase3_vehicle_dynamics_warning_reasons,
        "phase3_vehicle_dynamics_violation_rows": phase3_vehicle_dynamics_violation_rows,
        "phase3_vehicle_dynamics_violation_rows_text": phase3_vehicle_dynamics_violation_rows_text,
        "phase3_vehicle_dynamics_violation_summary": phase3_vehicle_dynamics_violation_summary,
        "phase3_vehicle_dynamics_violation_summary_text": phase3_vehicle_dynamics_violation_summary_text,
        "runtime_native_smoke_summary": runtime_native_smoke_summary,
        "runtime_native_smoke_summary_text": runtime_native_smoke_summary_text,
        "runtime_native_smoke_warning": runtime_native_smoke_warning,
        "runtime_native_smoke_warning_messages": runtime_native_smoke_warning_messages,
        "runtime_native_smoke_warning_reasons": runtime_native_smoke_warning_reasons,
        "runtime_native_summary_compare_summary": runtime_native_summary_compare_summary,
        "runtime_native_summary_compare_summary_text": runtime_native_summary_compare_summary_text,
        "runtime_native_evidence_compare_summary": runtime_native_evidence_compare_summary,
        "runtime_native_evidence_compare_summary_text": runtime_native_evidence_compare_summary_text,
        "runtime_native_evidence_compare_interop_import_mode_diff_count_total": (
            runtime_native_evidence_compare_interop_import_mode_diff_count_total
        ),
        "runtime_native_evidence_compare_interop_import_mode_diff_counts_text": (
            runtime_native_evidence_compare_interop_import_mode_diff_counts_text
        ),
        "runtime_native_evidence_compare_warning": runtime_native_evidence_compare_warning,
        "runtime_native_evidence_compare_warning_messages": runtime_native_evidence_compare_warning_messages,
        "runtime_native_evidence_compare_warning_reasons": runtime_native_evidence_compare_warning_reasons,
        "runtime_evidence_summary": runtime_evidence_summary,
        "runtime_evidence_summary_text": runtime_evidence_summary_text,
        "runtime_evidence_probe_args_summary_text": runtime_evidence_probe_args_summary_text,
        "runtime_evidence_scenario_contract_summary_text": runtime_evidence_scenario_contract_summary_text,
        "runtime_evidence_scene_result_summary_text": runtime_evidence_scene_result_summary_text,
        "runtime_evidence_interop_contract_summary_text": runtime_evidence_interop_contract_summary_text,
        "runtime_evidence_interop_contract_warning": runtime_evidence_interop_contract_warning,
        "runtime_evidence_interop_contract_warning_messages": runtime_evidence_interop_contract_warning_messages,
        "runtime_evidence_interop_contract_warning_reasons": runtime_evidence_interop_contract_warning_reasons,
        "runtime_evidence_interop_export_summary_text": runtime_evidence_interop_export_summary_text,
        "runtime_evidence_interop_import_summary_text": runtime_evidence_interop_import_summary_text,
        "runtime_evidence_interop_import_modes_text": runtime_evidence_interop_import_modes_text,
        "runtime_evidence_interop_import_inconsistent_records": (
            runtime_evidence_interop_import_inconsistent_records
        ),
        "runtime_evidence_interop_import_inconsistent_records_text": (
            runtime_evidence_interop_import_inconsistent_records_text
        ),
        "runtime_evidence_warning": runtime_evidence_warning,
        "runtime_evidence_warning_messages": runtime_evidence_warning_messages,
        "runtime_evidence_warning_reasons": runtime_evidence_warning_reasons,
        "runtime_evidence_failed_records": runtime_evidence_failed_records,
        "runtime_evidence_failed_records_text": runtime_evidence_failed_records_text,
        "runtime_lane_execution_summary": runtime_lane_execution_summary,
        "runtime_lane_execution_summary_text": runtime_lane_execution_summary_text,
        "runtime_lane_phase2_rig_sweep_radar_alignment_summary": (
            runtime_lane_phase2_rig_sweep_radar_alignment_summary
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_summary_text": (
            runtime_lane_phase2_rig_sweep_radar_alignment_summary_text
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_runtime_counts": (
            runtime_lane_phase2_rig_sweep_radar_alignment_runtime_counts
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_result_counts": (
            runtime_lane_phase2_rig_sweep_radar_alignment_result_counts
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_mapping_mode_counts": (
            runtime_lane_phase2_rig_sweep_radar_alignment_mapping_mode_counts
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary": (
            runtime_lane_phase2_rig_sweep_radar_alignment_pass_metric_summary
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary": (
            runtime_lane_phase2_rig_sweep_radar_alignment_fail_metric_summary
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta": (
            runtime_lane_phase2_rig_sweep_radar_alignment_pass_minus_fail_metric_delta
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_row_count": (
            runtime_lane_phase2_rig_sweep_radar_alignment_row_count
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_metrics_sample_count": (
            runtime_lane_phase2_rig_sweep_radar_alignment_metrics_sample_count
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_row_count": (
            runtime_lane_phase2_rig_sweep_radar_alignment_unmatched_row_count
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_pass_metrics_sample_count": (
            runtime_lane_phase2_rig_sweep_radar_alignment_pass_metrics_sample_count
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count": (
            runtime_lane_phase2_rig_sweep_radar_alignment_fail_metrics_sample_count
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_warning": (
            runtime_lane_phase2_rig_sweep_radar_alignment_warning
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages": (
            runtime_lane_phase2_rig_sweep_radar_alignment_warning_messages
        ),
        "runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons": (
            runtime_lane_phase2_rig_sweep_radar_alignment_warning_reasons
        ),
        "runtime_lane_execution_warning": runtime_lane_execution_warning,
        "runtime_lane_execution_warning_messages": runtime_lane_execution_warning_messages,
        "runtime_lane_execution_warning_reasons": runtime_lane_execution_warning_reasons,
        "runtime_lane_execution_failed_rows": runtime_lane_execution_failed_rows,
        "runtime_lane_execution_failed_rows_text": runtime_lane_execution_failed_rows_text,
        "runtime_lane_execution_exec_lane_row_count": runtime_lane_execution_exec_lane_row_count,
        "runtime_lane_execution_warn_min_exec_rows": runtime_lane_execution_warn_min_exec_rows,
        "runtime_lane_execution_hold_min_exec_rows": runtime_lane_execution_hold_min_exec_rows,
        "runtime_lane_execution_exec_lane_warn_min_rows_mismatch": (
            runtime_lane_execution_exec_lane_warn_min_rows_mismatch
        ),
        "runtime_lane_execution_exec_lane_hold_min_rows_mismatch": (
            runtime_lane_execution_exec_lane_hold_min_rows_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch": (
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch": (
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch": (
            runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch": (
            runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch": (
            runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch": (
            runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch": (
            runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch": (
            runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch": (
            runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch
        ),
        "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch": (
            runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch
        ),
        "runtime_lane_execution_lane_row_counts": runtime_lane_execution_lane_row_counts,
        "runtime_lane_execution_lane_row_counts_text": runtime_lane_execution_lane_row_counts_text,
        "runtime_lane_execution_runner_platform_counts": runtime_lane_execution_runner_platform_counts,
        "runtime_lane_execution_runner_platform_counts_text": runtime_lane_execution_runner_platform_counts_text,
        "runtime_lane_execution_sim_runtime_input_counts": runtime_lane_execution_sim_runtime_input_counts,
        "runtime_lane_execution_sim_runtime_input_counts_text": runtime_lane_execution_sim_runtime_input_counts_text,
        "runtime_lane_execution_dry_run_counts": runtime_lane_execution_dry_run_counts,
        "runtime_lane_execution_dry_run_counts_text": runtime_lane_execution_dry_run_counts_text,
        "runtime_lane_execution_continue_on_runtime_failure_counts": (
            runtime_lane_execution_continue_on_runtime_failure_counts
        ),
        "runtime_lane_execution_continue_on_runtime_failure_counts_text": (
            runtime_lane_execution_continue_on_runtime_failure_counts_text
        ),
        "runtime_lane_execution_exec_lane_warn_min_rows_counts": (
            runtime_lane_execution_exec_lane_warn_min_rows_counts
        ),
        "runtime_lane_execution_exec_lane_warn_min_rows_counts_text": (
            runtime_lane_execution_exec_lane_warn_min_rows_counts_text
        ),
        "runtime_lane_execution_exec_lane_hold_min_rows_counts": (
            runtime_lane_execution_exec_lane_hold_min_rows_counts
        ),
        "runtime_lane_execution_exec_lane_hold_min_rows_counts_text": (
            runtime_lane_execution_exec_lane_hold_min_rows_counts_text
        ),
        "runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts": (
            runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts
        ),
        "runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_text": (
            runtime_lane_execution_runtime_compare_warn_min_artifacts_with_diffs_counts_text
        ),
        "runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts": (
            runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts
        ),
        "runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_text": (
            runtime_lane_execution_runtime_compare_hold_min_artifacts_with_diffs_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts": (
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts
        ),
        "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text": (
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts": (
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts
        ),
        "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text": (
            runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts": (
            runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts
        ),
        "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text": (
            runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts": (
            runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts
        ),
        "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text": (
            runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts": (
            runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts
        ),
        "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text": (
            runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts": (
            runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts
        ),
        "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text": (
            runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts": (
            runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts
        ),
        "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text": (
            runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts": (
            runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts
        ),
        "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text": (
            runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts": (
            runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts
        ),
        "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text": (
            runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text
        ),
        "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts": (
            runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts
        ),
        "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text": (
            runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text
        ),
        "runtime_lane_execution_evidence_missing_runtime_counts": runtime_lane_execution_evidence_missing_runtime_counts,
        "runtime_lane_execution_evidence_missing_runtimes_text": runtime_lane_execution_evidence_missing_runtimes_text,
        "runtime_evidence_interop_contract_checked_warn_min": runtime_evidence_interop_contract_checked_warn_min,
        "runtime_evidence_interop_contract_checked_hold_min": runtime_evidence_interop_contract_checked_hold_min,
        "runtime_evidence_interop_contract_fail_warn_max": runtime_evidence_interop_contract_fail_warn_max,
        "runtime_evidence_interop_contract_fail_hold_max": runtime_evidence_interop_contract_fail_hold_max,
        "runtime_evidence_compare_warn_min_artifacts_with_diffs": (
            runtime_evidence_compare_warn_min_artifacts_with_diffs
        ),
        "runtime_evidence_compare_hold_min_artifacts_with_diffs": (
            runtime_evidence_compare_hold_min_artifacts_with_diffs
        ),
        "runtime_evidence_compare_warn_min_interop_import_mode_diff_count": (
            runtime_evidence_compare_warn_min_interop_import_mode_diff_count
        ),
        "runtime_evidence_compare_hold_min_interop_import_mode_diff_count": (
            runtime_evidence_compare_hold_min_interop_import_mode_diff_count
        ),
        "runtime_evidence_compare_summary": runtime_evidence_compare_summary,
        "runtime_evidence_compare_summary_text": runtime_evidence_compare_summary_text,
        "runtime_evidence_compare_interop_import_mode_diff_count_total": (
            runtime_evidence_compare_interop_import_mode_diff_count_total
        ),
        "runtime_evidence_compare_interop_import_mode_diff_counts_text": (
            runtime_evidence_compare_interop_import_mode_diff_counts_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_field_counts": (
            runtime_evidence_compare_interop_import_profile_diff_field_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_counts_text": (
            runtime_evidence_compare_interop_import_profile_diff_counts_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_label_pair_counts": (
            runtime_evidence_compare_interop_import_profile_diff_label_pair_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_profile_counts": (
            runtime_evidence_compare_interop_import_profile_diff_profile_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_breakdown_text": (
            runtime_evidence_compare_interop_import_profile_diff_breakdown_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_record_count": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_record_count
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text
        ),
        "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text": (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text
        ),
        "runtime_evidence_compare_interop_import_profile_diffs": (
            runtime_evidence_compare_interop_import_profile_diffs
        ),
        "runtime_evidence_compare_interop_import_profile_diffs_text": (
            runtime_evidence_compare_interop_import_profile_diffs_text
        ),
        "runtime_evidence_compare_warning": runtime_evidence_compare_warning,
        "runtime_evidence_compare_warning_messages": runtime_evidence_compare_warning_messages,
        "runtime_evidence_compare_warning_reasons": runtime_evidence_compare_warning_reasons,
        "runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch": (
            runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch
        ),
        "runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch": (
            runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch
        ),
        "runtime_threshold_drift_detected": runtime_threshold_drift_detected,
        "runtime_threshold_drift_severity": runtime_threshold_drift_severity,
        "runtime_threshold_drift_summary_text": runtime_threshold_drift_summary_text,
        "runtime_threshold_drift_reasons": runtime_threshold_drift_reasons,
        "runtime_threshold_drift_hold_detected": runtime_threshold_drift_hold_detected,
        "threshold_drift_hold_policy_failure_detected": (
            threshold_drift_hold_policy_failure_detected
        ),
        "threshold_drift_hold_policy_failure_count": threshold_drift_hold_policy_failure_count,
        "threshold_drift_hold_policy_failure_summary_text": (
            threshold_drift_hold_policy_failure_summary_text
        ),
        "threshold_drift_hold_policy_failures": threshold_drift_hold_policy_failures,
        "phase4_primary_warn_ratio": phase4_primary_warn_ratio,
        "phase4_primary_hold_ratio": phase4_primary_hold_ratio,
        "phase4_primary_module_warn_thresholds": phase4_primary_module_warn_thresholds,
        "phase4_primary_module_hold_thresholds": phase4_primary_module_hold_thresholds,
        "phase4_primary_warning": phase4_primary_warning,
        "phase4_primary_warning_messages": phase4_primary_warning_messages,
        "phase4_primary_warning_reasons": phase4_primary_warning_reasons,
        "phase4_primary_coverage_rows": phase4_primary_coverage_rows,
        "phase4_primary_module_warning_rows": phase4_primary_module_warning_rows,
        "phase4_primary_module_warning_summary": phase4_primary_module_warning_summary,
        "phase4_primary_module_hold_rows": phase4_primary_module_hold_rows,
        "phase4_primary_module_hold_summary": phase4_primary_module_hold_summary,
        "phase4_secondary_warn_ratio": phase4_secondary_warn_ratio,
        "phase4_secondary_hold_ratio": phase4_secondary_hold_ratio,
        "phase4_secondary_warn_min_modules": phase4_secondary_warn_min_modules,
        "phase4_secondary_module_warn_thresholds": phase4_secondary_module_warn_thresholds,
        "phase4_secondary_module_hold_thresholds": phase4_secondary_module_hold_thresholds,
        "phase4_secondary_warning": phase4_secondary_warning,
        "phase4_secondary_warning_messages": phase4_secondary_warning_messages,
        "phase4_secondary_warning_reasons": phase4_secondary_warning_reasons,
        "phase4_secondary_coverage_rows": phase4_secondary_coverage_rows,
        "phase4_secondary_module_warning_rows": phase4_secondary_module_warning_rows,
        "phase4_secondary_module_warning_summary": phase4_secondary_module_warning_summary,
        "phase4_secondary_module_hold_rows": phase4_secondary_module_hold_rows,
        "phase4_secondary_module_hold_summary": phase4_secondary_module_hold_summary,
        "reason_code_diff": reason_code_diff,
        "warning": warning,
        "workflow_name": workflow_name,
        "run_url": run_url,
        "message_text": text_body,
        "slack": {
            "text": text_body,
            "blocks": blocks,
        },
    }
    if (
        threshold_drift_hold_policy_failure_detected
        or threshold_drift_hold_policy_failure_count > 0
        or threshold_drift_hold_policy_failures
        or threshold_drift_hold_policy_failure_scope_counts
        or threshold_drift_hold_policy_failure_scope_reason_key_counts
        or threshold_drift_hold_policy_failure_reason_keys
        or threshold_drift_hold_policy_failure_reason_key_counts
    ):
        payload["threshold_drift_hold_policy_failure_scope_counts"] = (
            threshold_drift_hold_policy_failure_scope_counts
        )
        payload["threshold_drift_hold_policy_failure_scope_reason_key_counts"] = (
            threshold_drift_hold_policy_failure_scope_reason_key_counts
        )
        payload["threshold_drift_hold_policy_failure_reason_keys"] = (
            threshold_drift_hold_policy_failure_reason_keys
        )
        payload["threshold_drift_hold_policy_failure_reason_key_counts"] = (
            threshold_drift_hold_policy_failure_reason_key_counts
        )

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"[ok] out={out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="build_release_notification_payload.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD,
        )
    )
