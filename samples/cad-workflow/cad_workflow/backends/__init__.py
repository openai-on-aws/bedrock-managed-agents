"""Deployment-selected native CAD backends; no agent-supplied plugins or code."""


def get_backend(name="freecad"):
    if name == "freecad":
        from cad_workflow.backends import freecad

        return freecad
    raise ValueError(
        f"Unsupported CAD backend: {name!r}. Only FreeCAD is implemented; "
        "see docs/adding-a-backend.md for the CATIA V6 extension boundary."
    )
