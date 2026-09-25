using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

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
                ToolTip = "Pick a Triton drawings file (.crm, Design tab > Drawings > Revit) and draw it with the "
                    + "office line styles and families.",
            };
            panel.AddItem(data);
            return Result.Succeeded;
        }

        public Result OnShutdown(UIControlledApplication app) => Result.Succeeded;
    }

    /// <summary>
    /// Runs the same code as the DevKit text (DevKitCode.cs is TritonDrawBars.txt wrapped in a method), so
    /// the button and the paste-in code always draw the same thing.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    public class DrawCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        {
            DevKitCode.Run(data.Application.ActiveUIDocument.Document);
            return Result.Succeeded;
        }
    }
}
