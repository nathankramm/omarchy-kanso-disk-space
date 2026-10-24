"""Every text the engine prints about sizes, rates, dates and times (§6).

Binary GiB everywhere: one decimal below 100, whole numbers from 100 up,
except the Usage tab, which reads one decimal at every size (D132).
Free space is truncated, never rounded up, so a value on screen never sits on
the safe side of a line it has already crossed (D11: 9.96 GiB free is
"9.9 GiB", never "10.0", while updates are blocked below 10). Every other size
rounds half up. Integer arithmetic throughout: a float would round 0.05 steps
differently on different values.
"""

import datetime

GIB = 1024**3
DAY = 86400
# a sample this far ahead of the clock is from a wrong clock
FUTURE_SLACK = 3600
MINUS = "−"  # a real minus sign, as typeset
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # fmt: skip


def _tenths(size, floor):
    """|size| in tenths of a GiB, truncated or rounded half up."""
    size = abs(size)
    return size * 10 // GIB if floor else (size * 10 + GIB // 2) // GIB


def _wholes(size, floor):
    size = abs(size)
    return size // GIB if floor else (size + GIB // 2) // GIB


def gib_number(size, floor=False):
    """'27.4' or '412', no unit and no sign."""
    tenths = _tenths(size, floor)
    if tenths < 1000:
        return f"{tenths // 10}.{tenths % 10}"
    return str(_wholes(size, floor))


def shown_tenths(size, floor=False):
    """The tenths of a GiB a non-negative size reads as: tenths below 100 GiB,
    whole GiB (as tenths) from 100 up."""
    tenths = _tenths(size, floor)
    return tenths if tenths < 1000 else _wholes(size, floor) * 10


def tenths(size, floor=False):
    """A size in tenths of a GiB at every size, truncated or rounded half up,
    with its sign: the Usage tab's readings (D132)."""
    t = _tenths(size, floor)
    return -t if size < 0 else t


def usage_number(tenths):
    """'951.9': one decimal at every size, so Usage's figures add up (D132)."""
    sign = MINUS if tenths < 0 else ""
    return f"{sign}{abs(tenths) // 10}.{abs(tenths) % 10}"


def tenths_text(tenths):
    """'27.4 GiB' from tenths; whole GiB from 100 up (half up)."""
    if tenths < 1000:
        return f"{tenths // 10}.{tenths % 10} GiB"
    return f"{(tenths + 5) // 10} GiB"


def apportion(sizes, total, per=10, keep_nonzero=True):
    """Parts of a GiB (tenths by default, whole GiB with per=1) for each
    non-negative size, summing exactly to `total` parts (D83, D85): each
    floored, then the parts left over go to the largest remainders (any over,
    from the smallest). With keep_nonzero a nonzero size never reads 0."""
    base = [s * per // GIB for s in sizes]
    rem = [s * per % GIB for s in sizes]
    least = [1 if s > 0 and keep_nonzero else 0 for s in sizes]
    for i, s in enumerate(sizes):
        if base[i] < least[i]:
            base[i], rem[i] = least[i], -1  # already rounded up past its share
    diff = total - sum(base)
    up = sorted(range(len(sizes)), key=lambda i: (-rem[i], -sizes[i], i))
    while diff > 0 and up:
        for i in up:
            if diff == 0:
                break
            base[i] += 1
            diff -= 1
    down = sorted(range(len(sizes)), key=lambda i: (rem[i], sizes[i], i))
    while diff < 0:
        moved = False
        for i in down:
            if diff == 0:
                break
            if base[i] > least[i]:
                base[i] -= 1
                diff += 1
                moved = True
        if not moved:
            break
    return base


def gib(size, floor=False):
    """'27.4 GiB'; a negative size carries a real minus sign."""
    sign = MINUS if size < 0 and _tenths(size, floor) else ""
    return f"{sign}{gib_number(size, floor)} GiB"


# a nonzero size too small for "0.1 GiB": never "0.0 GiB" (D86)
SMALL = "under 0.1 GiB"


def reading(size, floor=False):
    """gib(), but a nonzero size that would read "0.0 GiB" reads SMALL."""
    if size and _tenths(size, floor) == 0:
        return SMALL
    return gib(size, floor)


def whole_gib(size):
    """Whole GiB, rounded half up, no unit: the ledger's sub-lines."""
    return str(_wholes(size, floor=False))


def signed_gib(size):
    """'+18.2 GiB' or '−9.0 GiB', rounded half up; '0.0 GiB' unsigned."""
    if _tenths(size, floor=False) == 0:
        return "0.0 GiB"
    sign = MINUS if size < 0 else "+"
    return f"{sign}{gib_number(size)} GiB"


def free_gib(avail):
    """Free space, truncated (D11)."""
    return gib(avail, floor=True)


def bar_number(avail):
    """The bar's whole-number free space, truncated: '27G'."""
    return f"{max(avail, 0) // GIB}G"


def rate_tenths(bytes_per_day):
    """|rate| in tenths of a GiB a day, half up: 0 reads "steady"."""
    return (abs(round(bytes_per_day)) * 10 + GIB // 2) // GIB


def rate_number(bytes_per_day):
    """|rate| in GiB a day with one decimal, rounded half up: '1.1'."""
    tenths = rate_tenths(bytes_per_day)
    return f"{tenths // 10}.{tenths % 10}"


def local(t):
    return datetime.datetime.fromtimestamp(t).astimezone()


def midnight(day):
    """Epoch seconds of a local date's midnight."""
    return int(datetime.datetime(day.year, day.month, day.day).astimezone().timestamp())


def stamp(t):
    """'2026-10-22 21:20 CDT': the report's time (local)."""
    return local(t).strftime("%Y-%m-%d %H:%M %Z")


def whole(n, minimum=None):
    """An int that is not a bool (and at least `minimum`, when given)."""
    return (
        isinstance(n, int)
        and not isinstance(n, bool)
        and (minimum is None or n >= minimum)
    )


def date_text(t):
    """'Nov 1' in local time."""
    d = local(t)
    return f"{MONTHS[d.month - 1]} {d.day}"


def clock(t):
    d = local(t)
    return f"{d.hour:02d}:{d.minute:02d}"


def day_text(t, now):
    """'today', 'yesterday', else 'Oct 20' (local)."""
    day, today = local(t).date(), local(now).date()
    if day == today:
        return "today"
    if day == today - datetime.timedelta(days=1):
        return "yesterday"
    return date_text(t)


def when_text(t, now):
    """'23:45' today, 'yesterday 23:45', else 'Oct 20 23:45' (local)."""
    day = day_text(t, now)
    return clock(t) if day == "today" else f"{day} {clock(t)}"


def in_days_text(days):
    """'today', 'in 1 day', 'in 11 days' from whole days."""
    if days <= 0:
        return "today"
    return "in 1 day" if days == 1 else f"in {days} days"
