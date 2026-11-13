#!/usr/bin/env python3
"""
Built with love by ILoveTacos, based on kinksheet.com

build.py - Build index.html from template.html + checklist.txt.

Permanent item ids are kept in ids.json so share links keep working when
the list is reordered, extended, trimmed, or items are renamed with "<-".

Usage:
    python build.py           Build index.html (and update ids.json)
    python build.py --check   Validate only; write nothing
"""
__version__ = "1.0.1"  # Category options field accepts cross/same/closed; default column is "Your interest"

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "checklist.txt"
REGISTRY = HERE / "ids.json"
TEMPLATE = HERE / "template.html"
OUTPUT = HERE / "index.html"
PLACEHOLDER = "/*__CHECKLIST_DATA__*/null"
REGISTRY_FORMAT = 1
DEFAULT_COLUMN = "Your interest"

CAT_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def norm(label):
    """Normalize a label for identity: collapse spaces, ignore case."""
    return re.sub(r"\s+", " ", label).strip().casefold()


def parse(text):
    """Parse checklist.txt into categories. Returns (cats, errors)."""
    cats, errors, seen = [], [], {}
    cur = None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = CAT_RE.match(line)
        if m:
            parts = [p.strip() for p in m.group(2).split("|")]
            title = parts[0] or m.group(1).strip()
            col_text = parts[1] if len(parts) > 1 else ""
            cols = [c.strip() for c in col_text.split(",") if c.strip()] or [DEFAULT_COLUMN]
            if len(cols) > 2:
                errors.append(f"Line {ln}: a category can have at most 2 columns")
                cols = cols[:2]

            # Options: any of cross / same / closed, space or comma separated.
            match, closed = "", False
            for opt in parts[2:]:
                for tok in opt.replace(",", " ").lower().split():
                    if tok in ("cross", "same"):
                        match = tok
                    elif tok == "closed":
                        closed = True
                    else:
                        errors.append(f"Line {ln}: unknown option '{tok}' (use cross, same, closed)")
            if not match:
                match = "cross" if len(cols) == 2 else "same"

            cur = {"key": m.group(1).strip(), "title": title, "cols": cols,
                   "match": match, "closed": closed, "items": []}
            cats.append(cur)
            continue

        if cur is None:
            errors.append(f"Line {ln}: item appears before any [category] line")
            continue

        parts = [p.strip() for p in line.split("|")]
        head = parts[0]
        desc = " | ".join(p for p in parts[1:] if p)
        old = None
        if "<-" in head:
            head, old = [s.strip() for s in head.split("<-", 1)]
            if not old:
                errors.append(f"Line {ln}: '<-' with no old label")
                old = None
        if not head:
            errors.append(f"Line {ln}: missing label")
            continue

        key = norm(head)
        if key in seen:
            errors.append(f"Line {ln}: duplicate label '{head}' (also on line {seen[key]})")
            continue
        seen[key] = ln
        cur["items"].append({"label": head, "desc": desc, "old": old, "line": ln})

    for c in cats:
        if not c["items"]:
            errors.append(f"Category '{c['title']}' has no items")
    return cats, errors


def load_registry():
    if not REGISTRY.exists():
        return {"format": REGISTRY_FORMAT, "next_id": 1, "ids": {}, "renamed": []}
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(f"ERROR: cannot read {REGISTRY.name}: {exc}")
    if reg.get("format") != REGISTRY_FORMAT or not isinstance(reg.get("ids"), dict):
        sys.exit(f"ERROR: {REGISTRY.name} has an unexpected format")
    reg.setdefault("renamed", [])
    used = list(reg["ids"].values())
    reg["next_id"] = max([reg.get("next_id", 1)] + [i + 1 for i in used])
    return reg


def assign_ids(cats, reg):
    """Apply renames, give new items fresh ids. Returns (notes, errors)."""
    ids = reg["ids"]
    notes, errors = [], []
    active = {norm(it["label"]) for c in cats for it in c["items"]}

    # Renames first, so the old id moves to the new label.
    for c in cats:
        for it in c["items"]:
            if not it["old"]:
                continue
            new_k, old_k = norm(it["label"]), norm(it["old"])
            if new_k in ids:
                continue  # already applied in an earlier build
            if old_k in active:
                errors.append(f"Line {it['line']}: rename source '{it['old']}' is still in the list")
            elif old_k in ids:
                ids[new_k] = ids.pop(old_k)
                reg["renamed"].append({"from": it["old"], "to": it["label"], "id": ids[new_k],
                                       "date": datetime.now().strftime("%Y-%m-%d")})
                notes.append(f"Renamed: '{it['old']}' -> '{it['label']}' (id {ids[new_k]})")
            else:
                notes.append(f"Line {it['line']}: rename source '{it['old']}' not found; "
                             f"'{it['label']}' added as a new item")

    # New items get the next unused id (ids are never reused).
    for c in cats:
        for it in c["items"]:
            k = norm(it["label"])
            if k not in ids:
                ids[k] = reg["next_id"]
                reg["next_id"] += 1
                notes.append(f"New: '{it['label']}' (id {ids[k]})")
            it["id"] = ids[k]

    # Safety check: no two active items may share an id.
    by_id = {}
    for c in cats:
        for it in c["items"]:
            if it["id"] in by_id:
                errors.append(f"Items '{by_id[it['id']]}' and '{it['label']}' share id {it['id']} "
                              f"(was ids.json edited by hand?)")
            by_id[it["id"]] = it["label"]

    retired = len(set(ids.values()) - set(by_id))
    if retired:
        notes.append(f"{retired} retired item(s) kept in registry (not shown on the page)")
    return notes, errors


def build_payload(cats):
    return {
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cats": [
            {"key": c["key"], "title": c["title"], "cols": c["cols"], "match": c["match"],
             "closed": c["closed"],
             "items": [{"id": it["id"], "label": it["label"], "desc": it["desc"]}
                       for it in c["items"]]}
            for c in cats
        ],
    }


def main():
    try:
        sys.stdout.reconfigure(errors="replace")  # avoid crashes on Windows consoles
    except AttributeError:
        pass

    ap = argparse.ArgumentParser(description="Build the checklist page.")
    ap.add_argument("--check", action="store_true", help="validate only; write nothing")
    args = ap.parse_args()

    for f in (SOURCE, TEMPLATE):
        if not f.exists():
            sys.exit(f"ERROR: {f.name} not found next to build.py")

    cats, errors = parse(SOURCE.read_text(encoding="utf-8"))
    if errors:
        print("Problems in checklist.txt:")
        for e in errors:
            print("  - " + e)
        sys.exit(1)

    reg = load_registry()
    notes, errors = assign_ids(cats, reg)
    for n in notes:
        print("  " + n)
    if errors:
        print("Build stopped:")
        for e in errors:
            print("  - " + e)
        sys.exit(1)

    count = sum(len(c["items"]) for c in cats)
    max_id = max(it["id"] for c in cats for it in c["items"])
    print(f"OK: {len(cats)} categories, {count} items, longest link data ~{max_id} chars")

    if args.check:
        print("Check only; nothing written.")
        return

    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        sys.exit(f"ERROR: placeholder {PLACEHOLDER} not found in {TEMPLATE.name}")
    payload = json.dumps(build_payload(cats), ensure_ascii=False).replace("</", "<\\/")
    OUTPUT.write_text(template.replace(PLACEHOLDER, payload), encoding="utf-8")

    if REGISTRY.exists():
        shutil.copyfile(REGISTRY, REGISTRY.with_suffix(".json.bak"))
    REGISTRY.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {OUTPUT.name} and {REGISTRY.name}")


if __name__ == "__main__":
    main()
