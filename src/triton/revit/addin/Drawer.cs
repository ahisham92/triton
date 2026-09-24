using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;

namespace Triton.Revit
{
    /// <summary>
    /// Draws Triton views as detail items: bars and links along the view as detail lines in the bar size's
    /// line style (or its line-based detail family), cut bars as the size's detail family or a filled dot,
    /// outlines in their line styles and text as text notes. A line style the project lacks is made.
    /// </summary>
    public class Drawer
    {
        const double FT = 304.8; // mm per foot: Revit works in feet
        const double SHORT_MM = 1.0; // Revit refuses shorter lines

        static readonly Dictionary<int, byte[]> Colours = new Dictionary<int, byte[]>
        {
            [8] = new byte[] { 128, 128, 128 }, [10] = new byte[] { 255, 127, 0 }, [12] = new byte[] { 191, 127, 0 },
            [16] = new byte[] { 0, 160, 0 }, [20] = new byte[] { 0, 170, 170 }, [25] = new byte[] { 0, 0, 255 },
            [28] = new byte[] { 160, 0, 160 }, [32] = new byte[] { 220, 0, 0 }, [40] = new byte[] { 120, 60, 0 },
        };

        readonly Document doc;
        readonly TritonFile file;
        readonly Category linesCat;
        readonly Dictionary<string, Category> subs = new Dictionary<string, Category>();
        readonly Dictionary<string, GraphicsStyle> styles = new Dictionary<string, GraphicsStyle>();
        readonly Dictionary<string, FamilySymbol> symbols = new Dictionary<string, FamilySymbol>();
        readonly ElementId fillType;
        readonly ElementId textType;

        public readonly List<string> Made = new List<string>();
        public readonly List<string> Missing = new List<string>();
        public int Lines, Bars, Texts;

        public Drawer(Document doc, TritonFile file)
        {
            this.doc = doc;
            this.file = file;
            linesCat = Category.GetCategory(doc, BuiltInCategory.OST_Lines);
            foreach (Category c in linesCat.SubCategories) subs[c.Name] = c;
            var fills = new FilteredElementCollector(doc).OfClass(typeof(FilledRegionType)).Cast<FilledRegionType>().ToList();
            var solid = fills.FirstOrDefault(t => t.Name.IndexOf("solid", StringComparison.OrdinalIgnoreCase) >= 0);
            fillType = (solid ?? fills.FirstOrDefault())?.Id;
            textType = TextType(file.Names("text").TextType);
        }

        /// <summary>Where a view's own point (x, y in mm) lands in the Revit view.</summary>
        public class Frame
        {
            public XYZ Origin, Right, Up;
            public double Dx, Dy; // mm, added to every point

            public XYZ At(double[] p) => At(p[0], p[1]);

            public XYZ At(double x, double y) =>
                Origin + Right.Multiply((x + Dx) / FT) + Up.Multiply((y + Dy) / FT);
        }

        public void Draw(View view, DrawingView v, Frame frame)
        {
            foreach (var it in v.Items)
            {
                switch (it.Type)
                {
                    case "line":
                        Line(view, it.Layer, frame.At(it.A), frame.At(it.B));
                        break;
                    case "rect":
                        var a = it.A;
                        var b = it.B;
                        var pts = new[] { frame.At(a[0], a[1]), frame.At(b[0], a[1]), frame.At(b[0], b[1]), frame.At(a[0], b[1]) };
                        for (int k = 0; k < 4; k++) Line(view, it.Layer, pts[k], pts[(k + 1) % 4]);
                        break;
                    case "circle":
                        Circle(view, it.Layer, frame.At(it.C), it.R, frame);
                        break;
                    case "bar":
                        Bar(view, it.Layer, frame.At(it.C), it.D, frame);
                        break;
                    case "text":
                        TextNote.Create(doc, view.Id, frame.At(it.A), it.Text, textType);
                        Texts++;
                        break;
                }
            }
        }

        void Line(View view, string key, XYZ p, XYZ q)
        {
            if (p.DistanceTo(q) * FT < SHORT_MM) return;
            var line = Autodesk.Revit.DB.Line.CreateBound(p, q);
            var fam = Symbol(file.Names(key).LineType);
            if (fam != null) doc.Create.NewFamilyInstance(line, fam, view);
            else doc.Create.NewDetailCurve(view, line).LineStyle = Style(key);
            Lines++;
        }

        void Circle(View view, string key, XYZ c, double rMm, Frame f)
        {
            foreach (var arc in Halves(c, rMm, f)) doc.Create.NewDetailCurve(view, arc).LineStyle = Style(key);
            Lines++;
        }

        static Arc[] Halves(XYZ c, double rMm, Frame f) => new[]
        {
            Arc.Create(c, rMm / FT, 0, Math.PI, f.Right, f.Up),
            Arc.Create(c, rMm / FT, Math.PI, 2 * Math.PI, f.Right, f.Up),
        };

        void Bar(View view, string key, XYZ c, double dMm, Frame f)
        {
            Bars++;
            var fam = Symbol(file.Names(key).SectionType);
            if (fam != null)
            {
                doc.Create.NewFamilyInstance(c, fam, view);
                return;
            }
            if (fillType != null)
            {
                try
                {
                    var loop = new CurveLoop();
                    foreach (var arc in Halves(c, dMm / 2, f)) loop.Append(arc);
                    var region = FilledRegion.Create(doc, fillType, view.Id, new List<CurveLoop> { loop });
                    try { region.SetLineStyleId(Style(key).Id); } catch (Exception) { }
                    return;
                }
                catch (Autodesk.Revit.Exceptions.ApplicationException) { }
            }
            Circle(view, key, c, dMm / 2, f);
        }

        GraphicsStyle Style(string key)
        {
            if (styles.TryGetValue(key, out var gs)) return gs;
            var names = file.Names(key);
            string name = string.IsNullOrWhiteSpace(names.LineStyle) ? key : names.LineStyle;
            if (!subs.TryGetValue(name, out var sub))
            {
                sub = doc.Settings.Categories.NewSubcategory(linesCat, name);
                var rgb = Colours.TryGetValue(names.Diameter, out var c) ? c : new byte[] { 0, 0, 0 };
                sub.LineColor = new Color(rgb[0], rgb[1], rgb[2]);
                sub.SetLineWeight(key.StartsWith("bar-") ? 3 : 1, GraphicsStyleType.Projection);
                subs[name] = sub;
                Made.Add(name);
            }
            gs = sub.GetGraphicsStyle(GraphicsStyleType.Projection);
            styles[key] = gs;
            return gs;
        }

        /// <summary>A detail component type by "Family: Type", or by the type name alone.</summary>
        FamilySymbol Symbol(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return null;
            if (symbols.TryGetValue(name, out var s)) return s;
            s = new FilteredElementCollector(doc)
                .OfClass(typeof(FamilySymbol))
                .OfCategory(BuiltInCategory.OST_DetailComponents)
                .Cast<FamilySymbol>()
                .FirstOrDefault(x => x.FamilyName + ": " + x.Name == name || x.Name == name);
            if (s == null) Missing.Add(name);
            else if (!s.IsActive) s.Activate();
            symbols[name] = s;
            return s;
        }

        ElementId TextType(string name)
        {
            if (!string.IsNullOrWhiteSpace(name))
            {
                var t = new FilteredElementCollector(doc).OfClass(typeof(TextNoteType)).FirstOrDefault(x => x.Name == name);
                if (t != null) return t.Id;
                Missing.Add(name);
            }
            return doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType);
        }
    }
}
