"""The Used view's ledger: every byte on the disk (§6 "The ledger").

Rows: Home (/home), System (everything else on the disk), /var, Swap (the
swap files /proc/swaps lists on this disk, D64), then the remainder = df used
− the measured rows, then Reserved (f_bfree − f_bavail, when non-zero), then
Free (f_bavail). They add up to f_blocks *
f_frsize to the byte, by construction and by test.

The walk's rows are on disk only on ext4 (no compression, no shared
extents, D76); there the remainder is an exact count of what the user cannot
read plus the file system's own bookkeeping, shown as "Other". On btrfs the
card shows the ledger only once on_disk() has put every row on disk from the
helper's measurement (D71, D78), with no remainder.
"""

import os
import time

import names as names_
import units
import walk

TIME_LIMIT = 300  # seconds; a slower disk gets a partial ledger, marked so
TOP_CHILDREN = 10  # kept per row for the report; the card shows 3 + other


SWAPS = "/proc/swaps"


def swap_files():
    """Absolute paths of the active swap files (not partitions or zram), from
    /proc/swaps or the KANSO_DISK_TEST_SWAPS file (tests); [] if unreadable."""
    path = os.environ.get("KANSO_DISK_TEST_SWAPS") or SWAPS
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "file":
            # /proc/swaps escapes a space as \040
            out.append(parts[0].replace("\\040", " "))
    return out


def row_of(rel):
    """'home', 'var' or 'system' for a path relative to the walk's root."""
    first = rel.split("/", 1)[0]
    return first if first in ("home", "var") else "system"


def bucket_for(root, swaps=()):
    """A file's bucket: its row and its path two levels into that row
    (~/Code → ('home', '<user>/Code'); /usr/lib/x → ('system', 'usr/lib'));
    a swap file is ('swap', '') whatever folder it sits in (D64)."""
    prefix = root.rstrip("/") + "/"
    swap_paths = {prefix + p.lstrip("/") for p in swaps}

    def bucket(path):
        if path in swap_paths:
            return "swap", ""
        rel = path[len(prefix) :] if path.startswith(prefix) else path.lstrip("/")
        row = row_of(rel)
        parts = rel.split("/")
        if row != "system":
            parts = parts[1:]
        return row, "/".join(parts[:2]) if parts else ""

    return bucket


def row(rid, label, path, size, children=(), other=None):
    """One ledger row; `other` is what its named folders leave of it."""
    kids = list(children)
    if other is None:
        other = size - sum(k["bytes"] for k in kids)
    return {"id": rid, "label": label, "path": path, "bytes": size,
            "children": kids, "other": other}  # fmt: skip


def children(sizes, rid, size):
    """The row's biggest two-level folders, then everything else as other."""
    mine = sorted(
        ((k, v) for (r, k), v in sizes.items() if r == rid and k),
        key=lambda kv: (-kv[1], kv[0]),
    )
    top = [{"path": k, "bytes": v} for k, v in mine[:TOP_CHILDREN]]
    return top, size - sum(c["bytes"] for c in top)


def measure(root, mount_table, statvfs, now, deadline=None, swaps=None, foreign=None):
    """Walk `root` and return the ledger document (exact bytes); `foreign`
    is the caller's walk.foreign_mount_points when it has them."""
    started = time.monotonic()
    if foreign is None:
        foreign = walk.foreign_mount_points(mount_table)
    swaps = swap_files() if swaps is None else swaps
    w = walk.Walk(foreign, bucket_for(root, swaps), deadline or started + TIME_LIMIT)
    w.run(root)
    fs = statvfs()
    totals = {"home": 0, "var": 0, "system": 0, "swap": 0}
    for (rid, _), size in w.sizes.items():
        totals[rid] += size
    frsize = fs["frsize"]
    disk = fs["blocks"] * frsize
    used = (fs["blocks"] - fs["bfree"]) * frsize
    reserved = (fs["bfree"] - fs["bavail"]) * frsize
    remainder = used - sum(totals.values())
    rows = []
    for rid, label, path in (
        ("home", "Home", "/home"),
        ("system", "System", "/"),
        ("var", "/var", "/var"),
        ("swap", "Swap", ""),
    ):
        if rid == "swap" and not totals["swap"]:
            continue
        top, other = children(w.sizes, rid, totals[rid])
        rows.append(row(rid, label, path, totals[rid], top, other))
    rows.append(row("remainder", "Other", "", remainder, other=0))
    if reserved:
        rows.append(row("reserved", "Reserved", "", reserved, other=0))
    rows.append(row("free", "Free", "", fs["bavail"] * frsize, other=0))
    return {
        "schema": 1,
        "measured_at": now,
        "seconds": round(time.monotonic() - started, 1),
        "complete": w.complete,
        "disk": disk,
        "used": used,
        "rows": rows,
        "unreadable": sorted(w.unreadable),
    }


FACTORY = "@factory"


def on_disk(doc, exact, data_used, metadata):
    """D71: with the helper's exact block, every data row is its on-disk size
    (compsize, cumulative in the row order), and the rows add up to btrfs
    Data used and to the disk, to the byte:

      Home · System · /var · Swap · Snapshots   on disk
      Factory snapshot (or Other subvolumes)    Data used − those rows: data
                                                in no mounted subvolume
      Metadata                                  its chunks, less the reserve
                                                statvfs reports separately
      Reserved                                  the rest of the disk not free
      Free                                      statvfs, as before

    A folder row's sub-line names only folders the helper measured on disk,
    so none exceeds its row. The remainder goes."""
    rows = {r["id"]: r for r in doc["rows"]}
    disk, free = doc["disk"], rows["free"]["bytes"]
    reserved = rows["reserved"]["bytes"] if "reserved" in rows else 0
    sizes = exact["rows"]
    data = sum(sizes.values())
    outside = max(0, data_used - data)
    meta = max(0, metadata - reserved)
    rest = disk - data - outside - meta - free
    if rest < 0:  # run-to-run churn: never a negative row
        outside, rest = max(0, outside + rest), 0
    label = (
        "Factory snapshot"
        if exact["unreached"] == [FACTORY.lstrip("/")]
        else "Other subvolumes"
    )

    def measured_children(rid):
        old = rows.get(rid)
        if old is None:
            return []
        out = []
        for c in old["children"]:
            full = os.path.join(old["path"], c["path"])
            if full in exact["children"]:
                out.append({"path": c["path"], "bytes": exact["children"][full]})
        return sorted(out, key=lambda c: (-c["bytes"], c["path"]))

    new = [
        row("home", "Home", "/home", sizes["home"], measured_children("home")),
        row("system", "System", "/", sizes["system"], measured_children("system")),
        row("var", "/var", "/var", sizes["var"], measured_children("var")),
    ]
    if sizes["swap"]:
        new.append(row("swap", "Swap", "", sizes["swap"]))
    if sizes["snapshots"]:
        new.append(row("snapshots", "Snapshots", "/.snapshots", sizes["snapshots"]))
    if outside:
        new.append(row("factory", label, "", outside))
    new.append(row("metadata", "Metadata", "", meta))
    if rest:
        new.append(row("reserved", "Reserved", "", rest))
    new.append(rows["free"])
    doc["rows"] = new
    doc["on_disk"] = True


REMAINDER_SUB = "unreadable folders, file system bookkeeping"


def remainder_sub(doc):
    """The remainder row's sub-line (D64): what it holds, with "scan
    stopped at 5 min" first so it is never the part cut off."""
    parts = [] if doc["complete"] else ["scan stopped at 5 min"]
    return " · ".join([*parts, REMAINDER_SUB])


def child_names(row, shown):
    """'Code 310 · Videos 260 · .local 96 · other 76' (whole GiB), adding up to
    the row's displayed reading (`shown`, in tenths: D83's split) in whole
    GiB, half up (D85, D92: /var at 8.49 GiB reads 8.5, so its parts share
    9): its biggest folders that read at least 1 on their own (D68), then
    "other" when it does too, share that by largest remainder; a part left
    at 0 is not named."""
    picks = [c for c in row["children"][:3] if units.whole_gib(c["bytes"]) != "0"]
    if not picks:
        return ""
    other = row["bytes"] - sum(c["bytes"] for c in picks)
    # a folder's name is data: shown escaped, never as markup (D114)
    names = [names_.shown(os.path.basename(c["path"]) or c["path"]) for c in picks]
    parts = [c["bytes"] for c in picks]
    if units.whole_gib(other) != "0":
        names.append("other")
        parts.append(other)
    total = (max(shown, 0) + 5) // 10
    shares = units.apportion(parts, total, per=1, keep_nonzero=False)
    return " · ".join(
        f"{n} {share}" for n, share in zip(names, shares, strict=True) if share
    )
