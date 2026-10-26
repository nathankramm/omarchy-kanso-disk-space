"""What the card may show exactly on this machine (D76, D77, D79, D101).

No estimated size appears anywhere (D78). Free space, the bar, the levels,
the trend and every notification come from statvfs and are always exact.
Sizes inside the disk are exact only where something measured them:

    native       ext4: no compression and no shared extents, so the scan's
                 own walk is exact (hard links counted once, freed only when
                 every link goes); the whole card from the start
    exact        btrfs with a current measure helper whose last run measured
    setup        btrfs without the helper: the one-time setup panel
    update       btrfs with a helper that differs from the plugin's: the same
                 panel with Update
    pending      btrfs with a current helper not yet run (no last run, or the
                 last scan ran before setup or before the Update: "absent",
                 "out-of-date"): measuring, the panel busy (D149)
    measuring    btrfs with a current helper running now and no measured scan:
                 only what is exact, and the quiet "Measuring exact sizes"
                 block; with one, the whole card and the same block (D149)
    refused      the helper would not measure (another login user, D72; a
                 symlink on the way to the plugin, D110): only what is
                 exact, and why in one line
    failed       the last run did not finish and no measured scan exists:
                 only what is exact, and one line (with one, the card keeps
                 the last measured scan, D79; a sudo rule gone with none
                 asks for setup again). Once SLOW_AFTER runs in a row have
                 timed out, the line says the disk is too slow (D101)
    retry        the last run timed out once and no measured scan exists:
                 the next scan asks for the essentials only (D121), and
                 says nothing until that one fails too
    unavailable  any other filesystem: only what is exact, and one line

`block()` gives what the card's `exactness` object is made from (the card
leaves out `mode`, `text` and `issue`); every string is here. An issue
(link, shared, refused, failed, slow, update, measuring) is a warning block on the card
(notices.py, D119); the plain `line` is left for setup, pending, retry and
unavailable.
"""

import units

FS_NAMES = {"xfs": "XFS", "zfs": "ZFS", "bcachefs": "bcachefs", "f2fs": "F2FS",
            "ext3": "ext3", "ext2": "ext2", "vfat": "FAT", "ntfs3": "NTFS"}  # fmt: skip
SETUP_LINE = "One-time setup needed · click the drive icon"
UPDATE_LINE = "Helper update needed · click the drive icon"
PENDING_LINE = "Measuring exact sizes…"
MEASURING_LINE = "Measuring exact sizes"
# a last run of no current helper: the scan before setup, or before the
# Update; the helper now installed has not run (D149)
UNRUN = ("absent", "out-of-date")
FAILED_LINE = "The last measurement didn't finish"
SHARED_LINE = "Exact sizes need a single-user machine: {why}"
REFUSED_LINE = "The helper refused this measurement"
# the helper's own reason, shortened to fit one line (D110)
LINK_LINES = {
    "the plugin folder is reached through a symlink": (
        "Exact sizes off: the plugin folder is behind a symlink"
    ),
    "the plugin's copy of this helper is a symlink": (
        "Exact sizes off: the helper's plugin copy is a symlink"
    ),
}
SLOW_LINE = "The disk is too slow to measure in time"
SLOW_AFTER = 2
# the out-of-date helper's body (D80, D81): the same panel, with Update
# broken after its first sentence: never "sizes." alone on a line
UPDATE_BODY = "This version changes the helper.\nUpdate it to keep exact sizes."
PANEL = {
    "title": "Exact sizes",
    "body": "Linux shows true on-disk sizes only to administrators. A one-time setup "
    "adds a small read-only helper.",
    # broken as the mockup breaks it: never "anytime." alone on a line
    "note": "Installs compsize, a read-only helper\nand one sudo rule. Removable anytime.",
    "not_now": "Not now",
    "running": "Setting up…",
    "cancelled": "Setup was cancelled. Nothing was installed.",
    "needs_admin": "Setup needs an administrator.",
    "failed": "Setup didn't finish. Nothing more was changed.",
    "terminal": "Setup continues in the terminal",
}
FULL = ("exact", "native")


def fs_name(fstype):
    return FS_NAMES.get(fstype, fstype or "this filesystem")


def shared_text(reason):
    if reason == "another login user":
        return SHARED_LINE.format(why="another login user exists")
    if reason.startswith("another user's folder"):
        return SHARED_LINE.format(why="another user's folder is in /home")
    return REFUSED_LINE


def link_line(reason):
    """The card's line for the helper's symlink refusal (D110)."""
    return next((line for words, line in LINK_LINES.items() if words in reason),
                REFUSED_LINE)  # fmt: skip


def run_state(marker, last, now, alive, limit):
    """Whether the helper's run in measure-run.json is going on (D149):
    "running" while its scan is alive and within `limit` seconds (the
    helper's time limit and a margin), "abandoned" once a run that never
    wrote measure.json is dead or past its limit, "" with no run or one that
    finished (its measure.json is as new as its start)."""
    if not marker:
        return ""
    started = marker["started_at"]
    done = (last or {}).get("measured_at")
    if units.whole(done, 0) and done >= started:
        return ""
    if alive and -units.FUTURE_SLACK <= now - started <= limit:
        return "running"
    return "abandoned"


def unrun(last):
    """Whether the last run was of no current helper (D149)."""
    return (last or {}).get("state") in UNRUN


def mode_of(fstype, status, last, measured_at, run=""):
    """(mode, line) from the root filesystem, the helper's status
    (measure.status), the last run's result (measure.json), when the
    measured scan was made (0: none) and the run going on (run_state)."""
    measured_scan = measured_at > 0
    if fstype == "ext4":
        return "native", ""
    if fstype != "btrfs":
        return "unavailable", f"Exact sizes aren't available on {fs_name(fstype)}"
    if status == "absent":
        return "setup", SETUP_LINE
    if status == "out-of-date":
        return "update", UPDATE_LINE
    if run == "running":
        # quiet: the run is going on, nothing failed (D149)
        return ("exact" if measured_scan else "measuring"), MEASURING_LINE
    if run == "abandoned":
        # its scan died or ran past its limit without a word: failed
        last = {"state": "abandoned"}
    if not last:
        return "pending", PENDING_LINE
    if unrun(last):
        # the scan before setup (or the Update) is not a failed run (D149)
        return ("exact", "") if measured_scan else ("pending", PENDING_LINE)
    if last.get("state") == "link":
        return "refused", link_line(last.get("reason") or "")
    if last.get("state") == "denied" and not measured_scan:
        return "setup", SETUP_LINE  # the sudo rule is gone: set up again
    exact = last.get("exact_state")
    if last.get("state") == "ok" and exact == "ok":
        return "exact", ""
    if last.get("state") == "ok" and exact == "refused":
        return "refused", shared_text(last.get("reason") or "")
    if retrying(last):
        # one timeout: the next scan asks for the essentials only, and
        # nothing is said unless that fails too (D121)
        return ("exact", "") if measured_scan else ("retry", PENDING_LINE)
    failed = FAILED_LINE
    if last.get("state") == "timeout" and units.whole(last.get("timeouts"), SLOW_AFTER):
        # honest, and the same each day, not a failure repeating (D101)
        # the date of the sizes kept is the warning's second line (D119)
        failed = SLOW_LINE
    return ("exact", failed) if measured_scan else ("failed", failed)


def retrying(last):
    """Whether the last run timed out once: the next scan is the essentials
    retry (D121)."""
    last = last or {}
    timeouts = last.get("timeouts")
    return (
        last.get("state") == "timeout"
        and units.whole(timeouts, 1)
        and timeouts < SLOW_AFTER
    )


def issue_of(mode, last, text):
    """The warning a mode's line becomes (D119): link · shared · refused ·
    slow · failed · update · measuring (D149), or None for a plain line (or
    none)."""
    if mode == "update":
        return "update"
    if text == MEASURING_LINE:
        return "measuring"
    if mode == "refused":
        if (last or {}).get("state") == "link":
            return "link"
        return "shared" if text.startswith(SHARED_LINE[:20]) else "refused"
    if text.startswith(SLOW_LINE):
        return "slow"
    if text == FAILED_LINE:
        return "failed"
    return None


def block(fstype, status, last, measured_at, run=""):
    """{mode, full (the whole card), trend_only, text (the mode's own line,
    for the report), issue (the warning it is on the card, notices.py), line
    (the Glance's one plain line when there is no issue, or ""), panel
    (setup · update · pending: every panel string, the button word, and
    `busy` for pending)}."""
    mode, text = mode_of(fstype, status, last, measured_at, run)
    panel = None
    if mode in ("setup", "update", "pending"):
        panel = {**PANEL, "button": "Update" if mode == "update" else "Set up",
                 "busy": mode == "pending"}  # fmt: skip
        if mode == "update":
            panel["body"] = UPDATE_BODY
    full = mode in FULL
    issue = issue_of(mode, last, text)
    return {
        "mode": mode,
        "full": full,
        "trend_only": not full and panel is None,
        "text": text,
        "issue": issue,
        "line": "" if full or issue else text,
        "panel": panel,
    }
