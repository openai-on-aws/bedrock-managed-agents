"""FreeCAD adapter metadata and launcher; importing this does not require FreeCAD."""

NAME = "freecad"
NATIVE_FILE = "support.FCStd"
CHECKED_FILES = (NATIVE_FILE, "support.step")
CANDIDATE_FILES = (*CHECKED_FILES, "preview.png", "author.json")
AUTHOR_MODULE = "cad_workflow.backends.freecad.author"
VERIFIER_MODULE = "cad_workflow.backends.freecad.verifier"
SOURCE_FILES = (
    "backends/freecad/__init__.py",
    "backends/freecad/author.py",
    "backends/freecad/verifier.py",
)


def command(phase, directory, code):
    """Launch only a packaged macro, in a fresh native application under Xvfb."""
    if phase not in ("author", "verifier", "mutant"):
        raise ValueError("Unknown native phase")
    return [
        "/usr/bin/xvfb-run", "-a", "-s", "-screen 0 1280x900x24",
        "/opt/freecad/AppRun", "--user-cfg", str(directory / "user.cfg"),
        "--system-cfg", str(directory / "system.cfg"), str(code / f"{phase}.FCMacro"),
    ]
