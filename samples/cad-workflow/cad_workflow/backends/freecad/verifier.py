"""Independent fresh-process checks against frozen FCStd/STEP bytes."""

import json
import os
from pathlib import Path
import traceback

from cad_workflow.contract import BORES_MM, SCHEMA, digest, expected_volume, request


def reference_shape(parameters):
    """Construct the specified material using primitives, not the author's sketches."""
    import FreeCAD as App
    import Part

    thickness = parameters["stock_thickness_mm"]
    depth = parameters["pocket_depth_mm"]
    shape = Part.makeBox(160, 42, thickness)
    for x, y in BORES_MM:
        shape = shape.cut(Part.makeCylinder(3.25, thickness + 2, App.Vector(x, y, -1)))
    floor = thickness - depth
    slot = Part.makeBox(68, 22, depth, App.Vector(55, 10, floor))
    for x in (55, 123):
        slot = slot.fuse(Part.makeCylinder(11, depth, App.Vector(x, 21, floor)))
    return shape.cut(slot).removeSplitter()


def verify(parameters, source):
    import FreeCAD as App
    import Part

    checks = []

    def check(name, passed, **measurements):
        checks.append({"name": name, "passed": bool(passed), **measurements})

    document = App.openDocument(str(source / "support.FCStd"))
    document.recompute()
    body = document.getObject("Support")
    pad = document.getObject("StockPad")
    pocket = document.getObject("LighteningPocket")
    if not all((body, pad, pocket)):
        raise ValueError("Required native Body/Pad/Pocket feature history is missing")
    thickness, depth = parameters["stock_thickness_mm"], parameters["pocket_depth_mm"]
    shape = body.Shape.copy()
    check("native_solid", shape.isValid() and len(shape.Solids) == 1, solids=len(shape.Solids))
    check("native_features", pad.TypeId == "PartDesign::Pad" and pocket.TypeId == "PartDesign::Pocket")
    sketches = [document.getObject(name) for name in ("StockSketch", "BoreSketch", "PocketSketch")]
    check("constrained_sketches", all(sketch and sketch.FullyConstrained for sketch in sketches))
    check("blind_pocket", str(pocket.Type) == pocket.getEnumerationsOfProperty("Type")[0]
          and not pocket.Reversed and abs(pocket.Length.Value - depth) < 1e-7,
          type=str(pocket.Type), depth_mm=pocket.Length.Value)
    check("stock_thickness", abs(pad.Length.Value - thickness) < 1e-7, measured_mm=pad.Length.Value)
    bounds = shape.BoundBox
    observed = [bounds.XMin, bounds.YMin, bounds.ZMin, bounds.XLength, bounds.YLength, bounds.ZLength]
    expected = [0, 0, 0, 160, 42, thickness]
    check("units_and_envelope", all(abs(a - b) < 1e-5 for a, b in zip(observed, expected)),
          units="mm", bounds=observed)
    desired = reference_shape(parameters)
    added = shape.cut(desired).Volume
    missing = desired.cut(shape).Volume
    check("material_and_mounting_interfaces", added + missing < 0.001,
          added_mm3=added, missing_mm3=missing)
    check("analytic_volume", abs(shape.Volume - expected_volume(parameters)) < 0.001,
          measured_mm3=shape.Volume, expected_mm3=expected_volume(parameters))
    floor = thickness - depth
    material_column = shape.common(Part.makeLine(App.Vector(89, 21, -1), App.Vector(89, 21, thickness + 1)))
    # An empty intersection has an unbounded OCCT bounding box. Its length
    # can be -inf; the physical material thickness is zero.
    measured_floor = 0.0 if material_column.isNull() or not material_column.Edges else material_column.BoundBox.ZLength
    floor_ok = shape.isInside(App.Vector(89, 21, floor / 2), 1e-7, True)
    cavity_ok = not shape.isInside(App.Vector(89, 21, floor + depth / 2), 1e-7, True)
    check("residual_floor", measured_floor >= 2 - 1e-7 and abs(measured_floor - floor) < 1e-7
          and floor_ok and cavity_ok, minimum_mm=2, measured_mm=measured_floor)

    exported = Part.read(str(source / "support.step"))
    difference = exported.cut(shape).Volume + shape.cut(exported).Volume
    check("step_roundtrip", exported.isValid() and len(exported.Solids) == 1 and difference < 0.001,
          difference_mm3=difference, solids=len(exported.Solids))
    pad.Length = thickness + 1
    document.recompute()
    changed_reference = reference_shape({**parameters, "stock_thickness_mm": thickness + 1})
    changed = body.Shape
    changed_difference = changed.cut(changed_reference).Volume + changed_reference.cut(changed).Volume
    changed_ok = changed.isValid() and len(changed.Solids) == 1 and changed_difference < 0.001
    pad.Length = thickness
    document.recompute()
    restored = body.Shape
    restored_difference = restored.cut(shape).Volume + shape.cut(restored).Volume
    check("native_parameter_roundtrip", changed_ok and restored.isValid() and restored_difference < 0.001,
          tested_thickness_mm=[thickness, thickness + 1, thickness],
          changed_difference_mm3=changed_difference, restored_difference_mm3=restored_difference)
    App.closeDocument(document.Name)
    return {
        "schema": SCHEMA,
        "backend": "freecad",
        "accepted": all(item["passed"] for item in checks),
        "checks": checks,
        "freecad": App.Version(),
        "occt": Part.OCC_VERSION,
        "input_sha256": {name: digest((source / name).read_bytes()) for name in ("support.FCStd", "support.step")},
    }


def main():
    destination = Path(os.environ["CAD_OUTPUT"])
    status = 2
    try:
        parameters = request(json.loads(Path(os.environ["CAD_REQUEST"]).read_text()))
        report = verify(parameters, Path(os.environ["CAD_SOURCE"]))
        (destination / "checks.json").write_text(json.dumps(report, indent=2, allow_nan=False))
        status = 0 if report["accepted"] else 2
    except Exception:
        report = {"schema": SCHEMA, "backend": "freecad",
                  "accepted": False, "error": "native_verification_failed"}
        (destination / "verifier-error.txt").write_text(traceback.format_exc())
        (destination / "checks.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    finally:
        # A Python/reporting error must not return to FreeCAD's GUI event
        # loop and leave a supposedly finished verifier alive.
        os._exit(status)
