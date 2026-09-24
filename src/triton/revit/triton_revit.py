# ruff: noqa  (runs inside Revit under IronPython 2 as well, so it keeps to Python 2 syntax)
# Triton drawings for Revit: draws a Triton drawings file (triton.drawings/1, "Drawings for Revit" on
# Triton's Design tab) as detail lines in drafting views.
#
# Runs in Revit's own Dynamo (Manage > Dynamo: open Triton-drawings.dyn, pick the file, Run) or as a
# pyRevit / RevitPythonShell script (it asks for the file). IronPython 2 and CPython 3 both work.
#
# * One drafting view per Triton view, named "<prefix> - <section> - <view>". Running it again redraws
#   the views it made before (their content is replaced; views placed on sheets stay on them).
# * Bars and links along the view: detail lines in the bar size's line style, or the line-based detail
#   family type when one is set. Cut bars: the detail family type set for the size, else a filled dot.
# * A line style the template lacks is made (a Lines subcategory), so the script runs before the office
#   names are set; the summary lists what was made and any family type it could not find.
#
# Nothing else in the model is changed.

import json
import math

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (  # noqa: E402
    XYZ,
    Arc,
    BuiltInCategory,
    BuiltInParameter,
    Category,
    Color,
    CurveElement,
    CurveLoop,
    ElementId,
    ElementTypeGroup,
    FamilyInstance,
    FamilySymbol,
    FilledRegion,
    FilledRegionType,
    FilteredElementCollector,
    GraphicsStyleType,
    Line,
    TextNote,
    TextNoteType,
    Transaction,
    ViewDrafting,
    ViewFamily,
    ViewFamilyType,
)
from System.Collections.Generic import List  # noqa: E402

FT = 304.8  # mm per foot: Revit works in feet
SHORT = 1.0  # mm; Revit refuses shorter lines
BAD_NAME = "\\:{}[]|;<>?`~"

try:  # Dynamo
    clr.AddReference("RevitServices")
    from RevitServices.Persistence import DocumentManager
    from RevitServices.Transactions import TransactionManager

    doc = DocumentManager.Instance.CurrentDBDocument
    DYNAMO = True
except Exception:  # pyRevit / RevitPythonShell
    doc = __revit__.ActiveUIDocument.Document  # noqa: F821
    DYNAMO = False

# Bar colours by size, only for line styles this script makes.
COLOURS = {
    8: (128, 128, 128),
    10: (255, 127, 0),
    12: (191, 127, 0),
    16: (0, 160, 0),
    20: (0, 170, 170),
    25: (0, 0, 255),
    28: (160, 0, 160),
    32: (220, 0, 0),
    40: (120, 60, 0),
}


def xyz(p):
    return XYZ(p[0] / FT, p[1] / FT, 0.0)


def type_name(e):
    """An element type's name (reading .Name directly fails on some types in IronPython)."""
    p = e.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
    return (p.AsString() if p else "") or ""


def clean(name):
    return "".join("_" if c in BAD_NAME else c for c in name).strip()


class Drawer:
    def __init__(self, data):
        self.data = data
        self.layers = data.get("layers", {})
        self.made = []
        self.missing = []
        self.styles = {}
        self.symbols = {}
        lines = Category.GetCategory(doc, BuiltInCategory.OST_Lines)
        self.lines_cat = lines
        self.subs = dict((c.Name, c) for c in lines.SubCategories)
        self.fill = self._solid_fill()
        self.text_type = self._text_type()

    # -- names from the drawings file to Revit elements
    def style(self, key):
        if key in self.styles:
            return self.styles[key]
        spec = self.layers.get(key, {})
        name = spec.get("revit_line_style") or key
        sub = self.subs.get(name)
        if sub is None:
            sub = doc.Settings.Categories.NewSubcategory(self.lines_cat, name)
            rgb = COLOURS.get(spec.get("diameter_mm"), (0, 0, 0))
            sub.LineColor = Color(rgb[0], rgb[1], rgb[2])
            sub.SetLineWeight(3 if key.startswith("bar-") else 1, GraphicsStyleType.Projection)
            self.subs[name] = sub
            self.made.append(name)
        gs = sub.GetGraphicsStyle(GraphicsStyleType.Projection)
        self.styles[key] = gs
        return gs

    def symbol(self, name):
        """A detail component type by 'Family: Type' (or the type name alone)."""
        if not name:
            return None
        if name in self.symbols:
            return self.symbols[name]
        found = None
        col = (
            FilteredElementCollector(doc)
            .OfClass(FamilySymbol)
            .OfCategory(BuiltInCategory.OST_DetailComponents)
        )
        for s in col:
            full = s.FamilyName + ": " + type_name(s)
            if name in (full, type_name(s)):
                found = s
                break
        if found is None:
            self.missing.append(name)
        elif not found.IsActive:
            found.Activate()
        self.symbols[name] = found
        return found

    def _solid_fill(self):
        types = list(FilteredElementCollector(doc).OfClass(FilledRegionType))
        for t in types:
            if "solid" in type_name(t).lower():
                return t.Id
        return types[0].Id if types else None

    def _text_type(self):
        want = self.layers.get("text", {}).get("revit_text_type")
        if want:
            for t in FilteredElementCollector(doc).OfClass(TextNoteType):
                if type_name(t) == want:
                    return t.Id
            self.missing.append(want)
        return doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)

    # -- views
    def view(self, v):
        prefix = self.data.get("view_prefix") or "Triton"
        parts = [prefix, self.data.get("section") or "", v["name"]]
        name = clean(" - ".join(p for p in parts if p))
        for old in FilteredElementCollector(doc).OfClass(ViewDrafting):
            if old.Name == name:
                ids = [
                    e.Id
                    for e in FilteredElementCollector(doc, old.Id).WhereElementIsNotElementType()
                    if isinstance(e, (CurveElement, TextNote, FamilyInstance, FilledRegion))
                ]
                if ids:
                    doc.Delete(List[ElementId](ids))
                return old, False
        vft = [
            t
            for t in FilteredElementCollector(doc).OfClass(ViewFamilyType)
            if t.ViewFamily == ViewFamily.Drafting
        ][0]
        view = ViewDrafting.Create(doc, vft.Id)
        view.Name = name
        view.Scale = int(v.get("scale") or 20)
        return view, True

    # -- items
    def line(self, view, key, a, b):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < SHORT:
            return
        curve = Line.CreateBound(xyz(a), xyz(b))
        fam = self.symbol(self.layers.get(key, {}).get("revit_line_type"))
        if fam is not None:
            doc.Create.NewFamilyInstance(curve, fam, view)
            return
        doc.Create.NewDetailCurve(view, curve).LineStyle = self.style(key)

    def circle(self, view, key, c, r):
        centre = xyz(c)
        for s0 in (0.0, math.pi):  # two halves: a closed arc is not always accepted
            arc = Arc.Create(centre, r / FT, s0, s0 + math.pi, XYZ.BasisX, XYZ.BasisY)
            doc.Create.NewDetailCurve(view, arc).LineStyle = self.style(key)

    def bar(self, view, key, c, d):
        fam = self.symbol(self.layers.get(key, {}).get("revit_section_type"))
        if fam is not None:
            doc.Create.NewFamilyInstance(xyz(c), fam, view)
            return
        if self.fill is not None:
            try:
                loop = CurveLoop()
                centre = xyz(c)
                r = d / 2.0 / FT
                loop.Append(Arc.Create(centre, r, 0.0, math.pi, XYZ.BasisX, XYZ.BasisY))
                loop.Append(Arc.Create(centre, r, math.pi, 2 * math.pi, XYZ.BasisX, XYZ.BasisY))
                region = FilledRegion.Create(doc, self.fill, view.Id, List[CurveLoop]([loop]))
                try:
                    region.SetLineStyleId(self.style(key).Id)
                except Exception:
                    pass
                return
            except Exception:
                pass
        self.circle(view, key, c, d / 2.0)

    def draw(self, view, v):
        for it in v["items"]:
            t, key = it["type"], it["layer"]
            if t == "line":
                self.line(view, key, it["a"], it["b"])
            elif t == "rect":
                (ax, ay), (bx, by) = it["a"], it["b"]
                pts = [(ax, ay), (bx, ay), (bx, by), (ax, by), (ax, ay)]
                for p, q in zip(pts, pts[1:]):
                    self.line(view, key, p, q)
            elif t == "circle":
                self.circle(view, key, it["c"], it["r"])
            elif t == "bar":
                self.bar(view, key, it["c"], it["d"])
            elif t == "text":
                TextNote.Create(doc, view.Id, xyz(it["at"]), it["text"], self.text_type)


def run(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != "triton.drawings/1":
        return "Not a Triton drawings file (Drawings for Revit on the Design tab): " + path
    if DYNAMO:
        TransactionManager.Instance.EnsureInTransaction(doc)
    else:
        t = Transaction(doc, "Triton drawings")
        t.Start()
    try:
        d = Drawer(data)
        new, again = 0, 0
        for v in data["views"]:
            view, made = d.view(v)
            d.draw(view, v)
            new, again = new + made, again + (not made)
    except Exception:
        if not DYNAMO:
            t.RollBack()
        raise
    if DYNAMO:
        TransactionManager.Instance.TransactionTaskDone()
    else:
        t.Commit()
    out = ["Triton: %d drafting views made, %d redrawn." % (new, again)]
    if d.made:
        out.append("Line styles made (not in the template): " + ", ".join(d.made))
    if d.missing:
        out.append("Not found, drawn as lines or dots instead: " + ", ".join(sorted(set(d.missing))))
    return "\n".join(out)


if DYNAMO:
    OUT = run(IN[0])  # noqa: F821
else:
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import DialogResult, OpenFileDialog

    dlg = OpenFileDialog()
    dlg.Filter = "Triton drawings (*.json)|*.json"
    dlg.Title = "Triton drawings file"
    if dlg.ShowDialog() == DialogResult.OK:
        print(run(dlg.FileName))
