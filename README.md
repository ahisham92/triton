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

## Pile design (N–M)

Upload the workbook on the project's **Workbook** tab, then press **Design piles** on the **Design** tab.
For each pile element Triton takes every ULS result (all piles of the row, every node below the pile
head level; QP is not used) and picks the arrangement of equal bars on one circle that carries all of
them with the least steel, or the lowest cost if that is the chosen objective.

- Section: EN 1992-1-1 parabola-rectangle concrete, bilinear steel with a horizontal top branch, strain
  limits of 6.1, displaced concrete deducted (the AdSec EC2 defaults). M is the resultant of M_2 and M_3.
- Utilisation is measured along the ray from the origin to each (N, M) point, taking the worse of two
  bar orientations (a bar on the bending axis, and midway between two bars).
- Seismic and accidental combinations use γc 1.2 and γs 1.0.
- Detailing: bars at least 16 mm, at least 6 bars, clear spacing between the project minimum and 200 mm
  (9.8.5), steel between Table 9.6N and 4% (9.5.2). Bars sit at cover + link + φ/2 from the face.
- Results: bars, utilisation, ρ and kg/m³, the governing point, the N–M chart with every load point,
  utilisation along the pile, and other bar sizes that also pass.

Set each pile's **head level** to the slab soffit: results above it are FE peaks inside the slab.
Not yet included: links and shear, crack width, and a structural casing acting with the concrete
(the pile is then designed as reinforced concrete alone, which is conservative).

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
