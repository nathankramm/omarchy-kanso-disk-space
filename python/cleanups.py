"""cleanups.jsonl: one line per cleanup, appended by the user's agent (the
skill's rule 7). The engine only ever reads it (D36).

Every field is required:
    t                ISO 8601 time with a zone offset
    categories       [category id, ...], at least one
    commands         [command as run, ...], at least one: every command that
                     changed the disk, never a dry run
    paths            [absolute path, ...], at least one: what those commands
                     removed, trashed or vacuumed
    avail_before     int >= 0: `df --output=avail` after a btrfs sync, before
                     the first command
    avail_after      int >= 0: the same, after the last command
    freed_bytes      int: exactly avail_after - avail_before
    estimated_bytes  int >= 0: the report's figure for what was cleaned
Other fields are ignored. A line that breaks any rule is skipped with its
reason: never trusted, never repaired, never fatal.
"""

import datetime
import json

import categories
import units

FIELDS = ("t", "categories", "commands", "paths", "avail_before", "avail_after",
          "freed_bytes", "estimated_bytes")  # fmt: skip


def strings(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(s, str) and s.strip() and "\0" not in s for s in value)
    )


def check(row):
    """(entry, None) for a valid line, else (None, reason)."""
    if not isinstance(row, dict):
        return None, "not a JSON object"
    missing = [k for k in FIELDS if k not in row]
    if missing:
        return None, "missing " + ", ".join(missing)
    try:
        t = datetime.datetime.fromisoformat(row["t"])
    except (TypeError, ValueError):
        return None, "t is not an ISO time"
    if t.tzinfo is None:
        return None, "t has no zone"
    for key in ("categories", "commands", "paths"):
        if not strings(row[key]):
            return None, f"{key} is not a list of strings"
    if not all(p.startswith("/") for p in row["paths"]):
        return None, "paths are not all absolute"
    for key in ("avail_before", "avail_after", "estimated_bytes"):
        if not units.whole(row[key], 0):
            return None, f"{key} is not a byte count"
    if not units.whole(row["freed_bytes"]):
        return None, "freed_bytes is not a byte count"
    if row["freed_bytes"] != row["avail_after"] - row["avail_before"]:
        return None, "freed_bytes is not avail_after - avail_before"
    return {
        "t": int(t.timestamp()),
        "categories": row["categories"],
        "commands": row["commands"],
        "paths": row["paths"],
        "avail_before": row["avail_before"],
        "avail_after": row["avail_after"],
        "freed": row["freed_bytes"],
        "estimated": row["estimated_bytes"],
    }, None


def parse(text):
    """(entries in file order, skipped [{line, reason}]); blank lines are
    neither."""
    entries, skipped = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            skipped.append({"line": n, "reason": "not JSON"})
            continue
        entry, reason = check(row)
        if entry is None:
            skipped.append({"line": n, "reason": reason})
        else:
            entries.append(entry)
    return entries, skipped


def under(path, top):
    return path == top or path.startswith(top.rstrip("/") + "/")


def expected(entry, cats):
    """The plugin's own figure for what a logged cleanup should free (D111),
    from the scan before it: each logged category's exact figure when the
    logged paths cover all it measured, else the sum of the logged folders'
    own figures (D99); None when any of them has no exact figure, and the
    agent's estimated_bytes is then the only one."""
    by_id = {c["id"]: c for c in cats}
    total = 0
    for cid in entry["categories"]:
        c = by_id.get(cid)
        if not c or c["state"] != "ok" or not c["exact"] or c["reclaimable"] is None:
            return None
        paths = categories.paths_of(c)
        logged = [p for p in entry["paths"]
                  if any(under(p, q) or under(q, p) for q in paths)]  # fmt: skip
        if paths and all(any(under(q, p) for p in logged) for q in paths):
            total += c["reclaimable"]
            continue
        figures = {i.get("path"): i.get("frees_bytes") for i in c.get("items") or []}
        if not logged or any(figures.get(p) is None for p in logged):
            return None
        total += sum(figures[p] for p in logged)
    return total
