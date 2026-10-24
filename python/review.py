"""The review condition (D58, amends blueprint §6 and §8.4; D59: off the bar).

One rule, scaled to the disk: the review condition holds when the Reclaim
hero's number (what cleaning frees, never the rollback points, D54) reaches
max(10 GiB, 5 % of the disk) above the last seen value.

    seen       review.json {schema 1, seen_bytes, seen_at}: the reclaimable
               when Reclaim was last opened in the full view or Ask agent ran;
               no file is a seen value of 0
    rebase     a scan that finds less than seen_bytes (a cleanup) lowers
               seen_bytes to it, so the next step counts from there

It never notifies and applies at any level. It shows in the Glance only,
which paints its "N GiB reclaimable" footer in accent; the bar never
changes for it (D59).
"""

import units
import views

GIB = units.GIB
REVIEW_MIN = 10 * GIB
REVIEW_PERCENT = 5


def line(size):
    """The threshold step for a disk of `size` bytes: 47.6 GiB on 952 GiB."""
    return max(REVIEW_MIN, size * REVIEW_PERCENT // 100)


def reclaimable(scan):
    """The Reclaim hero's number in bytes, or None before a scan."""
    if scan is None:
        return None
    return sum(views.totals(scan["categories"]))


def seen_bytes(doc):
    """The baseline from review.json; 0 when nothing was ever seen."""
    if not isinstance(doc, dict):
        return 0
    value = doc.get("seen_bytes")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def holds(scan, size, doc, have=None):
    """True when reclaimable has grown a whole step past the seen value;
    `have` is the card's reclaimable when it has it."""
    have = reclaimable(scan) if have is None else have
    if have is None:
        return False
    return have >= seen_bytes(doc) + line(size)


def seen(scan, now):
    """review.json for "seen now", or None before a scan."""
    have = reclaimable(scan)
    if have is None:
        return None
    return {"schema": 1, "seen_bytes": have, "seen_at": now}


def rebase(doc, scan, now):
    """review.json after a scan, or None when it does not change: a cleanup
    (reclaimable below the seen value) resets the baseline to the new low."""
    have = reclaimable(scan)
    if have is None or not isinstance(doc, dict) or have >= seen_bytes(doc):
        return None
    return {**doc, "schema": 1, "seen_bytes": have, "rebased_at": now}
