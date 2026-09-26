Triton drawings - Revit add-in
==============================

Adds a "Draw bars" button (Add-Ins tab > Triton). It runs the same code as the DevKit
text (TritonDrawBars.txt): it asks for a Triton drawings file (.crm; Triton, Design tab >
Drawings > Revit), lists its drawings, and draws the ones you tick:

  * all in one new drafting view for the run (the default), each drawing in its frame
    with a caption under it and a base point (BP) to copy it from, or
  * each in its own drafting view, "Triton - <section> - <drawing>" (drawn again in
    place when the view already exists), or
  * into the view that is open, the top left at a point you click.

Bars are detail lines in the office line styles (T32-Reinforcement Section...); the
office families (DET_Round_Col_RFT_Dar, DET_Rebar_Dot Bar_Dar, DET_Rebar_51_Dar,
RFT_ADD_MODIFIED) are placed with their parameters set, or drawn with lines if not
loaded. The summary at the end lists anything missing.

Build (once, and again for each Revit version you use)
------------------------------------------------------
1. Install Visual Studio 2022 Community (free) with the ".NET desktop development"
   workload. Revit 2025 and later also need the .NET 8 SDK (comes with it).
2. Open TritonDrawings.csproj in Visual Studio.
3. In TritonDrawings.csproj, set <RevitVersion> to your Revit (2024 by default). If
   Revit is not in C:\Program Files\Autodesk\Revit <version>, set <RevitFolder> too.
4. Build > Build Solution. The build copies TritonDrawings.addin and
   TritonDrawings\TritonDrawings.dll into
   %AppData%\Autodesk\Revit\Addins\<version>\
5. Start Revit, press "Always Load" when it asks about the add-in.

Or from a command prompt in this folder:  dotnet build -c Release -p:RevitVersion=2024

Files
-----
TritonDrawings.csproj   project (targets .NET Framework 4.8 or .NET 8 by Revit version)
TritonDrawings.addin    Revit manifest
DrawCommand.cs          the button and the command
DevKitCode.cs           the drawing code (TritonDrawBars.txt in a method)
