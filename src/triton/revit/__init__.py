"""The Revit side of the drawing export: what draws a Triton drawings file as detail lines.

``addin_zip()`` is the Revit add-in (C#, Revit API) as source to build once in Visual Studio: a Triton
button that picks the file and draws it into drafting views or the open view. The Python script below
does the same without building anything:
``script()`` is the Python file (pyRevit, RevitPythonShell, or pasted into a Dynamo Python node);
``dynamo_graph()`` wraps it in a Dynamo graph (.dyn) with a File Path input, for Revit's own Dynamo.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("triton_revit.py")
ADDIN = Path(__file__).with_name("addin")
ADDIN_FILES = ("*.cs", "*.csproj", "*.addin", "README.txt")
ENGINES = ("CPython3", "IronPython2")


def script() -> str:
    return SCRIPT.read_text("utf-8")


def addin_zip() -> bytes:
    """The add-in's source in a TritonDrawings folder, ready to open in Visual Studio."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for pattern in ADDIN_FILES:
            for f in sorted(ADDIN.glob(pattern)):
                z.write(f, f"TritonDrawings/{f.name}")
    return buf.getvalue()


def _id() -> str:
    return uuid.uuid4().hex


def dynamo_graph(engine: str = "CPython3") -> str:
    """A Dynamo 2.x graph: File Path -> Python Script (the Triton script) -> Watch."""
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}")
    file_node, file_out = _id(), _id()
    py_node, py_in, py_out = _id(), _id(), _id()
    watch_node, watch_in, watch_out = _id(), _id(), _id()

    def port(pid: str, name: str, desc: str) -> dict:
        return {
            "Id": pid,
            "Name": name,
            "Description": desc,
            "UsingDefaultValue": False,
            "Level": 2,
            "UseLevels": False,
            "KeepListStructure": False,
        }

    nodes = [
        {
            "ConcreteType": "CoreNodeModels.Input.Filename, CoreNodeModels",
            "HintPath": "",
            "InputValue": "",
            "NodeType": "ExtensionNode",
            "Id": file_node,
            "Inputs": [],
            "Outputs": [port(file_out, "", "Filename")],
            "Replication": "Disabled",
            "Description": "Allows you to select a file on the system to get its filename",
        },
        {
            "ConcreteType": "PythonNodeModels.PythonNode, PythonNodeModels",
            "Code": script(),
            "Engine": engine,
            "EngineName": engine,
            "VariableInputPorts": True,
            "NodeType": "PythonScriptNode",
            "Id": py_node,
            "Inputs": [port(py_in, "IN[0]", "Input #0")],
            "Outputs": [port(py_out, "OUT", "Result of the python script")],
            "Replication": "Disabled",
            "Description": "Runs an embedded Python script.",
        },
        {
            "ConcreteType": "CoreNodeModels.Watch, CoreNodeModels",
            "NodeType": "ExtensionNode",
            "Id": watch_node,
            "Inputs": [port(watch_in, "", "Node to show output from")],
            "Outputs": [port(watch_out, "", "Node output")],
            "Replication": "Disabled",
            "Description": "Visualize the node's output",
        },
    ]
    views = [
        (file_node, "Triton drawings file (pick it)", 0.0, True),
        (py_node, "Draw Triton bars as detail lines", 420.0, False),
        (watch_node, "Result", 760.0, False),
    ]
    graph = {
        "Uuid": _id(),
        "IsCustomNode": False,
        "Description": "Draws a Triton drawings file as detail lines in drafting views.",
        "Name": "Triton drawings",
        "ElementResolver": {"ResolutionMap": {}},
        "Inputs": [],
        "Outputs": [],
        "Nodes": nodes,
        "Connectors": [
            {"Start": file_out, "End": py_in, "Id": _id(), "IsHidden": "False"},
            {"Start": py_out, "End": watch_in, "Id": _id(), "IsHidden": "False"},
        ],
        "Dependencies": [],
        "NodeLibraryDependencies": [],
        "Bindings": [],
        "View": {
            "Dynamo": {
                "ScaleFactor": 1.0,
                "HasRunWithoutCrash": True,
                "IsVisibleInDynamoLibrary": True,
                "Version": "2.12.0.5650",
                "RunType": "Manual",
                "RunPeriod": "1000",
            },
            "Camera": {
                "Name": "Background Preview",
                "EyeX": -17.0,
                "EyeY": 24.0,
                "EyeZ": 50.0,
                "LookX": 12.0,
                "LookY": -13.0,
                "LookZ": -58.0,
                "UpX": 0.0,
                "UpY": 1.0,
                "UpZ": 0.0,
            },
            "NodeViews": [
                {
                    "Id": nid,
                    "Name": name,
                    "IsSetAsInput": is_input,
                    "IsSetAsOutput": False,
                    "Excluded": False,
                    "ShowGeometry": True,
                    "X": x,
                    "Y": 0.0,
                }
                for nid, name, x, is_input in views
            ],
            "Annotations": [],
            "X": 0.0,
            "Y": 0.0,
            "Zoom": 1.0,
        },
    }
    return json.dumps(graph, indent=2)
