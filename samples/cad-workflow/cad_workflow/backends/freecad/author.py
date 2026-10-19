"""Runs inside the pinned FreeCAD application, as the unprivileged author."""

import json
import math
import os
from pathlib import Path
import traceback

from cad_workflow.contract import BORES_MM, request


def construct(parameters, destination):
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    import Sketcher

    document = App.newDocument("SupportBracket")
    body = document.addObject("PartDesign::Body", "Support")
    stock = body.newObject("Sketcher::SketchObject", "StockSketch")
    vertices = [(0, 0), (160, 0), (160, 42), (0, 42)]
    for index, (x, y) in enumerate(vertices):
        end = vertices[(index + 1) % 4]
        stock.addGeometry(Part.LineSegment(App.Vector(x, y, 0), App.Vector(*end, 0)), False)
    for index in range(4):
        stock.addConstraint(Sketcher.Constraint("Coincident", index, 2, (index + 1) % 4, 1))
        stock.addConstraint(Sketcher.Constraint("Horizontal" if index % 2 == 0 else "Vertical", index))
    stock.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
    stock.addConstraint(Sketcher.Constraint("Distance", 0, 160.0))
    stock.addConstraint(Sketcher.Constraint("Distance", 1, 42.0))
    document.recompute()
    pad = body.newObject("PartDesign::Pad", "StockPad")
    pad.Profile = stock
    pad.Length = parameters["stock_thickness_mm"]
    document.recompute()
    stock.Visibility = False

    bores = body.newObject("Sketcher::SketchObject", "BoreSketch")
    bores.setExpression("Placement.Base.z", "StockPad.Length")
    for x, y in BORES_MM:
        index = bores.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), 3.25), False)
        bores.addConstraint(Sketcher.Constraint("Diameter", index, 6.5))
        bores.addConstraint(Sketcher.Constraint("DistanceX", index, 3, x))
        bores.addConstraint(Sketcher.Constraint("DistanceY", index, 3, y))
    document.recompute()
    holes = body.newObject("PartDesign::Pocket", "BorePocket")
    holes.Profile = bores
    holes.Type = 1
    document.recompute()
    pad.Visibility = False
    bores.Visibility = False

    slot = body.newObject("Sketcher::SketchObject", "PocketSketch")
    slot.setExpression("Placement.Base.z", "StockPad.Length")
    edges = [
        Part.LineSegment(App.Vector(55, 10, 0), App.Vector(123, 10, 0)),
        Part.ArcOfCircle(Part.Circle(App.Vector(123, 21, 0), App.Vector(0, 0, 1), 11), -math.pi / 2, math.pi / 2),
        Part.LineSegment(App.Vector(123, 32, 0), App.Vector(55, 32, 0)),
        Part.ArcOfCircle(Part.Circle(App.Vector(55, 21, 0), App.Vector(0, 0, 1), 11), math.pi / 2, 3 * math.pi / 2),
    ]
    for edge in edges:
        index = slot.addGeometry(edge, False)
        slot.addConstraint(Sketcher.Constraint("Block", index))
    document.recompute()
    pocket = body.newObject("PartDesign::Pocket", "LighteningPocket")
    pocket.Profile = slot
    pocket.Type = 0
    pocket.Length = parameters["pocket_depth_mm"]
    document.recompute()
    holes.Visibility = False
    slot.Visibility = False
    pocket.Visibility = True
    pocket.ViewObject.ShapeColor = (0.28, 0.53, 0.73)
    pocket.ViewObject.LineColor = (0.10, 0.14, 0.19)
    body.Tip = pocket
    if not pocket.Shape.isValid() or len(pocket.Shape.Solids) != 1:
        raise RuntimeError("Native construction did not produce one valid solid")
    for sketch in (stock, bores, slot):
        if not sketch.FullyConstrained:
            raise RuntimeError(f"{sketch.Name} is not fully constrained")
    document.saveAs(str(destination / "support.FCStd"))
    Part.export([body], str(destination / "support.step"))
    Gui.activeDocument().activeView().viewAxonometric()
    Gui.activeDocument().activeView().fitAll()
    Gui.updateGui()
    Gui.activeDocument().activeView().saveImage(str(destination / "preview.png"), 1280, 900, "White")
    return {"freecad": App.Version(), "occt": Part.OCC_VERSION, "features": [obj.Name for obj in body.Group]}


def main():
    destination = Path(os.environ["CAD_OUTPUT"])
    status = 0
    try:
        parameters = request(json.loads(Path(os.environ["CAD_REQUEST"]).read_text()))
        result = construct(parameters, destination)
        (destination / "author.json").write_text(json.dumps(result, indent=2))
    except Exception:
        (destination / "author-error.txt").write_text(traceback.format_exc())
        status = 2
    # FreeCAD's macro runner owns the GUI event loop; this dedicated process
    # exits only after files are closed. The supervisor fsyncs and freezes them.
    os._exit(status)
