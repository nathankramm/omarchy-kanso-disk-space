"""Two states statvfs shows without root (D102, D103): a filesystem the kernel
made read-only, and a /boot too full for new snapshot boot entries.

ST_RDONLY in statvfs's flags, never mountinfo's mount options: a filesystem
forced read-only after an error (btrfs, ext4 errors=remount-ro) keeps "rw" in
the per-mount options of mountinfo's field 6, while statvfs reports the
superblock's state. Read-only notifies once per episode: the marker is in the
runtime folder (tmpfs), which a read-only disk cannot take away.

Test hooks, honoured only under the test suite (KANSO_DISK_TESTING=1):
KANSO_DISK_TEST_READONLY ("/,/home": the paths that read as read-only) and
KANSO_DISK_TEST_BOOT ({"blocks", "bfree"} of /boot's statvfs).
"""

import json
import os

READONLY_PATHS = ("/", "/home")
# limine-snapper-sync's LIMIT_USAGE_PERCENT default: at it, new snapshots get
# no boot entry (/etc/limine-snapper-sync.conf)
BOOT_LIMIT = 85
APP = "kanso-disk-space"
MARKER = "readonly-notified"


def testing():
    return os.environ.get("KANSO_DISK_TESTING") == "1"


def readonly():
    """The READONLY_PATHS whose filesystem is read-only now, in order."""
    if testing():
        hook = os.environ.get("KANSO_DISK_TEST_READONLY") or ""
        return [p for p in READONLY_PATHS if p in hook.split(",")]
    out = []
    for path in READONLY_PATHS:
        try:
            if os.statvfs(path).f_flag & os.ST_RDONLY:
                out.append(path)
        except OSError:
            continue
    return out


def where(paths):
    return " and ".join(paths)


def is_are(paths):
    """The warning's fact and the toast's title (D146): "/ is read-only",
    "/ and /home are read-only"."""
    return f"{where(paths)} {'are' if len(paths) > 1 else 'is'} read-only"


def boot_percent(table):
    """How full /boot is, in whole percent rounded down, when it is a mount
    of its own (the ESP); None otherwise or unreadable."""
    if not any(m["mount_point"] == "/boot" for m in table):
        return None
    if testing():
        hook = os.environ.get("KANSO_DISK_TEST_BOOT")
        if not hook:
            return None
        doc = json.loads(hook)
        blocks, bfree = int(doc["blocks"]), int(doc["bfree"])
    else:
        try:
            s = os.statvfs("/boot")
        except OSError:
            return None
        blocks, bfree = s.f_blocks, s.f_bfree
    if blocks <= 0:
        return None
    return (blocks - bfree) * 100 // blocks


def note(paths):
    """The read-only notification: critical; its click opens the What now
    panel (D145, D146), which says what to do. No path from the home folder
    (§8.6)."""
    return {
        "kind": "readonly",
        "urgency": "critical",
        "title": is_are(paths),
        "body": "Saving fails until a restart. Click for what to do.",
    }


def marker_path():
    """The marker, or None under the test suite without XDG_RUNTIME_DIR: a
    test never touches the real runtime folder."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base and testing():
        return None
    return os.path.join(base or f"/run/user/{os.getuid()}", APP, MARKER)


def first_notice(paths):
    """Whether this read-only state is new since it was last notified: the
    marker holds what was read-only then ("" once it cleared). A marker that
    cannot be written means the notification may repeat, never go missing."""
    now = where(paths)
    path = marker_path()
    if path is None:
        return bool(paths)
    try:
        with open(path, encoding="utf-8") as f:
            before = f.read()
    except OSError:
        before = ""
    if before == now:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(now)
    except OSError:
        pass
    return bool(paths)
