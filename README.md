# Triton

Structural design of marine structure elements from Plaxis 3D straining actions.
Upload the geotechnical team's workbook and Triton checks it, designs each element
to Eurocode (EC2 / EC3) with BS 6349, and reports reinforcement and utilization ratios.

So far it covers **importing and checking the workbook**, **project setup** (sections, materials and design settings for each element) and the **longitudinal design of piles** for axial force and bending.

## Run it

```bash
pip install -e ".[dev]"
triton serve            # web app on http://127.0.0.1:8000
triton check "Section 01a.xlsb"   # same checks from the command line
pytest                  # tests
```

## Project setup

Open the web app, create a project, then either upload the workbook on the **Workbook** tab
and add every element it contains, or add elements by name (`Pile(5)`, `Deck`, …).
Each element gets its own inputs:

| Element | Inputs |
|---|---|
| Pile | diameter, cover, concrete, crack width limit, head level (results above it are inside the slab and ignored), optional steel casing |
| Steel casing | top and bottom level, thickness, corrosion loss, steel grade, and its role: *crack width only* or *structural* (shares forces with the concrete by E·I) |
| Combi wall | tube diameter and thickness, corrosion loss, steel and infill grades, infill bottom level (default −25 m), front beam soffit level |
| Sheet pile wall | section, steel grade, A / Wel / Wpl per m, class, corrosion loss per face |
| Slab | thickness, top and bottom cover, uniform or column and field strips |
| Front / rear beam | width, depth, cover |

Project-wide settings: partial factors, bar sizes to try, spacing limits, and whether the
reinforcement is chosen for the least steel or the lowest cost. Projects are saved as JSON
in `data/projects/` (set `TRITON_DATA_DIR` to move it).

## Load multipliers

On the Workbook tab, add a multiplier (e.g. 1.35) and tick the combinations or single sheets it
applies to (e.g. the Set B sheets). Design multiplies every straining action of those sheets, phase
and min/max, and never X, Y or Z. A sheet can be in one multiplier only, and results list the
multipliers used.

## Pile design (N–M)

Upload the workbook on the project's **Workbook** tab, then press **Design piles** on the **Design** tab.
For each pile element Triton takes every ULS result (all piles of the row, every node below the pile
head level; QP is not used) and picks the lightest cage that carries all of them, or the cheapest
if that is the chosen objective.

- Cages: 1, 1.5, 2, 2.5 or 3 rows. 1.5 rows is a full outer row plus half as many bars behind every
  second bar (e.g. 26Ø32 + 13Ø16); inner rows use one bar size no larger than the outer bars.
  Extra rows are used only when one row is not enough, unless that setting is switched off.
- Project settings (Design settings, *Pile reinforcement*): bars available on the project (default
  10 to 32 mm; piles use 16 mm and up), even bar counts, aggregate size, minimum clear spacing
  (project value, never below 8.2(2): max(φ, dg + 5, 20 mm)), maximum clear spacing (at most
  200 mm, 9.8.5(3)), the clear gap between rows (default: the 8.2(2) minimum), the rows allowed,
  laps or couplers, lap length (45φ) and the maximum steel ratio (4%, more only with couplers). Each pile can also fix the number of
  bars in its outer row (e.g. 26 for a 1200 mm pile).
- Section: EN 1992-1-1 parabola-rectangle concrete, bilinear steel with a horizontal top branch, strain
  limits of 6.1, displaced concrete deducted (the AdSec EC2 defaults). M is the resultant of M_2 and M_3.
- Utilisation is measured along the ray from the origin to each (N, M) point, taking the worst bar
  orientation (a bar on the bending axis, and midway between bars of each row).
- Seismic and accidental combinations use γc 1.2 and γs 1.0.
- Detailing: bars at least 16 mm, at least 6 bars in the outer row, every row's clear spacing at least
  the project minimum and the 8.2 minimum, the outer row's at most the project maximum (9.8.5 allows
  200 mm), steel between Table 9.6N and 4% (9.5.2). The outer bars sit at cover + link + φ/2.
- Only the corners of the convex hull of the (N, M) points are checked while searching; the maximum
  utilisation is always at one of them, because the capacity domain is convex.
- Results: the cage row by row with its cross-section drawn to scale, utilisation, ρ and kg/m³, the
  governing point, the N–M chart with every load point, utilisation along the pile, and the best
  other cages for each number of rows and outer bar size.

### Reinforcement down the pile

The head cage is then reduced down the pile in bar runs. Consecutive runs keep the same number of
bars in the outer row so they can be lapped (26Ø32 above 26Ø16) and may drop rows (2 rows above
1 row); no row gets bigger bars than the row above it. Each cage carries every load in its own run.
Bars run on below their run by the lap (lap factor × φ of the larger lapped bar, rounded up to
50 mm; the project default is 45φ) or join with couplers, and no bar is longer than the longest bar.
At laps the two cages together stay within 8% (9.5.2(3)); with couplers the 4% limit may be raised.

- *Least steel*: least total weight including laps, every run at least the minimum zone length (3 m).
- *Standard cut lengths*: as many bars as possible with a standard length (6, 8, 9, 12 m), then least weight.

The card shows the pile elevation, the bar schedule (levels, cage, bar lengths, laps, utilisation)
and the weight and kg/m³ against running the head cage all the way down.

### Export for Revit

**Download cages for Revit (JSON)** on the Design tab (`GET /api/projects/{id}/design/piles/cages.json`)
gives, for every pile element: diameter, cover, link, head and toe levels, the X, Y of each pile in
the Plaxis model, and each run row by row (bar count, diameter, radius of the bar circle, angle of
the first bar from the model X axis, bar top and bottom levels and length). A Revit / Dynamo script
can place the bars from this file alone; it only has to map Plaxis coordinates to the project base point.

Set each pile's **head level** to the slab soffit: results above it are FE peaks inside the slab.
Not yet included: links and shear, crack width, and a structural casing acting with the concrete
(the pile is then designed as reinforced concrete alone, which is conservative), and starter bars into the slab.

## Workbook format

One sheet per element and load combination, named `<Element>-<Combination>`:

| Element | Plaxis result | Actions used |
|---|---|---|
| `SPW` (sheet pile wall) | plate | N_1, N_2, Q_12, Q_23, Q_13, M_11, M_22, M_12 (per m) |
| `Combi Wall` | beam + embedded beam | N, Q_12, Q_13, M_1, M_2, M_3 |
| `Pile(n)` | embedded beam | N, Q_12, Q_13, M_1, M_2, M_3 |
| `Deck` | plate | as SPW |
| `Front Beam`, `Rear Beam` | plate | as SPW |

Combinations: `PT-B-*` / `PT-C-*` are ULS (Set B / Set C), `QP` is quasi-permanent SLS
(crack width only). Seismic and accidental sheets are recognised by name.

## What the import does automatically

- Reads `.xlsb`, `.xlsx` and `.xlsm`.
- Removes blank rows and the repeated header in the middle of combi wall sheets.
- Ignores the Plaxis element-name column (it is inconsistent) and identifies points by node number and coordinates.
- Removes repeated node rows, and treats `N/A` cells as empty.
- Ignores empty sheets such as `Portal Frame`.

## What it flags

| Severity | Check |
|---|---|
| Error | no header, missing force columns, text in number cells, empty force cells, a node with two different coordinates, two sheets for the same element and combination |
| Warning | two combinations of one element with identical forces (copy-paste), a combination that sibling elements have but this one lacks, no QP sheet, different node sets between combinations, values outside their own min/max, unexpected units, unknown sheet names |

## Design conventions already in place

- Design uses the phase value of each action, not the Plaxis min/max envelope.
- Concrete elements get `N × −1` (AdSec's sign convention is opposite to Plaxis).
- Combi wall: concrete filled down to −25 m (project input). Above that the actions are split between the steel tube and the concrete core by flexural stiffness (E·I, with the corroded steel section). Below it the steel carries everything.
