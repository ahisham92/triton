Triton drawings - Revit add-in
==============================

Adds a "Draw bars" button (Add-Ins tab > Triton). It asks for a Triton drawings file
(Triton, Design tab > Drawings > Revit), lists its views, and draws the ones you tick:

  * each in its own drafting view, "Triton - <section> - <view>" (drawn again in place
    when the view already exists, so views on sheets stay on them), or
  * into the view that is open (drafting, plan, section, elevation or detail view), with
    the top left of the drawing at a point you click.

Bars and links are detail lines in the line style named for their size on Triton's
Project tab (Drawing names), or that size's line-based detail family when one is set.
Cut bars are that size's detail family, else a filled dot. A line style the project
does not have yet is made, and the add-in says which ones it made.

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
PickViewsForm.cs        the view picker
Drawer.cs               draws lines, bars and text
TritonFile.cs, Json.cs  read the drawings file (no other DLL needed)
