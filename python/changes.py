"""What changed since the user last looked (D115, D118): one exact line in
the Glance, and the breakdown behind each big change on Trend.

The engine keeps a copy of the ledger's rows from every measured scan
(`ledgers/<local time>.json`) for 90 days (D126), so a cleanup and the scan
it makes due on the same day are both there. The baseline is the last
day's ledger measured before the user's previous visit to the full view
(views.json; visits within MERGE of each other are one); with no such
ledger, the oldest within the last week. The line shows only when the space
used changed by at least FLOOR since then: "Used +22.1 GiB since Mon ·
Downloads +8.0", the change and its biggest part alone (never a row's rest or
"other"). Trend gives the whole breakdown: up to TOP parts of at least
PART_FLOOR, then "other", their tenths adding up exactly by largest
remainder. A part is a folder both scans measured exactly, "rest of <row>"
after them, or a whole row; if folders would leave a row's rest negative in
either scan (reflinked copies counted twice), the ledger rows instead.
"""

import datetime
import json
import os

import names
import units

GIB = units.GIB
DAY = units.DAY
KEEP_DAYS = 90
FALLBACK_DAYS = 7
MERGE = 30 * 60
FLOOR = GIB
PART_FLOOR = GIB // 2
TOP = 3
# a folder's label on the one Glance line (D118)
LABEL_CAP = 16
FOLDER = "ledgers"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def file_name(t):
    """ledgers/<local date>T<HHMMSS>.json: one per measured scan (D126)."""
    return units.local(t).strftime("%Y-%m-%dT%H%M%S") + ".json"


def snapshot(doc):
    """The part of a ledger the change line reads: when, and each row's id,
    label, bytes and the folders the scan measured exactly in it (the Usage
    sub-line's partition: those folders, then the rest of the row)."""
    return {
        "schema": 1,
        "measured_at": doc["measured_at"],
        "rows": [
            {"id": r["id"], "label": r["label"], "bytes": r["bytes"],
             "children": [{"path": c["path"], "bytes": c["bytes"]}
                          for c in r.get("children") or []]}
            for r in doc["rows"]
        ],
    }  # fmt: skip


def load_all(folder):
    """Every kept ledger, oldest first; an unreadable one is skipped."""
    out = []
    try:
        files = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in files:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if (
            isinstance(doc, dict)
            and doc.get("schema") == 1
            and isinstance(doc.get("rows"), list)
        ):
            out.append(doc)
    return sorted(out, key=lambda d: d["measured_at"])


def expired(names, now):
    """The ledger files older than KEEP_DAYS, by the day their name starts
    with (a daily file from before D126 is just the day)."""
    oldest = (units.local(now).date() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    return [n for n in names if n.endswith(".json") and n[:10] < oldest]


def visit(doc, now):
    """views.json after a visit to the full view at `now`: a visit within
    MERGE of the last one is the same visit."""
    doc = doc if isinstance(doc, dict) else {}
    last = doc.get("last") if units.whole(doc.get("last"), 0) else 0
    if last and now - last < MERGE:
        return {"schema": 1, "last": last, "previous": doc.get("previous") or 0}
    return {"schema": 1, "last": now, "previous": last}


def looked_at(doc, now):
    """When the user last looked: their last visit, or the one before it
    while the last is still under way (within MERGE); 0 without one."""
    doc = doc if isinstance(doc, dict) else {}
    last = doc.get("last") if units.whole(doc.get("last"), 0) else 0
    previous = doc.get("previous") if units.whole(doc.get("previous"), 0) else 0
    return previous if last and now - last < MERGE else last


def baseline(ledgers, looked, now):
    """The last ledger measured before `looked`, else the oldest within
    FALLBACK_DAYS; None."""
    before = [d for d in ledgers if looked and d["measured_at"] < looked]
    if before:
        return before[-1]
    recent = [d for d in ledgers if d["measured_at"] >= now - FALLBACK_DAYS * DAY]
    return recent[0] if recent else None


def row_parts(old, new):
    """[(key, label, bytes changed, a row's rest)] for every row's change (Free
    left out). A row's folders appear only if both scans measured them
    exactly (D118), the rest of the row after them as "rest of <row>"; a row
    with no such folder is itself."""
    before = {r["id"]: r for r in old["rows"]}
    after = {r["id"]: r for r in new["rows"]}
    out = []
    for rid in [*after, *[r for r in before if r not in after]]:
        if rid == "free":
            continue
        a, b = before.get(rid, {}), after.get(rid, {})
        was = {c["path"]: c["bytes"] for c in a.get("children") or []}
        now = {c["path"]: c["bytes"] for c in b.get("children") or []}
        both = sorted(was.keys() & now.keys())
        rest_was = a.get("bytes", 0) - sum(was[p] for p in both)
        rest_now = b.get("bytes", 0) - sum(now[p] for p in both)
        if rest_was < 0 or rest_now < 0:
            # folders counting the same bytes twice (reflinked copies): the
            # finer level cannot be exact, so the ledger rows instead
            return None
        label = (b or a)["label"]
        for p in both:
            out.append((f"{rid}:{p}", folder_label(p), now[p] - was[p], False))
        out.append(
            (
                rid,
                f"rest of {label}" if both else label,
                rest_now - rest_was,
                bool(both),
            )
        )
    return out


def whole_rows(old, new):
    """The same, one part per ledger row (the guard's fallback)."""
    before = {r["id"]: r for r in old["rows"]}
    after = {r["id"]: r for r in new["rows"]}
    return [
        (rid, (after.get(rid) or before[rid])["label"],
         after.get(rid, {}).get("bytes", 0) - before.get(rid, {}).get("bytes", 0), False)
        for rid in [*after, *[r for r in before if r not in after]] if rid != "free"
    ]  # fmt: skip


def folder_label(path):
    """A folder's own name, escaped (D114) and short enough for one line."""
    name = names.shown(os.path.basename(path) or path)
    return name if len(name) <= LABEL_CAP else name[: LABEL_CAP - 1] + "…"


def signed_tenths(n):
    """Bytes in signed tenths of a GiB, half away from zero."""
    t = (abs(n) * 10 + GIB // 2) // GIB
    return -t if n < 0 else t


def split(parts, total_tenths):
    """Signed tenths for `parts` (bytes) adding up to `total_tenths`: each
    floored, the tenths left over to the largest remainders."""
    base = [p * 10 // GIB for p in parts]
    rem = [p * 10 - b * GIB for p, b in zip(parts, base, strict=True)]
    left = total_tenths - sum(base)
    for i in sorted(range(len(parts)), key=lambda i: (-rem[i], i))[: max(left, 0)]:
        base[i] += 1
    return base


def tenths_word(t):
    sign = units.MINUS if t < 0 else "+"
    return f"{sign}{abs(t) // 10}.{abs(t) % 10}"


def breakdown(old, new, total=None):
    """(used change in bytes, [(label, signed tenths, a row's rest)]): up to
    TOP parts of at least PART_FLOOR, the biggest change first, then
    "other" (the rest, the unattributed remainder included), their tenths
    adding up exactly to the change's by largest remainder. A given `total`
    (a big change as the chart marks it, D126) is the headline, and "other"
    takes whatever the ledgers on either side changed besides it."""
    parts = row_parts(old, new)
    if parts is None:
        parts = whole_rows(old, new)
    if total is None:
        total = sum(d for _, _, d, _ in parts)
    named = sorted((p for p in parts if p[0] != "remainder" and abs(p[2]) >= PART_FLOOR),
                   key=lambda p: (-abs(p[2]), p[0]))[:TOP]  # fmt: skip
    rest = total - sum(p[2] for p in named)
    tenths = split([p[2] for p in named] + [rest], signed_tenths(total))
    out = [(p[1], t, p[3]) for p, t in zip(named, tenths, strict=False)]
    if tenths[-1]:
        out.append(("other", tenths[-1], True))
    return total, out


def used_text(total):
    """ "Used +22.1 GiB" (U+2212 for a fall), always in tenths, like its parts."""
    return f"Used {tenths_word(signed_tenths(total))} GiB"


def full_text(old, new, total=None):
    """Trend's breakdown (D118): "Used +22.1 GiB: Downloads +8.0 · .cache +6.1
    · other +3.0"."""
    total, parts = breakdown(old, new, total)
    words = " · ".join(f"{label} {tenths_word(t)}" for label, t, _ in parts)
    return f"{used_text(total)}: {words}" if words else used_text(total)


def since_text(t, now):
    """ "since 08:12" today, "since yesterday", "since Mon" within the week,
    else "since Oct 12"."""
    day, today = units.local(t).date(), units.local(now).date()
    if day == today:
        return f"since {units.clock(t)}"
    if day == today - datetime.timedelta(days=1):
        return "since yesterday"
    if day > today - datetime.timedelta(days=FALLBACK_DAYS):
        return f"since {WEEKDAYS[day.weekday()]}"
    return f"since {units.date_text(t)}"


def line(ledgers, current, views_doc, now):
    """The Glance's change line (D118): "Used +22.1 GiB since Mon · Downloads
    +8.0", the total and only its biggest part, the total alone when that
    part is a row's rest or "other"; "" with no baseline, the same scan, or
    under FLOOR."""
    if current is None:
        return ""
    base = baseline(ledgers, looked_at(views_doc, now), now)
    if base is None or base["measured_at"] >= current["measured_at"]:
        return ""
    total, parts = breakdown(base, current)
    if abs(total) < FLOOR:
        return ""
    head = f"{used_text(total)} {since_text(base['measured_at'], now)}"
    if not parts or parts[0][2]:
        return head
    label, tenths, _ = parts[0]
    return f"{head} · {label} {tenths_word(tenths)}"


def across(ledgers, t, used):
    """Trend's breakdown across a big change at `t` of `used` bytes (the
    free-space step the chart marks, as used space): from the last ledger
    before it to the first at or after it, its headline the chart's own
    change (D126); "" without both."""
    before = [d for d in ledgers if d["measured_at"] < t]
    after = [d for d in ledgers if d["measured_at"] >= t]
    if not before or not after:
        return ""
    return full_text(before[-1], after[0], used)
