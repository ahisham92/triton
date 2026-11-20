"""A made-up SAP2000 results file: a piled jetty with its deck, for trying the results film
without a model of one's own (and for the tests). The numbers are shaped like a real analysis
(tributary reactions, a berthing push that bends the piles) but come from no analysis at all."""

from __future__ import annotations

import math
from pathlib import Path

LENGTH, WIDTH, BAY = 60.0, 24.0, 6.0
TOE, HEAD, DECK = -32.0, 2.5, 3.5
SEABED = -16.0


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def write(path: Path) -> Path:
    """Write the sample as SAP2000 text tables (.s2k) at ``path``."""
    out: list[str] = ["File C:\\Models\\Sample jetty.$2k was saved on 11/20/26 at 10:00:00", ""]

    def table(name: str, rows: list[dict]) -> None:
        out.append(f'TABLE:  "{name}"')
        for r in rows:
            parts = [
                f'{k}="{v}"'
                if isinstance(v, str) and " " in v
                else f"{k}={_fmt(v) if isinstance(v, float) else v}"
                for k, v in r.items()
            ]
            line = "   " + "   ".join(parts)
            if len(line) > 200:  # SAP2000 wraps long records with " _"
                cut = line.rfind("   ", 0, 180)
                out.append(line[:cut] + " _")
                out.append("        " + line[cut:].lstrip())
            else:
                out.append(line)
        out.append("")

    table("PROGRAM CONTROL", [{"ProgramName": "SAP2000", "Version": "24.0.0", "CurrUnits": "KN, m, C"}])

    nx, ny = int(LENGTH / BAY) + 1, int(WIDTH / BAY) + 1
    joints: list[dict] = []
    jid = 0
    heads: dict[tuple[int, int], int] = {}
    pile_nodes: dict[tuple[int, int], list[int]] = {}
    levels = [TOE + k * 3.0 for k in range(int((HEAD - TOE) / 3.0) + 1)]
    if levels[-1] < HEAD:
        levels.append(HEAD)
    xyz: dict[int, tuple[float, float, float]] = {}
    for i in range(nx):
        for j in range(ny):
            ids = []
            for z in levels:
                jid += 1
                xyz[jid] = (i * BAY, j * BAY, z)
                ids.append(jid)
            pile_nodes[(i, j)] = ids
            jid += 1
            xyz[jid] = (i * BAY, j * BAY, DECK)
            heads[(i, j)] = jid
    # deck mid nodes for 3 m shells
    deck: dict[tuple[int, int], int] = {}
    for a in range(2 * (nx - 1) + 1):
        for b in range(2 * (ny - 1) + 1):
            if a % 2 == 0 and b % 2 == 0:
                deck[(a, b)] = heads[(a // 2, b // 2)]
            else:
                jid += 1
                xyz[jid] = (a * BAY / 2, b * BAY / 2, DECK)
                deck[(a, b)] = jid
    for k, (x, y, z) in xyz.items():
        joints.append(
            {
                "Joint": k,
                "CoordSys": "GLOBAL",
                "CoordType": "Cartesian",
                "XorR": x,
                "Y": y,
                "Z": z,
                "SpecialJt": "No",
                "GlobalX": x,
                "GlobalY": y,
                "GlobalZ": z,
            }
        )
    table("JOINT COORDINATES", joints)

    frames: list[dict] = []
    sect: list[dict] = []
    fid = 0
    kinds: dict[int, tuple[str, tuple[int, int], float]] = {}
    for (i, j), ids in pile_nodes.items():
        chain = ids + [heads[(i, j)]]
        for a, b in zip(chain, chain[1:], strict=False):
            fid += 1
            frames.append({"Frame": fid, "JointI": a, "JointJ": b, "IsCurved": "No"})
            sect.append(
                {
                    "Frame": fid,
                    "SectionType": "Pipe",
                    "AutoSelect": "N.A.",
                    "AnalSect": "PILE1219x22",
                    "MatProp": "Default",
                }
            )
            kinds[fid] = ("pile", (i, j), (xyz[a][2] + xyz[b][2]) / 2)
    for j in range(ny):
        for i in range(nx - 1):
            fid += 1
            frames.append(
                {"Frame": fid, "JointI": heads[(i, j)], "JointJ": heads[(i + 1, j)], "IsCurved": "No"}
            )
            name = "FRONT1500x2000" if j == ny - 1 else "BEAM1200x1500"
            sect.append(
                {
                    "Frame": fid,
                    "SectionType": "Rectangular",
                    "AutoSelect": "N.A.",
                    "AnalSect": name,
                    "MatProp": "C40",
                }
            )
            kinds[fid] = ("beamx", (i, j), DECK)
    for i in range(nx):
        for j in range(ny - 1):
            fid += 1
            frames.append(
                {"Frame": fid, "JointI": heads[(i, j)], "JointJ": heads[(i, j + 1)], "IsCurved": "No"}
            )
            sect.append(
                {
                    "Frame": fid,
                    "SectionType": "Rectangular",
                    "AutoSelect": "N.A.",
                    "AnalSect": "BEAM1200x1500",
                    "MatProp": "C40",
                }
            )
            kinds[fid] = ("beamy", (i, j), DECK)
    table("CONNECTIVITY - FRAME", frames)
    table("FRAME SECTION ASSIGNMENTS", sect)
    table(
        "FRAME SECTION PROPERTIES 01 - GENERAL",
        [
            {"SectionName": "PILE1219x22", "Material": "S355", "Shape": "Pipe", "t3": 1.219, "tw": 0.022},
            {"SectionName": "BEAM1200x1500", "Material": "C40", "Shape": "Rectangular", "t3": 1.5, "t2": 1.2},
            {
                "SectionName": "FRONT1500x2000",
                "Material": "C40",
                "Shape": "Rectangular",
                "t3": 2.0,
                "t2": 1.5,
            },
        ],
    )
    areas: list[dict] = []
    aid = 0
    for a in range(2 * (nx - 1)):
        for b in range(2 * (ny - 1)):
            aid += 1
            areas.append(
                {
                    "Area": aid,
                    "NumJoints": 4,
                    "Joint1": deck[(a, b)],
                    "Joint2": deck[(a + 1, b)],
                    "Joint3": deck[(a + 1, b + 1)],
                    "Joint4": deck[(a, b + 1)],
                }
            )
    table("CONNECTIVITY - AREA", areas)
    table("AREA SECTION ASSIGNMENTS", [{"Area": k + 1, "Section": "DECK500"} for k in range(aid)])
    table(
        "AREA SECTION PROPERTIES",
        [
            {
                "Section": "DECK500",
                "Material": "C40",
                "AreaType": "Shell",
                "Type": "Shell-Thick",
                "Thickness": 0.5,
            }
        ],
    )
    toes = [ids[0] for ids in pile_nodes.values()]
    table(
        "JOINT RESTRAINT ASSIGNMENTS",
        [
            {"Joint": t, "U1": "Yes", "U2": "Yes", "U3": "Yes", "R1": "No", "R2": "No", "R3": "No"}
            for t in toes
        ],
    )

    pats = [
        ("DEAD", "Dead", 1.0),
        ("SDL", "Super Dead", 0.0),
        ("LIVE", "Live", 0.0),
        ("BERTHING", "Other", 0.0),
        ("MOORING", "Other", 0.0),
        ("CRANE", "Live", 0.0),
    ]
    table("LOAD PATTERN DEFINITIONS", [{"LoadPat": p, "DesignType": t, "SelfWtMult": s} for p, t, s in pats])
    table(
        "AREA LOADS - UNIFORM",
        [
            {"Area": k + 1, "LoadPat": "SDL", "CoordSys": "GLOBAL", "Dir": "Gravity", "UnifLoad": 3.0}
            for k in range(aid)
        ]
        + [
            {"Area": k + 1, "LoadPat": "LIVE", "CoordSys": "GLOBAL", "Dir": "Gravity", "UnifLoad": 40.0}
            for k in range(aid)
        ],
    )
    front = [heads[(i, ny - 1)] for i in range(nx)]
    table(
        "JOINT LOADS - FORCE",
        [
            {
                "Joint": front[nx // 2],
                "LoadPat": "BERTHING",
                "CoordSys": "GLOBAL",
                "F1": 0.0,
                "F2": -1800.0,
                "F3": 0.0,
            }
        ]
        + [
            {
                "Joint": front[k],
                "LoadPat": "MOORING",
                "CoordSys": "GLOBAL",
                "F1": -300.0,
                "F2": 1200.0,
                "F3": 400.0,
            }
            for k in (1, nx // 2 + 2, nx - 2)
        ],
    )
    crane = [f for f, (kd, (i, j), _) in kinds.items() if kd == "beamx" and j in (ny - 1, ny - 3)]
    table(
        "FRAME LOADS - DISTRIBUTED",
        [
            {
                "Frame": f,
                "LoadPat": "CRANE",
                "CoordSys": "GLOBAL",
                "Type": "Force",
                "Dir": "Gravity",
                "DistType": "RelDist",
                "RelDistA": 0.0,
                "RelDistB": 1.0,
                "FOverLA": 250.0,
                "FOverLB": 250.0,
            }
            for f in crane
        ],
    )

    combos = {
        "ULS1 Gravity": {"DEAD": 1.35, "SDL": 1.35, "LIVE": 1.5, "CRANE": 1.5},
        "ULS2 Berthing": {"DEAD": 1.35, "SDL": 1.35, "LIVE": 1.05, "BERTHING": 1.5},
        "ULS3 Mooring": {"DEAD": 1.0, "SDL": 1.0, "MOORING": 1.5, "CRANE": 1.05},
        "SLS1 Characteristic": {"DEAD": 1.0, "SDL": 1.0, "LIVE": 1.0, "BERTHING": 1.0},
        "SLS2 Quasi-permanent": {"DEAD": 1.0, "SDL": 1.0, "LIVE": 0.6},
    }
    table(
        "COMBINATION DEFINITIONS",
        [
            {"ComboName": c, "ComboType": "Linear Add" if k == 0 else "", "CaseName": p, "ScaleFactor": f}
            for c, parts in combos.items()
            for k, (p, f) in enumerate(parts.items())
        ],
    )

    # the made-up response of each pattern: vertical share per pile, lateral push per pile
    area_per_pile = BAY * BAY
    vert = {
        "DEAD": 0.5 * 25 * area_per_pile + 15.0 * 35.5,
        "SDL": 3.0 * area_per_pile,
        "LIVE": 40.0 * area_per_pile,
        "CRANE": 0.0,
        "BERTHING": 0.0,
        "MOORING": 0.0,
    }
    n_piles = nx * ny

    def response(parts: dict[str, float]) -> dict:
        v = {
            (i, j): sum(
                f * vert[p] * (0.5 if i in (0, nx - 1) else 1.0) * (0.5 if j in (0, ny - 1) else 1.0)
                for p, f in parts.items()
            )
            for (i, j) in pile_nodes
        }
        for i, j in v:
            if j in (ny - 1, ny - 3):
                v[(i, j)] += parts.get("CRANE", 0) * 250.0 * BAY
        push = parts.get("BERTHING", 0) * -1800.0 + parts.get("MOORING", 0) * 3600.0
        pull_x = parts.get("MOORING", 0) * -900.0
        h = {
            (i, j): (
                pull_x / n_piles * (1 + 0.1 * j),
                push / n_piles * (1.6 - 0.12 * abs(i - nx // 2)) * (0.6 + 0.4 * j / (ny - 1)),
            )
            for (i, j) in pile_nodes
        }
        uplift = parts.get("MOORING", 0) * 400.0
        for i in (1, nx // 2 + 2, nx - 2):
            v[(i, ny - 1)] -= uplift * 3.2
        return {"v": v, "h": h}

    R = {c: response(p) for c, p in combos.items()}
    reactions, disps, forces, base = [], [], [], []
    for c in combos:
        r = R[c]
        for (i, j), ids in pile_nodes.items():
            hx, hy = r["h"][(i, j)]
            reactions.append(
                {
                    "Joint": ids[0],
                    "OutputCase": c,
                    "CaseType": "Combination",
                    "F1": -hx * 0.35,
                    "F2": -hy * 0.35,
                    "F3": r["v"][(i, j)],
                    "M1": 0.0,
                    "M2": 0.0,
                    "M3": 0.0,
                }
            )
        base.append(
            {
                "OutputCase": c,
                "CaseType": "Combination",
                "GlobalFX": sum(-h[0] for h in r["h"].values()),
                "GlobalFY": sum(-h[1] for h in r["h"].values()),
                "GlobalFZ": sum(r["v"].values()),
            }
        )
        kgrav = sum(r["v"].values()) / (n_piles * 4500.0)
        for k, (x, y, z) in xyz.items():
            hx = sum(h[0] for h in r["h"].values()) / n_piles
            hy = sum(h[1] for h in r["h"].values()) / n_piles
            s = max(0.0, (z - TOE) / (DECK - TOE)) ** 2
            twist = 1 + 0.25 * math.cos(math.pi * x / LENGTH)
            ux, uy = hx * 3.5e-4 * s, hy * 3.5e-4 * s * twist
            sag = 0.0
            if z == DECK:
                sag = (
                    -0.004
                    * kgrav
                    * math.sin(math.pi * ((x % BAY) / BAY))
                    * math.sin(math.pi * ((y % BAY) / BAY))
                )
            uz = -0.006 * kgrav * s + sag
            disps.append(
                {
                    "Joint": k,
                    "OutputCase": c,
                    "CaseType": "Combination",
                    "U1": ux,
                    "U2": uy,
                    "U3": uz,
                    "R1": 0.0,
                    "R2": 0.0,
                    "R3": 0.0,
                }
            )
        for f, (kd, (i, j), zm) in kinds.items():
            if kd == "pile":
                hx, hy = r["h"][(i, j)]
                arm = 0.5 + 0.5 * math.cos(math.pi * (zm - SEABED) / 9.0) if zm > SEABED - 9 else 0.1
                m2, m3 = hx * 6.0 * arm, hy * 6.0 * arm
                for st in (0.0, 3.0):
                    forces.append(
                        {
                            "Frame": f,
                            "Station": st,
                            "OutputCase": c,
                            "CaseType": "Combination",
                            "P": -r["v"][(i, j)] * (1 - 0.1 * (HEAD - zm) / (HEAD - TOE)),
                            "V2": hy,
                            "V3": hx,
                            "T": 0.0,
                            "M2": m2,
                            "M3": m3,
                        }
                    )
            else:
                share = (r["v"][(i, j)] / area_per_pile) * BAY * BAY / 8
                for st, kf in ((0.0, -1.0), (BAY / 2, 0.6), (BAY, -1.0)):
                    forces.append(
                        {
                            "Frame": f,
                            "Station": st,
                            "OutputCase": c,
                            "CaseType": "Combination",
                            "P": 0.0,
                            "V2": share / 2,
                            "V3": 0.0,
                            "T": 0.0,
                            "M2": 0.0,
                            "M3": share * kf,
                        }
                    )
    table("BASE REACTIONS", base)
    table("JOINT REACTIONS", reactions)
    table("JOINT DISPLACEMENTS", disps)
    table("ELEMENT FORCES - FRAMES", forces)

    design = []
    for f, (kd, (i, j), zm) in kinds.items():
        if kd != "pile":
            continue
        worst, combo = 0.0, ""
        for c in combos:
            r = R[c]
            hx, hy = r["h"][(i, j)]
            arm = 0.5 + 0.5 * math.cos(math.pi * (zm - SEABED) / 9.0) if zm > SEABED - 9 else 0.1
            m = math.hypot(hx, hy) * 6.0 * arm
            ratio = abs(r["v"][(i, j)]) / 8000.0 + m / 1800.0
            if ratio > worst:
                worst, combo = ratio, c
        design.append(
            {
                "Frame": f,
                "DesignSect": "PILE1219x22",
                "DesignType": "Column",
                "Status": "No Messages",
                "Ratio": round(worst, 4),
                "RatioType": "PMM",
                "Combo": combo,
            }
        )
    table("STEEL DESIGN 1 - SUMMARY DATA - EUROCODE 3-1-1:2005", design)
    out.append("END TABLE DATA")
    path.write_text("\n".join(out) + "\n", "utf-8")
    return path


# Where the sample's surroundings are, in its own axes (X along the jetty, Y out to sea): a rock
# breakwater seaward of the berth, and the land behind.
SURROUNDINGS = {
    "breakwaters": [
        {
            "name": "Main breakwater",
            "pts": [[-120.0, 170.0], [60.0, 210.0], [260.0, 150.0], [330.0, 40.0]],
            "crest": 6.0,
            "width": 10.0,
        }
    ],
    "land": [[[-150.0, -110.0], [220.0, -110.0], [220.0, -3.0], [-150.0, -3.0]]],
}
