"""The journal files `journalctl --vacuum-size` removes (D106), worked out the
way journald does it, read-only: systemd v262, journal_directory_vacuum() in
src/libsystemd/sd-journal/journal-vacuum.c, called once per journal folder
(action_vacuum in src/journal/journalctl-varlink.c).

In each folder:
  - every regular file named *.journal or *.journal~ counts toward the
    folder's sum at its allocated size (st_blocks * 512); any other file is
    ignored;
  - only archived files (<prefix>@<32 hex seqnum id>-<16 hex seqnum>-
    <16 hex realtime>.journal) and corrupted ones (<prefix>@<16 hex
    realtime>-<16 hex>.journal~) can go; active files only count;
  - an archived or corrupted file with no entries (shorter than the header,
    or n_entries 0) always goes and does not count;
  - the rest go in journald's order (same seqnum id: by seqnum; else by
    realtime, the earliest of the name's, ctime, atime, mtime and creation
    time) until the folder's sum is at most the limit.
"""

import ctypes
import functools
import os
import re
import stat
import struct

# journald checks the separators by position and needs a prefix before "@"
HEX = "[0-9a-fA-F]"
ARCHIVED = re.compile(rf"^.+@({HEX}{{32}})-({HEX}{{16}})-({HEX}{{16}})\.journal$")
CORRUPTED = re.compile(rf"^.+@({HEX}{{16}})-({HEX}{{16}})\.journal~$")
HEADER_SIZE = 272  # sizeof(Header), journal-def.h (v254 and later)
N_ENTRIES_AT = 152  # offsetof(Header, n_entries)
STATX_BTIME = 0x800
AT_SYMLINK_NOFOLLOW = 0x100


def birth_usec(path):
    """The creation time in µs as journald reads it (getcrtime_at): the
    older of statx's btime and the user.crtime_usec xattr; None without
    either."""
    out = []
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        buf = ctypes.create_string_buffer(256)
        rc = libc.statx(-100, os.fsencode(path), AT_SYMLINK_NOFOLLOW, STATX_BTIME, buf)
        mask = struct.unpack_from("<I", buf, 0)[0]
        sec, nsec = struct.unpack_from("<qI", buf, 80)
        if rc == 0 and mask & STATX_BTIME and sec:
            out.append(sec * 1_000_000 + nsec // 1000)
    except (OSError, AttributeError):
        pass
    try:
        raw = os.getxattr(path, "user.crtime_usec", follow_symlinks=False)
        if len(raw) == 8:
            out.append(struct.unpack("<Q", raw)[0])
    except OSError:
        pass
    return min(out) if out else None


def empty(path, st):
    """journal_file_empty(): no header, or n_entries 0. None when it cannot
    be read (journald, as root, could)."""
    if st.st_size < HEADER_SIZE:
        return True
    try:
        with open(path, "rb") as f:
            f.seek(N_ENTRIES_AT)
            raw = f.read(8)
    except OSError:
        return None
    return len(raw) == 8 and struct.unpack("<Q", raw)[0] == 0


def usec(t):
    return int(t * 1_000_000)


def candidate(path, name, st):
    """(seqnum id or None, seqnum, realtime) for a file that can go, or None
    for one that only counts."""
    m = ARCHIVED.match(name)
    if m:
        seqnum_id = m.group(1).lower()
        seqnum, realtime = int(m.group(2), 16), int(m.group(3), 16)
    else:
        m = CORRUPTED.match(name)
        if not m:
            return None
        seqnum_id, seqnum, realtime = None, 0, int(m.group(1), 16)
    # patch_realtime(): the file may be older than its name says
    stamps = [usec(st.st_ctime), usec(st.st_atime), usec(st.st_mtime), birth_usec(path)]
    realtime = min([realtime, *[s for s in stamps if s]])
    return seqnum_id, seqnum, realtime


def order(a, b):
    """vacuum_info_compare(), on (seqnum id, seqnum, realtime, name)."""
    if a[0] is not None and a[0] == b[0]:
        return (a[1] > b[1]) - (a[1] < b[1])
    if a[2] != b[2]:
        return (a[2] > b[2]) - (a[2] < b[2])
    if a[0] is not None and b[0] is not None:
        return (a[0] > b[0]) - (a[0] < b[0])
    return (a[3] > b[3]) - (a[3] < b[3])


def folder(directory, limit):
    """(paths journald removes from `directory`, unreadable: a file whose
    emptiness could not be read, so the set cannot be known)."""
    total, listed, gone = 0, [], []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return [], True
    for name in names:
        if not name.endswith((".journal", ".journal~")):
            continue
        path = os.path.join(directory, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        size = st.st_blocks * 512
        key = candidate(path, name, st)
        if key is None:
            total += size
            continue
        is_empty = empty(path, st)
        if is_empty is None:
            return [], True
        if is_empty:
            gone.append(path)
            continue
        listed.append((*key, name, path, size))
        total += size
    for item in sorted(listed, key=functools.cmp_to_key(order)):
        if total <= limit:
            break
        gone.append(item[4])
        total -= min(item[5], total)
    return gone, False


def vacuumed(top, limit):
    """(every path `journalctl --vacuum-size=<limit>` removes under `top`,
    each journal folder on its own, unreadable)."""
    out, unreadable = [], False
    for d, dirs, _ in os.walk(top):
        dirs.sort()
        paths, bad = folder(d, limit)
        out += paths
        unreadable = unreadable or bad
    return sorted(out), unreadable
