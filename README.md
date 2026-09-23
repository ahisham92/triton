# Triton

Structural design of marine structure elements from Plaxis 3D straining actions.
Upload the geotechnical team's workbook and Triton checks it, designs each element
to Eurocode (EC2 / EC3) with BS 6349, and reports reinforcement and utilization ratios.

So far it covers **importing and checking the workbook**, **project setup** (sections, materials and design settings for each element) the **design of piles** (N–M cages, reductions down the pile, shear links) and the **combi wall** (reinforced infill and steel tube).

## Run it

```bash
pip install -e ".[dev]"
triton serve            # web app on http://127.0.0.1:8000
triton check "Section 01a.xlsb"   # same checks from the command line
pytest                  # tests
```

## Project setup

A project is split into **sections** (e.g. Section 01a, Section 02), each with its own Plaxis
workbook, elements, load multipliers and results. Materials and design settings are shared by
the whole project. Add sections on the **Sections** tab.

Each vertical element has its own **top level**: the slab soffit for a pile, the front beam soffit
for a combi wall. Results up to 100 mm above it (Design settings, *Results taken into the slab or
beam above*) are used and taken at the top level; higher results are FE peaks inside the
connection and are ignored.

Open the web app, create a project, pick a section, then either upload its workbook on the
**Workbook** tab and add every element it contains, or add elements by name (`Pile(5)`, `Deck`, …).
Each element gets its own inputs:

| Element | Inputs |
|---|---|
| Pile | diameter, cover, concrete, crack width limit, number of piles (default: counted from the workbook), top level (slab soffit), optional steel casing |
| Steel casing | top and bottom level, thickness, corrosion loss, steel grade, and its role: *crack width only* or *structural* (shares forces with the concrete by E·I) |
| Combi wall | tube diameter and thickness, corrosion loss, steel and infill grades, infill bottom level (default −25 m), top level (front beam soffit), infill cover and links, number of king piles, tube fabrication quality class |
| Sheet pile wall | section, steel grade, A / Wel / Wpl per m, class, corrosion loss per face |
| Slab | thickness, top and bottom cover, uniform or column and field strips, crack width limits for the top and bottom faces |
| Front / rear beam | width, depth, cover, crack width limits for the top and bottom faces (e.g. a soffit in the splash zone) |

**Project grades** (Design settings) apply to every element: concrete for piles, slabs and beams,
combi wall infill concrete, tube and casing steel, and sheet pile steel. An element can set its own
grade instead; left at *Project grade*, it follows the project value.

**Covers and corrosion** (Design settings) are project values too. Choosing the code for covers
(EN 1992-1-1 Table 4.4N, or BS 6349-1-4) or for corrosion (EN 1993-5 Table 4.2, or BS 6349-1-4) or
changing the design life fills them in; they stay editable, and an element's own value wins.

- EN 1992 covers: c_min,dur + 10 mm, structural class S4 at 50 years, S5 at 75, S6 at 100. Piles
  XS3 and at least 75 mm, combi wall infill XS2, slab top XS1, slab bottom and beams XS3.
- EN 1993-5 corrosion (per face, interpolated between the tabulated lives): casing and combi tube in
  the zone of high attack, sheet piles in the permanent immersion or intertidal zone.
- The BS 6349-1-4 tables are not loaded yet; choosing BS 6349 keeps the values that are set.

A **structural casing** can have bars welded to it at its top. Where the casing stops, the
connection zone (0.5 m by default, from the casing top upward, or down from the pile top when the
casing reaches it) is checked for N–M with no help from the casing: the welded bars at cover 0 plus
the head cage.

Pile links are unified by default: one spacing over the whole pile, the closest one needed
anywhere (Design settings, *Links along the pile*; *Zoned* spaces them by need instead).

Project-wide settings: partial factors, bar sizes to try, spacing limits, and whether the
reinforcement is chosen for the least steel or the lowest cost. Projects are saved as JSON
in `data/projects/`, with each section's checked workbook and results in
`data/projects/{project}/{section}/` (set `TRITON_DATA_DIR` to move it). Projects saved before
sections existed open with everything in one section; upload their workbook again.

### Working zone and isolated peaks

FE results near the model edges are unreliable. A section can set a **working zone** (Sections tab,
X and/or Y from and to, e.g. Y −14 to 14 in a model that runs from −16 to 16): results outside it
are not used for design. The number of piles is still counted from the whole workbook.

An **isolated peak** is a node whose resultant moment is more than the section's *peak ratio*
(default 1.5×) times both neighbours along the same pile, and at least 20% of that pile's largest
moment. Peaks above the top level + 100 mm are not reported. They are marked on the moment diagram
and listed under each element. The section setting uses them **as they are** or **averages** them
with the nodes either side; ticking *Leave out* on a peak drops that node for that combination at
the next design run.

### 3D view

The **3D view** tab draws the section from the workbook's node coordinates: piles and king piles as
lines, the deck, beams and sheet pile wall as panels. After a design run each pile is coloured by
its N–M utilisation per 0.5 m (green very safe, yellow, orange, red at 1.0, dark red above 1.0);
the combi wall takes the higher of the infill and the tube, and the tube alone below the infill.
Grey is not designed yet (outside the working zone, above the top level, or an element type not
designed yet). Alerts list anything unsafe, close to the limit (0.95 or more) or very safe (below
0.5). Picking an element shows it with the others faded and arrows for the directions of its
actions, from the workbook's direction check. Each element on the Design tab has the same view.
Drag to orbit, shift-drag or right-drag to pan, scroll to zoom; presets for 3D, plan, from the sea
and along the quay.

## Load multipliers

On the Workbook tab, add a multiplier (e.g. 1.35) and tick the combinations or single sheets it
applies to (e.g. the Set B sheets). Design multiplies every straining action of those sheets, phase
and min/max, and never X, Y or Z. A sheet can be in one multiplier only, and results list the
multipliers used.

## Pile design (N–M)

Upload the workbook on the project's **Workbook** tab, then press **Design piles and combi wall** on the **Design** tab.
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
  limits of 6.1, displaced concrete deducted (the AdSec EC2 defaults; Design settings can use the
  gross area instead). M is the resultant of M_2 and M_3.
- Checked against Ahmed's capacities sheet (1200 mm pile, C40/50, B500): with the sheet's αcc = 1.0,
  gross concrete area and bars 60 mm clear of the face, single-row curves agree within 0.2% on
  average and 2.3% at worst (`tests/test_capacity_sheet.py`).
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

### Shear and links

Shear to EN 1992-1-1 6.2 on the usual equivalent section of a circular pile: bw = D and
d = r + 2·rs/π (rs the bar circle radius), z = 0.9d, half the bars as tension steel. VEd is the
resultant of Q_12 and Q_13. Where the pile is in tension the concrete takes no shear (project rule),
so links carry it all. Circular hoops count as two legs of π/4 each: VRd,s = (π/2)·(Asw/s)·z·fywd·cot θ,
with cot θ up to 2.5 while VRd,max holds. Links follow 9.5.3: at least max(6 mm, φl/4), spacing at
most min(20·φl,min, D, 400 mm), and 0.6 of that for a length D below the slab and over laps of bars
above 14 mm. Spacings are rounded down to the spacing step and grouped into zones of at least 1 m.
The steel per pile and kg/m³ include the links.

### Export for Revit

**Download cages for Revit (JSON)** on the Design tab (`GET /api/projects/{id}/sections/{section}/design/cages.json`)
gives, for every pile element and combi wall infill (`part`: `pile` or `infill`): diameter, cover, link, head and toe levels, the X, Y of each pile in
the Plaxis model, and each run row by row (bar count, diameter, radius of the bar circle, angle of
the first bar from the model X axis, bar top and bottom levels and length). A Revit / Dynamo script
can place the bars from this file alone; it only has to map Plaxis coordinates to the project base point.

Set each pile's **top level** to the slab soffit (see Project setup). The results give the steel
for all piles of each type.
### Crack width

QP crack widths to EN 1992-1-1 7.3.4 at the extreme bar, from the elastic cracked circle (no
concrete tension, Ec,eff = Ecm/(1 + φ)) with a bar on the bending axis. Ac,eff is the circular
segment of depth hc,ef at the tension face, or a ring round the pile when it is all in tension
(k2 then from the extreme strains). sr,max uses the outer row's spacing round its circle. There is
no check inside a steel casing. The cage search and the curtailment both keep wk within the
pile's limit, so a cage can be heavier than strength alone needs.

Not yet included: a structural casing acting with the concrete (the pile is then designed as
reinforced concrete alone, which is conservative), and starter bars into the slab.

### Governing sets for AdSec

**Download governing sets for AdSec (Excel)** on the Design tab
(`GET /api/projects/{id}/sections/{section}/design/governing.xlsx`) is laid out for copy and paste.
Maxima and minima are taken over all combinations of the element, not per combination, and every
row names the combination it comes from.

*Concrete* sheet: for each pile and combi wall infill (and each station, a length with one cage,
when the reinforcement changes down the element), the element name, then 7 QP rows and 7 ULS rows
underneath, each with the criterion, N, M2, M3 and the combination. The seven are max and min N,
M2 and M3 with the other actions at the same point, and the most utilised point (ULS: highest N–M
utilisation with the station's cage, kept even when it repeats one of the six; QP: largest
resultant moment until crack width is checked). N is in the concrete (AdSec) sign convention,
Plaxis N × −1. Points inside a steel casing are left out of the QP rows; where nothing is left
(the combi wall infill, a station inside the casing) the QP rows are 1s.

*Steel* sheet: for the combi wall tube (its share of the actions) and the sheet pile wall, 10 ULS
rows each: max and min N, M2, M3, Q1 and Q2 with the other actions at the same point, in the
Plaxis sign. There is no most utilised row, as steel is not designed with these sets. For the
sheet pile wall (a plate) N, M2, M3, Q1 and Q2 are N_1, M_11, M_22, Q_13 and Q_23.

## Sheet pile wall

Triton does not design the sheet pile wall; the office uses the ArcelorMittal program. The Design
tab lists its 10 governing rows. **Download SPW straining actions (Excel)**
(`GET /api/projects/{id}/sections/{section}/spw.xlsx`) gives the plate results with the section's
load multipliers applied and N in the Plaxis sign (steel element): a *Governing* sheet with the same
10 rows, and one *Envelope* sheet per combination with the maximum and minimum across the wall at
each level.

## Combi wall

Between the front beam soffit and the infill bottom level (default −25 m) every straining action,
axial force included, is shared between the steel tube and the concrete infill by E·I, using the
corroded tube and the infill grade's Ecm. For a 1626 × 18 mm tube with 3 mm corrosion and C32/40
that is 67% to the infill. Below the infill the tube carries everything.

- **Infill:** a circular reinforced concrete section of the tube's inner diameter, designed exactly
  like a pile (N–M cage, reductions down the length, links), from the front beam soffit to the infill
  bottom. There is no crack width check, because the tube is a permanent casing.
- **Tube, filled part:** full plastic resistance, whatever its D/t (EN 1993-5 5.5.4(9)), with
  M_N,Rd = M_pl,Rd cos(πn/2) and the 6.2.8 shear reduction.
- **Tube, below the infill:** class from EN 1993-1-1 Table 5.2 on the corroded section. Classes 1 and 2
  are checked plastically and class 3 elastically. Class 4 is checked elastically plus for meridional
  shell buckling to EN 1993-1-6 Annex D.1.2 (C_x = 1, fabrication quality class A/B/C, γM1 = 1.1),
  as EN 1993-5 5.5.4(7) refers tubes to EN 1993-1-6.
- **Not yet included:** shell buckling under shear, and forces from the secondary sheet piles.

## Beams (front, rear, transverse)

The beams are plate strips in Plaxis. Triton turns them into beam section forces at stations along
the beam. At each station it fits the plate results across the width, over ±0.8 m along the beam:
N, vertical bending, horizontal bending (from the in-plane force varying across the width),
vertical and horizontal shear, and torsion (twisting moments plus the vertical shear's lever arm).
The span direction is the longer plan extent, and the local axis along it comes from the directions
check. Piles and king piles that reach the beam are supports: results inside them are left out,
bending is taken at their faces, and shear is taken at d (or 2d) from them, or midway when supports
are closer than that.

- **Cage:** one longitudinal cage for the whole beam, with top, bottom and side bars. It is checked
  for N with biaxial bending by EN 1992-1-1 5.8.9(4), using N–M curves about each axis for both
  senses. The minimum steel is 9.2.1.1. The cheapest bars per face come first, and the face that
  governs is stepped up.
- **Crack widths:** 7.3.4 under QP loads at the top and bottom faces, each against its own limit.
  Stresses come from the cracked section with Ec,eff = Ecm/(1 + φ).
- **Temperature and shrinkage restraint:** EN 1992-3 Annex M / CIRIA C660. The restrained strain is
  K1·R·(α(T1 + T2) + εca), minus half the tensile strain capacity, over sr,max at each face. R comes
  from the length between joints over the depth (ACI 207.2R), unless it is entered. T1, T2, α, K1
  and φ are in Design settings under Cracking and restraint.
- **Links:** one arrangement for the whole beam. They cover vertical shear with torsion (6.2, 6.3.2),
  with σcp from compression and no concrete contribution in tension. They also cover horizontal
  shear, the transverse shear per metre, and the 9.2.2 minimum and spacing rules.
- **Transverse bars:** top and bottom bars per metre from the transverse moments at each node,
  with their own QP crack widths.
- Plate moment sign: positive M11/M22 is sagging by default (Design settings). In the sample the
  deck's M11 peaks negative at every pile head.
- **Not yet included:** fender and bollard reinforcement, and the torsion longitudinal steel added
  to the bending steel (it is reported).

## Slab (deck)

The deck is designed per metre from the plate results at every node, in the global directions
from the directions check (bars along X take Mx). Results inside pile heads are left out.

- **Bending:** Wood–Armer moments from Mx, My and Mxy for the four layers (bottom and top, along X
  and along Y), with the in-plane N of each direction. Where K exceeds K' = 0.167 the opposite
  face's bars are designed as compression steel. Each bar option is taken at its own depth, so a
  second layer or a bigger bar counts for less.
- **Mesh and additional bars:** four meshes, bottom and top, along X and along Y, each laid over
  the whole slab. The slab is split into a grid of cells (1 m by default) only to find where a mesh
  is not enough: there, additional bars go between the mesh bars (at the mesh spacing or every
  second gap), sized for strength, the QP crack width at that face (Ø and spacing of the mix,
  7.12) and restraint cracking. Each mesh is the one with the least steel overall, or the mesh you
  enter (value engineering). For each layer you choose a mesh with additional bars (default) or a
  mesh only, strong enough everywhere. Additional bars run in zones at least 2.5 m long (setting):
  shorter pieces take their heavier neighbour's bars, and matching runs merge into rectangles.
  Bars are Ø10 to Ø32; a second mesh layer only for Ø25 and up.
- **Moments at the pile faces:** designed as they are, or averaged over a ring one pile diameter
  wide round each pile, per combination (slab setting).
- **Mobile crane:** areas with the extra factored actions from the SAP model (factored crane minus
  factored live load, M, V, N per metre) are added to every ULS combination over each area, so
  the governing combination carries them. Pile reactions for punching stay as Plaxis gives them.
- **Column and field strips (option):** the need is averaged across each strip, the column strip
  being a quarter of the pile spacing each side of a pile line.
- **Shear per metre:** v = √(Vx² + Vy²) from d (or 2d) off the pile faces, and at least 2d where
  punching governs. No concrete contribution where the slab is in tension; links are given per
  cell as Ø @ s × s.
- **Punching (6.4):** at every pile head not under a beam, from the pile face (vRd,max = 0.4·ν·fcd)
  out to u1 = π(D + 4d) at 2d, nothing inside the pile. β = 1 + 0.6π·e/(D + 4d) with the pile force
  and moment at the slab soffit, as in the pile design. Links per perimeter by 6.52 out to u_out.
  The thickness is the slab's, a slab-wide punching thickness, or one entered per pile (slopes).
  Each pile has a plan and a section drawing of its perimeters and links.
- **Restraint:** the basic mesh at each face against temperature and shrinkage cracking, as for the
  beams, with R from the joint spacing over the thickness.
- The utilisation heat map in 3D shows, per cell, the bending steel needed over the bars given.

## Workbook format

One sheet per element and load combination, named `<Element>-<Combination>`:

| Element | Plaxis result | Actions used |
|---|---|---|
| `SPW` (sheet pile wall) | plate | N_1, N_2, Q_12, Q_23, Q_13, M_11, M_22, M_12 (per m) |
| `Combi Wall` | beam + embedded beam | N, Q_12, Q_13, M_1, M_2, M_3 |
| `Pile(n)` | embedded beam | N, Q_12, Q_13, M_1, M_2, M_3 |
| `Deck` | plate | as SPW |
| `Front Beam`, `Rear Beam`, `Transverse Beam` (optional, also `Trans Beam(1)`) | plate | as SPW |

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
| Warning | two combinations of one element with identical forces (copy-paste), a combination that sibling elements have but this one lacks, no QP sheet, different node sets between combinations, values outside their own min/max, unexpected units, unknown sheet names, directions of the actions that the results do not confirm, or elements of one type whose directions disagree |

### Directions of the actions

The workbook has Plaxis local-axis actions but not the axes. The Workbook tab works them out per
element from equilibrium and shows the evidence:

- Beams (piles, combi wall): along the member dM3/dz follows Q12 and dM2/dz follows Q13; the larger
  moment is the main bending, taken as across the quay line.
- Plates (deck, beams): Q13 = dM11/dx1 + dM12/dx2 and Q23 = dM12/dx1 + dM22/dx2, with the gradients
  fitted from neighbouring nodes; the global axis for local 1 that makes the shears fit is the
  model's. N1 and N2 act along the same axes.
- Walls whose moments are too small to tell (the sheet piles between king piles): the in-plane force
  that builds up steadily with depth is the vertical one.

The quay line is the longer horizontal extent of the walls (else of the beams). Rank correlations
are used, so single FE spikes do not decide it.

## Design conventions already in place

- Design uses the phase value of each action, not the Plaxis min/max envelope.
- Concrete elements get `N × −1` (AdSec's sign convention is opposite to Plaxis).
- Combi wall: concrete filled down to −25 m (project input). Above that the actions are split between the steel tube and the concrete core by flexural stiffness (E·I, with the corroded steel section). Below it the steel carries everything.
