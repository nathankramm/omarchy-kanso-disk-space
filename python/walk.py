"""One careful directory walk, shared by the categories and the ledger.

Sizes are allocated bytes (st_blocks * 512); a hard-linked file counts once;
symlinks are never followed. The walk never enters, or even stats, a mount
point of another device: entering an autofs mount point mounts it, and on
the test machine one is a network share (docs/machine-facts.md, dev repo). Which mount points are
foreign comes from the mount table (D3), checked before any stat.
"""

import os
import stat
import time

import mounts

SKIP = frozenset({"/proc", "/sys", "/dev", "/run"})


def foreign_mount_points(mount_table):
    """Mount points the walk must not enter: another source device or
    filesystem type than `/`, or a bind mount repeating part of a mount it
    already walks (its root lies inside another same-device mount's root)."""
    root = mounts.root_mount(mount_table)
    if root is None:
        return frozenset()
    same = [
        m
        for m in mount_table
        if m["source"] == root["source"] and m["fstype"] == root["fstype"]
    ]
    out = set()
    for m in mount_table:
        if m not in same:
            out.add(m["mount_point"])
            continue
        for other in same:
            if other is m or other["mount_point"] == m["mount_point"]:
                continue
            inside = m["root"].startswith(other["root"].rstrip("/") + "/")
            repeat = m["root"] == other["root"] and same.index(other) < same.index(m)
            if inside or repeat:
                out.add(m["mount_point"])
    out.discard(root["mount_point"])
    return frozenset(out)


class Walk:
    """Walks one tree; `sizes` maps each counted path's bucket to bytes.

    bucket(path) names where a file's bytes go (None: drop them). The walk
    stops at `deadline` (time.monotonic()) and then reports complete False."""

    def __init__(self, foreign, bucket, deadline=None):
        self.foreign = foreign
        self.bucket = bucket
        self.deadline = deadline
        self.skip = SKIP
        self.sizes = {}
        self.seen = set()
        self.unreadable = []
        self.complete = True

    def add(self, path, st):
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            if key in self.seen:
                return
            self.seen.add(key)
        size = st.st_blocks * 512
        key = self.bucket(path)
        if key is not None:
            self.sizes[key] = self.sizes.get(key, 0) + size

    def run(self, top):
        stack = [top]
        while stack:
            if self.deadline is not None and time.monotonic() > self.deadline:
                self.complete = False
                return self
            self.directory(stack.pop(), stack)
        return self

    def directory(self, path, stack):
        try:
            entries = os.scandir(path)
        except OSError:
            self.unreadable.append(path)
            return
        with entries:
            for entry in entries:
                child = entry.path
                if child in self.foreign or child in self.skip:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                self.add(child, st)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(child)


def contained(paths, foreign):
    """(bytes deleting every one of `paths` together frees, unreadable paths)
    on a filesystem with no shared extents and no compression (ext4, D76):
    each file's allocated bytes, a hard-linked file only when all of its
    links are among `paths`, each inode once."""
    links, freed, unreadable = {}, 0, []

    def count(path, st):
        nonlocal freed
        size = st.st_blocks * 512
        if st.st_nlink <= 1 or not stat.S_ISREG(st.st_mode):
            freed += size
            return
        key = (st.st_dev, st.st_ino)
        seen = links.setdefault(key, [size, 0, st.st_nlink])
        seen[1] += 1

    # a path inside another listed path is counted with it, never twice
    tops = sorted(set(paths))
    tops = [t for t in tops if not any(t.startswith(o.rstrip("/") + "/") for o in tops)]
    for top in tops:
        try:
            st = os.stat(top, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            unreadable.append(top)
            continue
        count(top, st)
        if not stat.S_ISDIR(st.st_mode):
            continue
        stack = [top]
        while stack:
            d = stack.pop()
            try:
                entries = os.scandir(d)
            except OSError:
                unreadable.append(d)
                continue
            with entries:
                for entry in entries:
                    if entry.path in foreign:
                        continue
                    try:
                        est = entry.stat(follow_symlinks=False)
                    except OSError:
                        # uncounted, so the total is not exact
                        unreadable.append(entry.path)
                        continue
                    count(entry.path, est)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
    freed += sum(size for size, seen, nlink in links.values() if seen >= nlink)
    return freed, unreadable


def measure(top, foreign, deadline=None):
    """(bytes, unreadable paths, complete) of `top` and everything under it,
    the top directory itself included; (None, [top], True) if it cannot be read."""
    try:
        st = os.stat(top, follow_symlinks=False)
    except FileNotFoundError:
        return 0, [], True
    except OSError:
        return None, [top], True
    w = Walk(foreign, lambda _path: "all", deadline)
    w.add(top, st)
    if os.path.isdir(top) and not os.path.islink(top):
        before = len(w.unreadable)
        w.run(top)
        if w.unreadable[before:] == [top]:
            return None, [top], True
    return w.sizes.get("all", 0), w.unreadable, w.complete
