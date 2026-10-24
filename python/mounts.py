"""The mount table, read from /proc/self/mountinfo without a subprocess.

Three things depend on it: which btrfs filesystem `/` is (the metadata check),
whether a path frees space now or only after updates (§6 "Frees now vs
later"), and which directories the ledger walk may enter (D3). On btrfs every
subvolume has its own st_dev, so "the same filesystem" is decided here by the
mount's source device, never by st_dev.

Test hook: KANSO_DISK_TEST_MOUNTINFO=<file> reads that file instead.
"""

import os
import re

OCTAL = re.compile(r"\\([0-7]{3})")


def unescape(field):
    """mountinfo writes space, tab, newline and backslash as \\ooo."""
    return OCTAL.sub(lambda m: chr(int(m.group(1), 8)), field)


def parse(text):
    """[{mount_point, root, source, fstype, subvol}] in file order; bad lines
    skipped. subvol is btrfs's subvol= option ("/@home" for a folder of @home
    bind-mounted elsewhere, whose root is the folder), else ""."""
    out = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if not sep or len(fields) < 5 or len(tail) < 2:
            continue
        source = tail[1].split("[", 1)[0]
        options = tail[2].split(",") if len(tail) > 2 else []
        subvol = next((o[7:] for o in options if o.startswith("subvol=")), "")
        out.append(
            {
                "mount_point": unescape(fields[4]),
                "root": unescape(fields[3]),
                "source": unescape(source),
                "fstype": tail[0],
                "subvol": unescape(subvol),
            }
        )
    return out


def read():
    path = os.environ.get("KANSO_DISK_TEST_MOUNTINFO") or "/proc/self/mountinfo"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse(f.read())
    except OSError:
        return []


def under(path, mount_point):
    if mount_point == "/":
        return path.startswith("/")
    return path == mount_point or path.startswith(mount_point + "/")


def mount_of(path, mounts):
    """The mount holding `path`: the longest matching mount point, the last
    one mounted when two share it (as findmnt --target answers)."""
    best = None
    for m in mounts:
        if under(path, m["mount_point"]) and (
            best is None or len(m["mount_point"]) >= len(best["mount_point"])
        ):
            best = m
    return best


def root_mount(mounts):
    return mount_of("/", mounts)


def frees_now(path, mounts):
    """True when `path` is on another mount than `/`. On Omarchy only `@` (the
    `/` mount) is snapshotted, so space freed elsewhere (`@home`, `@log`,
    `@pkg`) comes back now, and space freed on `@` only once the snapshots
    holding it rotate."""
    root, here = root_mount(mounts), mount_of(path, mounts)
    if root is None or here is None:
        return False
    return here["mount_point"] != root["mount_point"]
