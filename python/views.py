"""The card's Cleanup and Usage tabs and the Glance's footer and rows, from
scan.json and ledger.json (§8.5, D49, D61–D64). The review condition is
review.py's (D58).

No estimated size appears anywhere (D78): a row shows its size only when it
was measured (on ext4 by the scan, on btrfs by the helper); otherwise "—".
Docker's own figure is shown as "reported by Docker", never counted (D75)."""

import ledger
import units

GIB = units.GIB
STALE_LEDGER = 36 * 3600
# The Cleanup tab's groups (D62): the main list has no title (it is what the
# hero counts); snapshots sit below a hairline, never in the totals (D54).
GROUPS = (
    ("now", ""),
    ("after", "AFTER UPDATES"),
    ("rollback", ""),
    ("sudo", "NOT MEASURED"),
)
UNMEASURED = "—"  # an unmeasured row's value; never a hero (D68)
NOT_MEASURED = "not measured"
NEVER_TEXT = "Not measured yet"
RUNNING_TEXT = "Measuring…"
SCAN_NOW = "Scan now ›"
ASK_AGENT = "Ask agent ›"
RESCAN = "Rescan ›"
DUA = {"text": "Explore in dua ›", "argv": ["xdg-terminal-exec", "dua", "i", "/"]}
NEVER_TOUCHED = "Documents and git repos never touched"


SHOWN_MIN = GIB // 20  # a row must read at least "0.1 GiB" to be worth a line (D26)


def offered(cat):
    """A measured category with something to give back."""
    return cat["state"] == "ok" and (cat["reclaimable"] or 0) > 0


def shown(cat):
    """On the card: offered and big enough not to read "0.0 GiB", or waiting
    for its app to close (D129); the report still lists every byte."""
    if cat["state"] != "ok" or cat.get("waiting"):
        return True
    return (cat["reclaimable"] or 0) >= SHOWN_MIN


def group_of(cat):
    if cat["state"] == "needs sudo":
        return "sudo"
    # an error, or an app's caches waiting for it to close (D129)
    if cat["state"] == "error" or (cat["state"] == "ok" and cat.get("waiting")):
        return "now" if cat["frees"] == "now" else "after"
    if offered(cat):
        if cat["frees"] == "rollback":
            return "rollback"
        return "now" if cat["frees"] == "now" else "after"
    return None


def qualifier_of(cat):
    """The muted words after a row's label (D62): "could not measure" for a
    scanner that failed, "not measured" for a size nothing measured (D78),
    else the category's own qualifier."""
    if cat["state"] == "error":
        return "could not measure"
    # a cleaner whose share no path shows (pnpm's prune, D128) says why
    unsaid = not (cat.get("reported") or cat.get("unmeasurable") or cat.get("waiting"))
    if offered(cat) and not exact(cat) and unsaid:
        return NOT_MEASURED
    return cat["qualifier"]


def counted(c):
    """A row the totals count: measured, exact, not the rollback points (D54)
    and not a tool's own figure (D75)."""
    return (
        offered(c) and c["frees"] != "rollback" and exact(c) and not c.get("reported")
    )


def totals(cats):
    """(frees now, frees after updates): only counted rows (D78)."""
    now = sum(c["reclaimable"] for c in cats if counted(c) and c["frees"] == "now")
    after = sum(
        c["reclaimable"] for c in cats if counted(c) and c["frees"] == "after updates"
    )
    return now, after


def exact(cat):
    return cat["exact"]


def cleanup_text(cleanup, now, expected=None):
    """The last real result (D62), against what it should have freed by the
    plugin's own figure (D111, D116): "Last cleanup today · 9.8 of 12.4 GiB
    freed" (today · yesterday · Oct 20), the agent's estimate when the plugin
    had none; "· 1.5 GiB freed" with nothing expected. A negative difference
    reads 0.0."""
    if cleanup is None:
        return ""
    freed = max(cleanup["freed"], 0)
    day = units.day_text(cleanup["t"], now)
    want = expected_of(cleanup, expected)
    # an expected figure that reads 0.0 says nothing: freed alone
    if units.shown_tenths(max(want, 0)) == 0:
        return f"Last cleanup {day} · {units.gib(freed)} freed"
    return f"Last cleanup {day} · {units.gib_number(freed)} of {units.gib(want)} freed"


def expected_of(cleanup, expected):
    return expected if expected is not None else cleanup["estimated"]


def receipt_text(cleanup, expected, cats):
    """Why a cleanup freed less than expected, named once (D116): the
    agent's estimate (no plugin figure), held by rollback points (a category
    that frees after updates), waiting in the Trash (moved there, not
    emptied), else plainly how much less; "" when it freed what it should."""
    if cleanup is None:
        return ""
    gap = expected_of(cleanup, expected) - max(cleanup["freed"], 0)
    if gap <= 0 or units.shown_tenths(gap) == 0:
        return ""
    if expected is None:
        return "Expected is the agent's estimate"
    frees = {c["id"]: c["frees"] for c in cats}
    if any(frees.get(cid) == "after updates" for cid in cleanup["categories"]):
        return f"{units.gib(gap)} still held by rollback points"
    if in_trash(cleanup["commands"]):
        return f"{units.gib(gap)} waiting in the Trash"
    return f"{units.gib(gap)} less than expected"


def in_trash(commands):
    """Whether the cleanup's last Trash step moved something there without
    emptying it afterwards."""
    waiting = False
    for c in commands:
        words = c.split()
        if words[:2] == ["gio", "trash"]:
            waiting = "--empty" not in words
    return waiting


def shown_split(sizes, total, fixed=()):
    """{key: tenths} for `sizes` ({key: bytes}) that add up exactly to the
    displayed `total` (bytes, D83): a key in `fixed` ({key: tenths}) keeps its
    own reading (a row of 100 GiB or more in whole GiB, Free truncated), and
    the tenths left go to the others by largest remainder."""
    out = dict(fixed)
    rest = [k for k in sizes if k not in out]
    left = units.shown_tenths(total) - sum(out.values())
    out.update(zip(rest, units.apportion([max(sizes[k], 0) for k in rest], left)))
    return out


def cleanup_tenths(cats):
    """Each shown counted row's displayed tenths: they add up to the hero
    (D83), which still counts the rows too small to show (D26)."""
    total = sum(c["reclaimable"] for c in cats if counted(c))
    sizes = {c["id"]: c["reclaimable"] for c in cats if counted(c) and shown(c)}
    big = {
        k: units.shown_tenths(v)
        for k, v in sizes.items()
        if units.shown_tenths(v) >= 1000
    }
    return shown_split(sizes, total, big)


def figured(c):
    """Whether a row shows a figure: measured and offered, or Docker's own."""
    return offered(c) and bool(exact(c) or c.get("reported"))


def cleanup_row(c, largest, tenths=None, shared=None):
    """{id, label, qualifier, text, bar, style} for one category. style:
    "row" (counted: a bar on the page's largest row's scale), "aside" (the
    snapshots, and Docker's own figure: muted value, no bar, D62, D75) or
    "dim" (not measured: "—")."""
    if not figured(c):
        style, text = "dim", UNMEASURED
    elif c["frees"] == "rollback" or c.get("reported"):
        # one figure, one reading: Snapshots as Usage shows it (D83)
        text = (shared or {}).get(c["id"]) or units.gib(c["reclaimable"])
        style = "aside"
    else:
        style = "row"
        text = (
            units.tenths_text(tenths[c["id"]])
            if tenths
            else units.gib(c["reclaimable"])
        )
    return {
        "id": c["id"],
        "label": c["label"],
        "qualifier": qualifier_of(c),
        "text": text,
        "bar": round(c["reclaimable"] / largest, 4)
        if style == "row" and largest
        else 0.0,
        "style": style,
    }


def cleanup_groups(cats, shared=None):
    rows = [c for c in cats if group_of(c) and shown(c)]
    largest = max([c["reclaimable"] for c in rows if counted(c)], default=0)
    tenths = cleanup_tenths(cats)
    groups = []
    for gid, title in GROUPS:
        # a row with no figure ("—": not measured, pnpm's prune, an app still
        # running) after every row with one, then biggest first (D131)
        mine = sorted(
            (c for c in rows if group_of(c) == gid),
            key=lambda c: (not figured(c), -(c["reclaimable"] or 0)),
        )
        if not mine:
            continue
        sized = [c for c in mine if counted(c)]
        # a total only where it adds something: a titled group of counted
        # rows with more than one size (the main list's total is the hero)
        total = ""
        if gid == "after" and len(sized) > 1:
            total = units.tenths_text(sum(tenths[c["id"]] for c in sized))
        groups.append(
            {
                "id": gid,
                "title": title,
                "total_text": total,
                "hairline": gid == "rollback",
                "rows": [cleanup_row(c, largest, tenths, shared) for c in mine],
            }
        )
    return groups


def footers(left="", action="", verb="", argv=(), busy=True):
    """A page's pinned footer (D94): {left, action, verb, argv}, idle and
    while Service scans. A scan's own action and Ask agent wait for the scan
    (`busy`); Explore in dua does not."""
    idle = {"left": left, "action": action, "verb": verb, "argv": list(argv)}
    scanning = {**idle, "action": "", "verb": "", "argv": []} if busy else idle
    return {"footer": idle, "scanning_footer": scanning}


TOTALS_ONLY = "Agent report: totals only, no names"


def reclaim_view(scan, now, cleanup=None, shared=None, reclaimable=None, expected=None,
                 totals_only=False):  # fmt: skip
    """The Cleanup tab (D62); `cleanup` is the newest valid cleanups.jsonl
    entry (cleanups.check). Warnings sit above every page (D119). With the
    agent report set to totals only, a muted line says so above the footer
    (D127), beside Ask agent. Before
    the first scan: no hero number, the muted "Not measured yet" in its place,
    and "Scan now ›" (D67, D68)."""
    out = {
        "state": "never",
        "when_text": "",
        "hero_text": "",
        "hero_unit": "GiB",
        "hero_caption": "reclaimable",
        "groups": [],
        **footers(action=SCAN_NOW, verb="scan"),
        "never_text": NEVER_TEXT,
        "running_text": RUNNING_TEXT,
        "receipt_text": "",
        "mode_text": TOTALS_ONLY if totals_only else "",
    }
    if scan is None:
        return out
    cats = scan["categories"]
    if reclaimable is None:
        reclaimable = sum(totals(cats))
    # the left: the last cleanup once one is logged; no Ask agent until
    # there is something to clean (D67)
    left = cleanup_text(cleanup, now, expected) or NEVER_TOUCHED
    ask = (ASK_AGENT, "ask") if reclaimable > 0 else ("", "")
    return {
        **out,
        "state": "ok",
        "when_text": scanned_text(scan["measured_at"], now),
        "hero_text": units.gib_number(reclaimable),
        "groups": cleanup_groups(cats, shared),
        **footers(left, *ask),
        "receipt_text": receipt_text(cleanup, expected, cats),
    }


def reclaim_footer(scan, reclaimable=None):
    """The Glance's footer: "12.4 GiB reclaimable" (D63: no "up to")."""
    if scan is None:
        return ""
    if reclaimable is None:
        reclaimable = sum(totals(scan["categories"]))
    return f"{units.gib(reclaimable)} reclaimable"


def where_rows(doc):
    """The Glance's rows under the bar (D61): the ledger's three biggest
    measured rows by name and size, no folder hints, no title."""
    if doc is None:
        return []
    rows = sorted(
        (r for r in doc["rows"] if r["id"] in MEASURED_ROWS),
        key=lambda r: -r["bytes"],
    )
    # the same readings as Usage (D83)
    tenths = used_tenths(doc)
    return [
        {"label": r["label"], "text": usage_text(tenths[r["id"]])} for r in rows[:3]
    ]


MEASURED_ROWS = ("home", "system", "var", "swap", "snapshots", "metadata")


def used_view(doc, now, level_colour, dua):
    """The Usage tab (D64). Before the first scan: no hero number, the muted
    "Not measured yet" in its place, and "Scan now ›" (D67, D68). A last run
    that did not finish is a warning with its own Rescan (D119)."""
    out = {
        "state": "never",
        "hero_text": "",
        "hero_unit": "GiB",
        "hero_caption": "",
        "when_text": "",
        "sub_text": "",
        "sub_colour": "muted",
        "rows": [],
        **footers(action=SCAN_NOW, verb="scan"),
        "never_text": NEVER_TEXT,
        "running_text": RUNNING_TEXT,
    }
    if doc is None:
        return out
    disk = doc["disk"]
    stale = now - doc["measured_at"] > STALE_LEDGER
    when = units.when_text(doc["measured_at"], now)
    tenths = used_tenths(doc)
    rows = [used_row(r, doc, disk, level_colour, tenths) for r in doc["rows"]]
    if stale:
        footer = footers(action=RESCAN, verb="rescan")
    elif dua:
        footer = footers(action=DUA["text"], verb="dua", argv=DUA["argv"], busy=False)
    else:
        footer = footers()
    return {
        **out,
        "state": "ok",
        # the hero and Free add up to the total as shown (D132)
        "hero_text": units.usage_number(units.tenths(disk) - tenths["free"]),
        "hero_caption": f"used of {units.usage_number(units.tenths(disk))}",
        "when_text": scanned_text(doc["measured_at"], now),
        # the sub-line only speaks once the ledger is stale
        "sub_text": f"measured {when}" if stale else "",
        "sub_colour": "urgent" if stale else "muted",
        "rows": rows,
        **footer,
    }


def scanned_text(t, now):
    """Beside the Reclaim and Used heroes (D44): 'scanned 07:36', 'scanned
    yesterday 07:36' or 'scanned Oct 19 07:36'."""
    return f"scanned {units.when_text(t, now)}"


# The Usage rows' names and fixed sub-lines (D64); folder rows get their
# biggest folders with sizes.
FOLDER_ROWS = ("home", "system", "var")
FIXED_SUBS = {
    "metadata": "file system bookkeeping",
    "reserved": "kept back by the file system",
}
DIM_ROWS = ("remainder", "reserved", "metadata", "factory")


def used_label(row):
    # the walk's remainder, on ext4 only (D76): an exact count of the bytes
    # no readable folder holds
    return "Other" if row["id"] == "remainder" else row["label"]


def used_tenths(doc):
    """Each Usage row's displayed tenths, one decimal at every size (D132):
    the total is the disk rounded half up, Free is truncated (D11), the used
    hero is the total less Free, and every other row shares the hero by
    largest remainder (D83); a negative row (ext4 churn) keeps its own
    reading. So the hero and Free add up to the total, the rows to the hero."""
    sizes = {r["id"]: r["bytes"] for r in doc["rows"] if r["id"] != "free"}
    free = next(r["bytes"] for r in doc["rows"] if r["id"] == "free")
    out = {"free": units.tenths(max(free, 0), floor=True)}
    out |= {rid: units.tenths(b) for rid, b in sizes.items() if b < 0}
    rest = [rid for rid in sizes if rid not in out]
    left = units.tenths(doc["disk"]) - sum(out.values())
    out.update(zip(rest, units.apportion([sizes[k] for k in rest], left)))
    return out


def usage_text(tenths):
    """'855.9 GiB': a Usage reading (D132)."""
    return f"{units.usage_number(tenths)} GiB"


def shared_readings(doc):
    """Cleanup's side rows that are a Usage row's measurement read as Usage
    shows it (D83): Snapshots."""
    if doc is None or not any(r["id"] == "snapshots" for r in doc["rows"]):
        return {}
    return {"snapshots": usage_text(used_tenths(doc)["snapshots"])}


def used_row(row, doc, disk, level_colour, tenths):
    rid = row["id"]
    sub = FIXED_SUBS.get(rid, "")
    if rid in FOLDER_ROWS and row["children"]:
        sub = ledger.child_names(row, tenths[rid])
    if rid == "remainder":
        sub = ledger.remainder_sub(doc)
    colour = {rid: "muted" for rid in DIM_ROWS} | {"free": level_colour}
    # the total's tenths, Free truncated as in the Glance (D63, D83, D132)
    text = usage_text(tenths[rid])
    return {
        "id": rid,
        "label": used_label(row),
        "sub": sub,
        "text": text,
        "bar": round(max(row["bytes"], 0) / disk, 4) if disk else 0.0,
        "colour": colour.get(rid, "accent"),
        "dim": rid in DIM_ROWS,
    }
