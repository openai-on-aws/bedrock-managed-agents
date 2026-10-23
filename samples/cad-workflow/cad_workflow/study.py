"""Pure validation and comparison logic for a persistent BMA design study."""

import json
import math

from cad_workflow.contract import digest, job_id, request
from cad_workflow.example import validate_document


def load_brief(path):
    brief = json.loads(path.read_text())
    validate_document(brief, "design-brief.schema.json")
    request({"job_id": "0" * 32, **brief["baseline"]})
    # Infeasible engineering requirements are allowed: the agent must surface
    # the conflict rather than silently loosen it.
    return brief


def message(text):
    return {"type": "agent.session.input.message",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}


def text_content(item):
    return "\n".join(part.get("text", "") for part in item.get("content", [])
                     if part.get("type") in ("input_text", "output_text"))


def round_items(items, round_id, expected_input_sha256=None):
    """Bind results to the turn carrying this round's exact input marker."""
    marker = f"CAD_STUDY_ROUND={round_id}\n"
    inputs = [item for item in items
              if item.get("type") == "message" and item.get("role") == "user"
              and text_content(item).startswith(marker)]
    if len(inputs) != 1 or not inputs[0].get("turn_id"):
        raise ValueError("Round input is not yet uniquely present in durable session items")
    if expected_input_sha256 and digest(text_content(inputs[0]).encode()) != expected_input_sha256:
        raise ValueError("Durable round input differs from the submitted input hash")
    turn_id = inputs[0]["turn_id"]
    return [item for item in items if item.get("turn_id") == turn_id]


def proposals(items, allocated, known=None, minimum_new=1):
    """Validate the agent's actual tool arguments, not a narrated candidate list."""
    known = known or {}
    created, retrieved = {}, set()
    for item in items:
        if item.get("type") != "mcp_call":
            continue
        if item.get("server_label") != "cad":
            raise ValueError("Unexpected MCP server in design study")
        name = item["name"].rsplit("___", 1)[-1]
        if name not in ("create_support", "get_result"):
            raise ValueError("Unexpected CAD tool")
        if item.get("status") != "completed" or item.get("error"):
            raise ValueError("An MCP call did not complete successfully")
        arguments = item["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        identifier = job_id(arguments["job_id"])
        if identifier not in allocated and identifier not in known:
            raise ValueError("Agent used a job ID outside the round's allocation")
        if name == "create_support":
            if identifier in known:
                raise ValueError("Reuse an existing candidate with get_result, not a new create call")
            parameters = request(arguments)
            if identifier in created and created[identifier] != parameters:
                raise ValueError("Agent changed an immutable candidate's parameters")
            created[identifier] = parameters
        else:
            if set(arguments) != {"job_id"}:
                raise ValueError("Unexpected get_result arguments")
            retrieved.add(identifier)
    if len(created) < minimum_new or not set(created) <= retrieved:
        raise ValueError("New candidates must be created and retrieved; the initial study needs a comparison")
    pairs = {(p["stock_thickness_mm"], p["pocket_depth_mm"]) for p in created.values()}
    if len(pairs) != len(created):
        raise ValueError("The study must compare distinct parameter pairs")
    reused = {identifier: known[identifier]["parameters"] for identifier in retrieved if identifier in known}
    if not created and not reused:
        raise ValueError("Inspect at least one new or previously verified native candidate")
    return {**created, **reused}


def recommendation(items):
    answers = [item for item in items
               if item.get("type") == "message" and item.get("role") == "assistant"
               and item.get("status") == "completed" and item.get("phase") == "final_answer"]
    if len(answers) != 1:
        raise ValueError("Expected exactly one completed final recommendation for this turn")
    value = json.loads(text_content(answers[0]))
    if not isinstance(value, dict) or set(value) != {
        "recommended_job_id", "rationale", "requires_engineer_review"
    }:
        raise ValueError("Recommendation does not follow the JSON contract")
    if value["requires_engineer_review"] is not True or not isinstance(value["rationale"], str):
        raise ValueError("Recommendation must explain its reasoning and require review")
    if not value["rationale"].strip():
        raise ValueError("Recommendation rationale is empty")
    if value["recommended_job_id"] is not None:
        job_id(value["recommended_job_id"])
    return value


def compare(brief, native_results, baseline_volume):
    if not math.isfinite(baseline_volume) or baseline_volume <= 0:
        raise ValueError("Measured baseline volume must be finite and positive")
    rows = []
    requirements = brief["requirements"]
    for native in native_results:
        parameters = native["parameters"]
        row = {"job_id": parameters["job_id"], "parameters": parameters,
               "native_accepted": native["accepted"], "volume_mm3": None,
               "floor_mm": None, "volume_reduction_percent": None, "meets_brief": False}
        if native["accepted"]:
            checks = {item["name"]: item for item in native["checks"]["checks"]}
            volume = checks["analytic_volume"]["measured_mm3"]
            floor = checks["residual_floor"]["measured_mm"]
            if not all(math.isfinite(value) for value in (volume, floor)) or volume <= 0:
                raise ValueError("Native measurements must be finite and volume positive")
            reduction = 100 * (baseline_volume - volume) / baseline_volume
            row.update(
                volume_mm3=volume, floor_mm=floor, volume_reduction_percent=reduction,
                meets_brief=(
                    parameters["stock_thickness_mm"] in requirements["available_stock_mm"]
                    and floor + 1e-7 >= requirements["minimum_floor_mm"]
                    and reduction + 1e-7 >= requirements["minimum_volume_reduction_percent"]
                ),
            )
        rows.append(row)
    return rows


def validate_selection(rows, selected):
    feasible = [row for row in rows if row["native_accepted"] and row["meets_brief"]]
    if not feasible:
        if selected is not None:
            raise ValueError("Agent recommended a candidate that does not meet the brief")
        return False
    if selected is None:
        raise ValueError("Agent claimed no solution despite a measured compliant candidate")
    candidate = next((row for row in feasible if row["job_id"] == selected), None)
    if candidate is None:
        raise ValueError("Recommendation is not a verified candidate meeting this brief")
    if candidate["volume_mm3"] > min(row["volume_mm3"] for row in feasible) + 0.001:
        raise ValueError("Recommendation is not the lowest-volume compliant tested candidate")
    return True
