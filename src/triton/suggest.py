"""Suggested sheet mapping: what a sheet with an odd name most likely holds.

The workbook's sheets are meant to be named ``<Element>-<Combination>`` (``Pile(1)-PT-B-Apron``),
but they are typed by hand: ``Pile 1 - PTB Apron``, ``Frnt Beam-QP``, ``Pile(2)-PT-B-Aprn``. For each
sheet that is not recognised, and each recognised one whose combination is spelled differently
from the same combination on other sheets, this suggests an element and a combination in the
spelling the rest of the workbook uses. The user accepts or corrects the suggestions; nothing is
applied without them.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import get_close_matches
from typing import Any

# Canonical element names, and the ways people write them (matched on a lower-case name with
# separators turned into spaces). "{n}" is the element's number.
_ELEMENTS: list[tuple[str, re.Pattern[str]]] = [
    ("Pile({n})", re.compile(r"^(?:piles?|pl|p)\s*\(?\s*(\d+)\s*\)?$")),
    ("Combi Wall", re.compile(r"^(?:combi(?:ned)?|kombi)\s*(?:wall|wal|w)?$|^cw$")),
    ("SPW", re.compile(r"^(?:spw|sp\s*wall|sheet\s*piles?(?:\s*wall)?)$")),
    ("Deck", re.compile(r"^(?:deck|slab|deck\s*slab)$")),
    ("Front Beam", re.compile(r"^(?:front|frnt|fr|f)\s*(?:beam|bm|b)$|^fb$")),
    ("Rear Beam", re.compile(r"^(?:rear|back|rr|r)\s*(?:beam|bm|b)$|^rb$")),
    ("Transverse Beam({n})", re.compile(r"^(?:trans(?:verse)?|tr|t)\s*(?:beam|bm|b)\s*\(?\s*(\d+)?\s*\)?$")),
    ("Portal Frame", re.compile(r"^portal(?:\s*frame)?$")),
]
# For typos the patterns do not cover: compared with the name squashed to letters and digits.
_FUZZY = {
    "pile": "Pile({n})",
    "combiwall": "Combi Wall",
    "sheetpilewall": "SPW",
    "deck": "Deck",
    "frontbeam": "Front Beam",
    "rearbeam": "Rear Beam",
    "transversebeam": "Transverse Beam({n})",
    "portalframe": "Portal Frame",
}


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.,;:/\\]+", " ", text.lower())).strip()


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def element_of(text: str) -> tuple[str, bool] | None:
    """The canonical element a piece of a sheet name stands for, and whether it was an exact
    pattern (True) or a close spelling (False)."""
    flat = _flat(text)
    if not flat:
        return None
    for name, pattern in _ELEMENTS:
        m = pattern.match(flat)
        if m:
            number = next((g for g in m.groups() if g), None) if m.groups() else None
            if "{n}" in name:
                return (name.format(n=number) if number else name.replace("({n})", "")), True
            return name, True
    squashed = _squash(flat)
    number = re.search(r"(\d+)\s*\)?$", flat)
    letters = re.sub(r"\d+", "", squashed)
    close = get_close_matches(letters, _FUZZY, n=1, cutoff=0.75)
    if not close:
        return None
    name = _FUZZY[close[0]]
    if "{n}" in name:
        if not number:
            return None if name.startswith("Pile") else (name.replace("({n})", ""), False)
        return name.format(n=int(number.group(1))), False
    return name, False


def combination_key(text: str) -> tuple[str, ...] | None:
    """What a combination is, whatever its spelling: ("ULS", "B", "Apron"), ("QP",), ...;
    None when it is not recognisable."""
    flat = _flat(text)
    squashed = _squash(text)
    if not flat:
        return None
    words = flat.split()
    if re.search(r"\bqp\b|quasi|\bsls\b", flat) or squashed in ("qp", "sqp", "slsqp"):
        return ("QP",)
    if re.search(r"seis|\beq\b|earthquake", flat):
        return ("SEISMIC", *_location(words))
    if re.search(r"\bacc", flat):
        return ("ACCIDENTAL", *_location(words))
    letter = re.search(r"(?:^|[^a-z])(?:pt|set|uls|str)\s*([abc])(?![a-z])", flat) or re.search(
        r"^(?:pt|set)([abc])", squashed
    )
    if not letter:
        return None
    return ("ULS", letter.group(1).upper(), *_location(words))


def _location(words: list[str]) -> tuple[str, ...]:
    for w in words:
        close = get_close_matches(w, ("apron", "yard"), n=1, cutoff=0.6)
        if close and len(w) >= 3:
            return (close[0].capitalize(),)
    return ()


def _spell(key: tuple[str, ...]) -> str:
    if key[0] == "QP":
        return "QP"
    if key[0] == "ULS":
        return "-".join(["PT", *key[1:]])
    return "-".join([key[0].capitalize(), *key[1:]])


def _best_split(name: str) -> tuple[str, bool, tuple[str, ...], str] | None:
    """Try every place the name could split into element and combination."""
    cleaned = name.strip()
    cuts = [m.start() for m in re.finditer(r"[\s_\-]", cleaned)]
    best = None
    for pos in cuts:
        left, right = cleaned[:pos], cleaned[pos + 1 :]
        element = element_of(left)
        key = combination_key(right)
        if element and key:
            score = (element[1], len(left))  # exact before close, then the longest element text
            if best is None or score > best[0]:
                best = (score, (element[0], element[1], key, right.strip(" -_")))
    return best[1] if best else None


def suggest(sheets: list[dict[str, Any]], section_elements: list[str] = ()) -> dict[str, dict[str, Any]]:
    """Suggestions by sheet name, from the workbook's sheet summaries (name, element, combination)."""
    # The spelling the workbook already uses for each combination and element.
    spellings: dict[tuple[str, ...], Counter] = {}
    for s in sheets:
        if s.get("combination") and not s.get("empty"):
            key = combination_key(s["combination"])
            if key:
                spellings.setdefault(key, Counter())[s["combination"]] += 1
    preferred = {}
    for key, counts in spellings.items():
        top = max(counts.values())
        tied = [c for c, n in counts.items() if n == top]
        preferred[key] = _spell(key) if _spell(key) in tied else sorted(tied)[0]
    known_elements = {e for e in section_elements} | {s["element"] for s in sheets if s.get("element")}

    def element_spelling(canonical: str) -> str:
        for e in known_elements:
            if _squash(e) == _squash(canonical):
                return e
        return canonical

    out: dict[str, dict[str, Any]] = {}
    for s in sheets:
        if s.get("empty"):
            continue
        name = s["name"]
        if s.get("element"):
            # Recognised, but is its combination spelled like the same combination elsewhere?
            key = combination_key(s["combination"])
            want = preferred.get(key) if key else None
            if want and want != s["combination"]:
                out[name] = {
                    "element": s["element"],
                    "combination": want,
                    "sure": True,
                    "why": f"“{s['combination']}” is spelled “{want}” on the other sheets",
                }
            continue
        found = _best_split(name)
        if not found:
            continue
        element, exact, key, typed = found
        combination = preferred.get(key) or _spell(key)
        why = []
        if not exact:
            why.append("element name read as a close spelling")
        if _squash(typed) != _squash(combination):
            why.append(f"“{typed}” read as {combination}")
        out[name] = {
            "element": element_spelling(element),
            "combination": combination,
            "sure": exact and key in preferred,
            "why": "; ".join(why) or "name written differently",
        }
    return out
