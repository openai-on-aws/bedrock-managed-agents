"""The frozen dimensional contract; no cloud or CAD dependencies."""

import hashlib
import json
import math
import re

SCHEMA = "bma-cad/support/v1"
LENGTH_MM = 160.0
WIDTH_MM = 42.0
BORES_MM = ((14.0, 10.0), (14.0, 32.0), (148.0, 21.0))
BORE_DIAMETER_MM = 6.5
MINIMUM_FLOOR_MM = 2.0
MAX_FILE_BYTES = 20 * 1024 * 1024
REQUIRED_CHECKS = frozenset({
    "native_solid", "native_features", "constrained_sketches", "blind_pocket",
    "stock_thickness", "units_and_envelope", "material_and_mounting_interfaces",
    "analytic_volume", "residual_floor", "step_roundtrip", "native_parameter_roundtrip",
})


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("job_id must be a lowercase UUID hex string (32 characters)")
    return value


def number(value, name, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum} mm")
    return float(value)


def request(value):
    if not isinstance(value, dict) or set(value) != {
        "job_id", "stock_thickness_mm", "pocket_depth_mm"
    }:
        raise ValueError("Supply only job_id, stock_thickness_mm and pocket_depth_mm")
    thickness = number(value["stock_thickness_mm"], "stock_thickness_mm", 10, 20)
    depth = number(value["pocket_depth_mm"], "pocket_depth_mm", 1, thickness - MINIMUM_FLOOR_MM)
    return {
        "job_id": job_id(value["job_id"]),
        "stock_thickness_mm": thickness,
        "pocket_depth_mm": depth,
    }


def expected_volume(parameters):
    thickness, depth = parameters["stock_thickness_mm"], parameters["pocket_depth_mm"]
    bore_area = len(BORES_MM) * math.pi * (BORE_DIAMETER_MM / 2) ** 2
    # Capsule x=44..134, y=10..32: 68 mm straight section and R11 ends.
    pocket_area = 68 * 22 + math.pi * 11 ** 2
    return (LENGTH_MM * WIDTH_MM - bore_area) * thickness - pocket_area * depth


def require_verified_report(report, backend, input_sha256):
    """Acceptance requires every named check against these exact candidate bytes."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("Unknown native check report schema")
    if report.get("backend") != backend or report.get("accepted") is not True:
        raise ValueError("Native checker did not accept this backend's candidate")
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(REQUIRED_CHECKS):
        raise ValueError("Native report is missing required checks")
    if any(not isinstance(check, dict) or check.get("passed") is not True for check in checks):
        raise ValueError("Not every native check passed")
    if {check.get("name") for check in checks} != REQUIRED_CHECKS:
        raise ValueError("Native report contains unknown, duplicate or missing checks")
    if report.get("input_sha256") != input_sha256:
        raise ValueError("Native report does not describe the frozen candidate bytes")
