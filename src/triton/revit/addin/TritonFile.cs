using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Triton.Revit
{
    /// <summary>One item of a view: a line, rectangle, circle, cut bar or text, in mm on the view.</summary>
    public class Item
    {
        public string Type, Layer, Text;
        public double[] A, B, C;
        public double R, D, H;
    }

    public class DrawingView
    {
        public string Name, Title, Element;
        public int Scale;
        public double[] Box; // x0, y0, x1, y1 (mm)
        public List<Item> Items = new List<Item>();
        public override string ToString() => Name;
    }

    /// <summary>What a layer key (bar-16, concrete, zones, text) is called in Revit.</summary>
    public class LayerNames
    {
        public int Diameter;
        public string LineStyle = "", SectionType = "", LineType = "", TextType = "";
    }

    /// <summary>A Triton drawings file (format triton.drawings/1).</summary>
    public class TritonFile
    {
        public const string Format = "triton.drawings/1";
        public string Project, Section, ViewPrefix;
        public List<DrawingView> Views = new List<DrawingView>();
        public Dictionary<string, LayerNames> Layers = new Dictionary<string, LayerNames>();

        public static TritonFile Load(string path)
        {
            var root = Json.Parse(File.ReadAllText(path)) as Dictionary<string, object>;
            if (root == null || Str(root, "format") != Format)
                throw new InvalidDataException("This is not a Triton drawings file (Design tab > Drawings > Revit).");
            var f = new TritonFile
            {
                Project = Str(root, "project"),
                Section = Str(root, "section"),
                ViewPrefix = Str(root, "view_prefix"),
            };
            if (string.IsNullOrWhiteSpace(f.ViewPrefix)) f.ViewPrefix = "Triton";
            if (root.TryGetValue("layers", out var ls) && ls is Dictionary<string, object> layers)
            {
                foreach (var kv in layers)
                {
                    var l = kv.Value as Dictionary<string, object> ?? new Dictionary<string, object>();
                    f.Layers[kv.Key] = new LayerNames
                    {
                        Diameter = (int)Num(l, "diameter_mm"),
                        LineStyle = Str(l, "revit_line_style"),
                        SectionType = Str(l, "revit_section_type"),
                        LineType = Str(l, "revit_line_type"),
                        TextType = Str(l, "revit_text_type"),
                    };
                }
            }
            foreach (var o in List(root, "views"))
            {
                var v = (Dictionary<string, object>)o;
                var view = new DrawingView
                {
                    Name = Str(v, "name"),
                    Title = Str(v, "title"),
                    Element = Str(v, "element"),
                    Scale = Math.Max(1, (int)Num(v, "scale")),
                    Box = Pt(v, "box") ?? new double[4],
                };
                foreach (var io in List(v, "items"))
                {
                    var it = (Dictionary<string, object>)io;
                    view.Items.Add(new Item
                    {
                        Type = Str(it, "type"),
                        Layer = Str(it, "layer"),
                        Text = Str(it, "text"),
                        A = Pt(it, "a") ?? Pt(it, "at"),
                        B = Pt(it, "b"),
                        C = Pt(it, "c"),
                        R = Num(it, "r"),
                        D = Num(it, "d"),
                        H = Num(it, "h"),
                    });
                }
                f.Views.Add(view);
            }
            return f;
        }

        public LayerNames Names(string key) =>
            Layers.TryGetValue(key ?? "", out var n) ? n : new LayerNames();

        static string Str(Dictionary<string, object> d, string k) =>
            d.TryGetValue(k, out var v) && v != null ? Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture) : "";

        static double Num(Dictionary<string, object> d, string k) =>
            d.TryGetValue(k, out var v) && v is double x ? x : 0.0;

        static List<object> List(Dictionary<string, object> d, string k) =>
            d.TryGetValue(k, out var v) && v is List<object> l ? l : new List<object>();

        static double[] Pt(Dictionary<string, object> d, string k) =>
            d.TryGetValue(k, out var v) && v is List<object> l ? l.Select(x => x is double n ? n : 0.0).ToArray() : null;
    }
}
