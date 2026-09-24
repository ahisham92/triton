using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Windows.Forms;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;
using View = Autodesk.Revit.DB.View;

namespace Triton.Revit
{
    /// <summary>Adds the Triton button to the Add-Ins tab.</summary>
    public class App : IExternalApplication
    {
        public Result OnStartup(UIControlledApplication app)
        {
            var panel = app.CreateRibbonPanel("Triton");
            var data = new PushButtonData(
                "TritonDrawBars", "Draw bars", typeof(App).Assembly.Location, typeof(DrawCommand).FullName)
            {
                ToolTip = "Pick a Triton drawings file (Design tab > Drawings > Revit) and draw its bars as detail lines.",
            };
            panel.AddItem(data);
            return Result.Succeeded;
        }

        public Result OnShutdown(UIControlledApplication app) => Result.Succeeded;
    }

    /// <summary>
    /// Pick a Triton drawings file, pick its views, then draw them either into the active view (at a
    /// point you click) or each into its own drafting view "prefix - section - view", redrawn in place
    /// when it already exists so views on sheets stay on them.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    public class DrawCommand : IExternalCommand
    {
        static readonly HashSet<ViewType> Detailable = new HashSet<ViewType>
        {
            ViewType.DraftingView, ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
            ViewType.AreaPlan, ViewType.Section, ViewType.Elevation, ViewType.Detail, ViewType.Legend,
        };

        static string lastFolder;

        public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        {
            var uidoc = data.Application.ActiveUIDocument;
            var doc = uidoc.Document;
            string path;
            using (var dlg = new OpenFileDialog
            {
                Title = "Triton drawings file",
                Filter = "Triton drawings (*.json)|*.json|All files (*.*)|*.*",
                InitialDirectory = lastFolder ?? "",
            })
            {
                if (dlg.ShowDialog() != DialogResult.OK) return Result.Cancelled;
                path = dlg.FileName;
                lastFolder = Path.GetDirectoryName(path);
            }

            TritonFile file;
            try
            {
                file = TritonFile.Load(path);
            }
            catch (Exception e)
            {
                TaskDialog.Show("Triton", "Could not read " + Path.GetFileName(path) + ":\n" + e.Message);
                return Result.Failed;
            }
            if (file.Views.Count == 0)
            {
                TaskDialog.Show("Triton", "The file has no views to draw.");
                return Result.Cancelled;
            }

            var active = doc.ActiveView;
            bool canUseActive = active != null && Detailable.Contains(active.ViewType) && !active.IsTemplate;
            List<DrawingView> picked;
            bool intoActive;
            using (var form = new PickViewsForm(file, canUseActive ? active.Name : null))
            {
                if (form.ShowDialog() != DialogResult.OK || form.Picked.Count == 0) return Result.Cancelled;
                picked = form.Picked;
                intoActive = form.IntoActiveView;
            }

            XYZ corner = null;
            if (intoActive)
            {
                try
                {
                    corner = uidoc.Selection.PickPoint("Click where the top left of the Triton drawing goes");
                }
                catch (Autodesk.Revit.Exceptions.OperationCanceledException)
                {
                    return Result.Cancelled;
                }
                catch (Autodesk.Revit.Exceptions.InvalidOperationException)
                {
                    corner = active.Origin; // no work plane to click on: the view's origin
                }
            }

            var drawer = new Drawer(doc, file);
            View first = null;
            int made = 0, redrawn = 0;
            using (var t = new Transaction(doc, "Triton drawings"))
            {
                t.Start();
                try
                {
                    if (intoActive)
                    {
                        var at = Layout(picked);
                        for (int i = 0; i < picked.Count; i++)
                        {
                            var frame = new Drawer.Frame
                            {
                                Origin = corner, Right = active.RightDirection, Up = active.UpDirection,
                                Dx = at[i][0], Dy = at[i][1],
                            };
                            drawer.Draw(active, picked[i], frame);
                        }
                    }
                    else
                    {
                        foreach (var v in picked)
                        {
                            var view = DraftingView(doc, file, v, out bool isNew);
                            if (isNew) made++; else redrawn++;
                            first = first ?? view;
                            var frame = new Drawer.Frame { Origin = XYZ.Zero, Right = XYZ.BasisX, Up = XYZ.BasisY };
                            drawer.Draw(view, v, frame);
                        }
                    }
                    t.Commit();
                }
                catch (Exception e)
                {
                    if (t.HasStarted()) t.RollBack();
                    message = e.Message;
                    TaskDialog.Show("Triton", "Nothing was drawn:\n" + e.Message);
                    return Result.Failed;
                }
            }

            var lines = new List<string>
            {
                intoActive
                    ? $"Drew {picked.Count} Triton view(s) in {active.Name}."
                    : $"{made} drafting view(s) made, {redrawn} redrawn.",
                $"{drawer.Lines} lines, {drawer.Bars} cut bars, {drawer.Texts} texts.",
            };
            if (drawer.Made.Count > 0)
                lines.Add("Line styles made (not in the project yet): " + string.Join(", ", drawer.Made));
            if (drawer.Missing.Count > 0)
                lines.Add("Not found, drawn as lines or dots instead: " + string.Join(", ", drawer.Missing.Distinct()));
            TaskDialog.Show("Triton", string.Join("\n", lines));
            if (first != null) uidoc.ActiveView = first;
            return Result.Succeeded;
        }

        /// <summary>The drafting view for a Triton view: the one of the same name, emptied, or a new one.</summary>
        static ViewDrafting DraftingView(Document doc, TritonFile file, DrawingView v, out bool isNew)
        {
            string name = Clean(string.Join(" - ", new[] { file.ViewPrefix, file.Section, v.Name }.Where(s => !string.IsNullOrWhiteSpace(s))));
            var old = new FilteredElementCollector(doc).OfClass(typeof(ViewDrafting)).Cast<ViewDrafting>()
                .FirstOrDefault(x => !x.IsTemplate && x.Name == name);
            if (old != null)
            {
                var ids = new FilteredElementCollector(doc, old.Id).WhereElementIsNotElementType()
                    .Where(e => e is CurveElement || e is TextNote || e is FamilyInstance || e is FilledRegion)
                    .Select(e => e.Id).ToList();
                if (ids.Count > 0) doc.Delete(ids);
                isNew = false;
                return old;
            }
            var type = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>()
                .First(x => x.ViewFamily == ViewFamily.Drafting);
            var view = ViewDrafting.Create(doc, type.Id);
            view.Name = name;
            view.Scale = v.Scale;
            isNew = true;
            return view;
        }

        static string Clean(string name)
        {
            foreach (char c in "\\:{}[]|;<>?`~") name = name.Replace(c, '_');
            return name.Trim();
        }

        /// <summary>Offsets (mm) that put the views side by side, rows downward, as the AutoCAD file does.</summary>
        static List<double[]> Layout(List<DrawingView> views)
        {
            const double gap = 3000, rowWidth = 120000;
            var outp = new List<double[]>();
            double x = 0, y = 0, rowH = 0;
            foreach (var v in views)
            {
                double w = v.Box[2] - v.Box[0], h = v.Box[3] - v.Box[1];
                if (x > 0 && x + w > rowWidth)
                {
                    x = 0;
                    y -= rowH + 2 * gap;
                    rowH = 0;
                }
                outp.Add(new[] { x - v.Box[0], y - v.Box[3] });
                x += w + gap;
                rowH = Math.Max(rowH, h);
            }
            return outp;
        }
    }
}
