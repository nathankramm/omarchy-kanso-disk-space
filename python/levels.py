"""Levels with hysteresis and the btrfs metadata check (§6 "Levels").

Each level is entered when free space drops below its line and cleared only
when free space rises a margin above it, so a disk hovering on a line does not
flap. Blocked is Omarchy's own update gate (10 GiB, df avail), not a setting.
"""

import os

import mounts
import units

GIB = units.GIB
LEVELS = ("fine", "watch", "low", "critical", "blocked")
COLOURS = {
    "fine": "muted",
    "watch": "foreground",
    "low": "yellow",
    "critical": "urgent",
    "blocked": "urgent",
}
# the hero's level word: none while Fine
WORDS = {k: "" if k == "fine" else k.upper() for k in COLOURS}
CLEAR_MARGIN = 5 * GIB  # watch 50 → 55, low 30 → 35, critical 15 → 20
BLOCKED_ENTER = 10 * GIB  # bin/omarchy-update-requires-free-space
BLOCKED_CLEAR = 12 * GIB
METADATA_UNALLOCATED = 1 * GIB
METADATA_RATIO = 0.90
DEFAULTS = {
    "watch": 50,
    "low": 30,
    "critical": 15,
    "horizon": 14,
    "notifications": True,
    "number_on_bar": True,
    # the report for cloud agents names no folder or file (D127)
    "totals_only": False,
}


def rank(level):
    return LEVELS.index(level) if level in LEVELS else 0


def settings_valid(s):
    """The lines must strictly fall: watch > low > critical > the 10 GiB gate."""
    return (
        s["watch"] > s["low"] > s["critical"] > BLOCKED_ENTER // GIB
        and s["horizon"] > 0
    )


def lines(s):
    """{level: (enter below, clear above)} in bytes."""
    return {
        "watch": (s["watch"] * GIB, s["watch"] * GIB + CLEAR_MARGIN),
        "low": (s["low"] * GIB, s["low"] * GIB + CLEAR_MARGIN),
        "critical": (s["critical"] * GIB, s["critical"] * GIB + CLEAR_MARGIN),
        "blocked": (BLOCKED_ENTER, BLOCKED_CLEAR),
    }


def raw_level(avail, line):
    """The worst level whose line `avail` is below."""
    level = "fine"
    for name in LEVELS[1:]:
        if avail < line[name][0]:
            level = name
    return level


def next_level(prev, avail, line):
    """Worse (or equal) takes effect at once; better only one cleared line at a
    time, each cleared when `avail` rises above its clear line."""
    raw = raw_level(avail, line)
    if rank(raw) >= rank(prev):
        return raw
    level = prev
    while rank(level) > rank(raw) and avail > line[level][1]:
        level = LEVELS[rank(level) - 1]
    return level


def effective(level, pressure):
    """The metadata check lifts the level to at least Critical."""
    if pressure and rank(level) < rank("critical"):
        return "critical"
    return level


def sysfs_btrfs():
    return os.environ.get("KANSO_DISK_TEST_SYSFS_BTRFS") or "/sys/fs/btrfs"


def read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def fs_dir_for(device_name):
    """/sys/fs/btrfs/<fsid> whose devices/ holds `device_name`, or None."""
    base = sysfs_btrfs()
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for fsid in names:
        if os.path.exists(os.path.join(base, fsid, "devices", device_name)):
            return os.path.join(base, fsid)
    return None


def btrfs_fs(mount_table, root=None):
    """/sys/fs/btrfs/<fsid> of the root filesystem, or None off btrfs or
    unreadable; `root` is the caller's mounts.root_mount when it has it."""
    root = mounts.root_mount(mount_table) if root is None else root
    if root is None or root["fstype"] != "btrfs":
        return None
    return fs_dir_for(os.path.basename(os.path.realpath(root["source"])))


ALLOCATION = (("data", "disk_total"), ("data", "bytes_used"), ("metadata", "disk_total"),
              ("metadata", "bytes_used"), ("metadata", "total_bytes"),
              ("system", "disk_total"))  # fmt: skip


def allocation(fs):
    """{(kind, field): bytes or None} from <fs>/allocation, each read once."""
    out = {}
    for kind, field in ALLOCATION:
        try:
            out[kind, field] = read_int(os.path.join(fs, "allocation", kind, field))
        except (OSError, ValueError):
            out[kind, field] = None
    return out


def on_disk(mount_table, root=None):
    """(metadata, data used) from sysfs without root, either None off btrfs
    or unreadable: the bytes metadata and system chunks take on the disk
    (DUP counted twice, D55) and the bytes of data btrfs holds
    (allocation/data/bytes_used, what `btrfs filesystem df` calls Data used,
    D71)."""
    fs = btrfs_fs(mount_table, root)
    if fs is None:
        return None, None
    a = allocation(fs)
    meta = (a["metadata", "disk_total"], a["system", "disk_total"])
    return (None if None in meta else sum(meta)), a["data", "bytes_used"]


def metadata(mount_table, root=None):
    """{state, unallocated, used, total, pressure}. state "n/a" off btrfs,
    "unreadable" when sysfs refuses (then never pressure: no guess)."""
    out = {"state": "n/a", "unallocated": 0, "used": 0, "total": 0, "pressure": False}
    root = mounts.root_mount(mount_table) if root is None else root
    if root is None or root["fstype"] != "btrfs":
        return out
    fs = btrfs_fs(mount_table, root)
    if fs is None:
        return {**out, "state": "unreadable"}
    a = allocation(fs)
    totals = [a[k, "disk_total"] for k in ("data", "metadata", "system")]
    used, total = a["metadata", "bytes_used"], a["metadata", "total_bytes"]
    try:
        devices = os.path.join(fs, "devices")
        size = sum(
            read_int(os.path.join(devices, d, "size")) * 512
            for d in sorted(os.listdir(devices))
        )
    except (OSError, ValueError):
        return {**out, "state": "unreadable"}
    if None in (*totals, used, total):
        return {**out, "state": "unreadable"}
    allocated = sum(totals)
    unallocated = size - allocated
    pressure = (
        unallocated < METADATA_UNALLOCATED
        and total > 0
        and used / total > METADATA_RATIO
    )
    return {
        "state": "ok",
        "unallocated": unallocated,
        "used": used,
        "total": total,
        "pressure": pressure,
    }
