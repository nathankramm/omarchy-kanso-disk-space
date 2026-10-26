#!/usr/bin/env python3
"""Kanso Disk Space engine: every number and string the bar and card show.

    disk_space.py tick     one statvfs sample, appended; prints the card + notify list
    disk_space.py card     the card from the current state, no new sample
    disk_space.py scan     measures the categories and the ledger (scan.json,
                           ledger.json; nice 19, idle I/O, 5-minute limit), then
                           prints the card. Service runs it daily
    disk_space.py viewed   records a visit to the full view (views.json), the
                           change line's "since you last looked" (D115), then
                           prints the card. Service runs it on each full view
    disk_space.py seen     records the reclaimable now as the review baseline
                           (review.json, D58), then prints the card. Service runs
                           it when the full view shows Reclaim; `ask` runs it too
    disk_space.py preview DIR
                           the card for a saved fixture state (DIR holds the
                           state files and preview.json: now, settings,
                           readonly, boot, fstype, status, dua, tz), never
                           writing anything: the preview mode (D130)
    disk_space.py report --md|--json|--goal [--out PATH]
                           the cleanup report (report.py), printed or written
                           atomically to PATH; --goal: the Ask agent prompt's
                           first sentence (D89)

Widget settings: --watch --low --critical --horizon (integers, GiB and days),
--notifications 0|1, --number-on-bar 0|1, --totals-only 0|1 (the report for
cloud agents names no folder or file, D127). Service passes them all on every
tick; a verb run without them uses the ones the last tick stored in
alerts.json, else the defaults (D29).

Options for every verb: --now <epoch seconds | ISO time> (tests; default the
clock). State lives in $XDG_STATE_HOME/kanso-disk-space (default
~/.local/state/kanso-disk-space, D6) and is written by this engine only:
whole-file writes are a temp file plus os.replace, samples are appended one
whole line per write.

Free space is `f_bavail * f_frsize` of statvfs("/"), which equals
`df --output=avail --block-size=1 /`, the number `omarchy update` refuses to
run under (§4.1, docs/machine-facts.md row 2, dev repo).

Test hooks (environment):
    KANSO_DISK_FAKE_AVAIL=<bytes>   tick uses this free value instead of statvfs;
                                    the sample is marked "fake": true (§5)
    KANSO_DISK_TEST_STATVFS=<json>  {"frsize", "blocks", "bfree", "bavail"} (tests only)
    KANSO_DISK_TEST_BOOT_ID=<id>    the boot id (tests only)
    KANSO_DISK_TEST_ROOT=<dir>      scan walks this tree instead of / (tests only)
    KANSO_DISK_TEST_MOUNTINFO=<f>   the mount table (tests only)
    KANSO_DISK_TEST_SYSFS_BTRFS=<d> /sys/fs/btrfs (tests only)
    KANSO_DISK_TEST_READONLY, KANSO_DISK_TEST_BOOT, KANSO_DISK_TEST_MISE,
    KANSO_DISK_TEST_CACHE_TOOLS:    health.py and categories.py (tests only)

The card's fields: docs/card-contract.md (the CARD CONTRACT).
"""

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import math
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Never write __pycache__ into the plugin folder: the shell reloads a local
# plugin on any file written under it, so each first import would restart the
# Service and tick again (D39).
sys.dont_write_bytecode = True

import alerts
import categories
import changes
import chart
import cleanups
import exactness
import forecast
import health
import ledger
import levels
import measure
import mounts
import notices
import report
import review
import theme
import trend
import units
import views

SCHEMA = 4
GIB = units.GIB
APP = "kanso-disk-space"
KEEP_SECONDS = 120 * 86400  # §5: samples are kept 120 days
COMPACT_SLACK = 86400  # rewrite the file at most about once a day
STALE_AFTER = 30 * 60  # two missed 15-minute ticks: Service has stalled
SCAN_EVERY = 24 * 3600  # §5: daily


# a preview's fixture folder in place of the state folder (D130)
PREVIEW_DIR = None


def state_dir():
    if PREVIEW_DIR:
        return PREVIEW_DIR
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, APP)


def real_state_dir():
    """The real user's state folder: the default under the home folder in
    the password database, which a test's HOME and XDG_STATE_HOME cannot
    move. KANSO_DISK_TEST_REAL_HOME stands in for it in the guard's own
    tests, which must never aim at the real one."""
    home = (
        os.environ.get("KANSO_DISK_TEST_REAL_HOME") or pwd.getpwuid(os.getuid()).pw_dir
    )
    return os.path.join(home, ".local", "state", APP)


def refuses_real_state():
    """Under the test suite (KANSO_DISK_TESTING=1), tick and scan never
    write the real state folder (D38)."""
    if os.environ.get("KANSO_DISK_TESTING") != "1":
        return False
    return os.path.realpath(state_dir()) == os.path.realpath(real_state_dir())


def state_file(name):
    """A state file; cleanups.jsonl is the one the user's agent appends to
    (skill rule 7). A preview fixture holds only what makes its state, and
    reads the rest from its sibling _base (D130)."""
    path = os.path.join(state_dir(), name)
    if PREVIEW_DIR and not os.path.exists(path):
        base = os.path.join(os.path.dirname(PREVIEW_DIR), "_base", name)
        if os.path.exists(base):
            return base
    return path


def write_json(path, doc):
    atomic_write(path, json.dumps(doc, indent=1) + "\n")


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "omarchy", APP)


def parse_now(value):
    """Epoch seconds from --now (epoch seconds or an ISO time), else the clock."""
    if value is None:
        return int(time.time())
    try:
        return int(float(value))
    except ValueError:
        pass
    d = datetime.datetime.fromisoformat(value)
    if d.tzinfo is None:
        d = d.astimezone()
    return int(d.timestamp())


def read_statvfs():
    """{frsize, blocks, bfree, bavail} of "/" (or the test hook's values)."""
    hook = os.environ.get("KANSO_DISK_TEST_STATVFS")
    if hook:
        doc = json.loads(hook)
        return {k: int(doc[k]) for k in ("frsize", "blocks", "bfree", "bavail")}
    s = os.statvfs("/")
    return {
        "frsize": s.f_frsize,
        "blocks": s.f_blocks,
        "bfree": s.f_bfree,
        "bavail": s.f_bavail,
    }


def boot_id():
    hook = os.environ.get("KANSO_DISK_TEST_BOOT_ID")
    if hook:
        return hook
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def fake_avail():
    """KANSO_DISK_FAKE_AVAIL as bytes, or None. A malformed value is an error:
    a level test that silently measured the real disk would pass for the
    wrong reason."""
    value = os.environ.get("KANSO_DISK_FAKE_AVAIL")
    if value is None or value == "":
        return None
    # one plain message for every malformed value: it is what the card shows
    try:
        n = int(value)
    except ValueError:
        n = -1
    if n < 0:
        raise ValueError("KANSO_DISK_FAKE_AVAIL must be a byte count >= 0")
    return n


def take_sample(now):
    fs = read_statvfs()
    sample = {
        "t": now,
        "avail": fs["bavail"] * fs["frsize"],
        "size": fs["blocks"] * fs["frsize"],
        "boot_id": boot_id(),
    }
    fake = fake_avail()
    if fake is not None:
        sample["avail"] = fake
        sample["fake"] = True
    return sample


def atomic_write(path, text):
    """A temp file in the same directory, then os.replace: never a torn file."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)  # own-file: our own temp file
        raise


@contextlib.contextmanager
def state_lock():
    """One writer at a time: a tick from Service and one by hand must not
    interleave their read-modify-write of alerts.json."""
    directory = state_dir()
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, ".lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def valid_sample(doc):
    if not isinstance(doc, dict):
        return False
    t, avail, size = doc.get("t"), doc.get("avail"), doc.get("size")
    numbers = (t, avail, size)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in numbers):
        return False
    if not all(math.isfinite(v) for v in numbers):
        return False
    return avail >= 0 and size > 0 and isinstance(doc.get("boot_id", ""), str)


def load_samples(path):
    """Valid samples in file order. A corrupt or torn line is skipped, never
    repaired: the file is append-only in spirit."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    doc = json.loads(line)
                except ValueError:
                    continue
                if valid_sample(doc):
                    doc["t"] = int(doc["t"])
                    doc["avail"] = int(doc["avail"])
                    doc["size"] = int(doc["size"])
                    doc.setdefault("boot_id", "")
                    out.append(doc)
    except FileNotFoundError:
        pass
    return out


def append_sample(path, sample):
    """One whole line per write. If the file ends in a torn line (a crash
    mid-write), start on a new line so the next sample is not glued to it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(sample, separators=(",", ":")) + "\n"
    with open(path, "ab+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                line = "\n" + line
        f.write(line.encode())


def compact_samples(path, samples, now):
    """Drop samples older than 120 days, at most about once a day."""
    if not samples or samples[0]["t"] >= now - KEEP_SECONDS - COMPACT_SLACK:
        return samples
    kept = [s for s in samples if s["t"] >= now - KEEP_SECONDS]
    atomic_write(
        path, "".join(json.dumps(s, separators=(",", ":")) + "\n" for s in kept)
    )
    return kept


def newest(samples, now, real_only=False):
    """The newest usable sample: not from the future (a wrong clock)."""
    for s in reversed(samples):
        if s["t"] > now + units.FUTURE_SLACK:
            continue
        if real_only and s.get("fake"):
            continue
        return s
    return None


def read_cleanups():
    """(valid entries in file order, skipped lines). Every skipped line is a
    warning on stderr, never a failure (D36)."""
    try:
        with open(
            state_file("cleanups.jsonl"), encoding="utf-8", errors="replace"
        ) as f:
            entries, skipped = cleanups.parse(f.read())
    except OSError:
        return [], []
    for s in skipped:
        print(
            f"kanso-disk-space: warning: cleanups.jsonl line {s['line']} skipped: "
            f"{s['reason']}",
            file=sys.stderr,
        )
    return entries, skipped


def read_last_cleanup():
    entries, _ = read_cleanups()
    return entries[-1] if entries else None


def protect_path():
    return os.path.join(config_dir(), "protect.json")


def walk_root():
    """KANSO_DISK_TEST_ROOT: tests walk a fake tree instead of /."""
    return os.environ.get("KANSO_DISK_TEST_ROOT") or "/"


def plugin_bin():
    return os.path.join(os.path.dirname(HERE), "bin", "kanso-disk-space")


def settings_from(args, stored):
    """(settings, notice): each flag Service passes, else the value the
    last tick stored, else the default. The right-click menu and a
    notification click pass none, and must still use the user's lines (D29).
    notice is "" or, for lines that do not fall, the lines as given."""
    stored = stored if isinstance(stored, dict) else {}
    s = {}
    for key in levels.DEFAULTS:
        given = getattr(args, key)
        default = levels.DEFAULTS[key]
        if given is not None:
            s[key] = bool(given) if isinstance(default, bool) else given
        elif type(stored.get(key)) is type(default):
            s[key] = stored[key]
        else:
            s[key] = default
    if levels.settings_valid(s):
        return s, ""
    # the lines as given, for the level settings' What now (D131)
    notice = {k: s[k] for k in ("watch", "low", "critical")}
    # invalid lines never widen the report: totals only stays as set (D127)
    return {**levels.DEFAULTS, "totals_only": s["totals_only"]}, notice


def base_card(now, state, message):
    return {
        "schema": SCHEMA,
        "state": state,
        "message": message,
        "as_of": now,
        "notify": [],
        "level": {"id": "fine", "word": "", "colour": "muted"},
        "bar": {
            "glyph": alerts.GLYPH,
            "text": "",
            # no sample yet or an engine error: dim, Omarchy's "inactive"
            "colour": "muted",
            # once the Service has stalled the glyph dims, Omarchy's "inactive" (D67)
            "stale_colour": "muted",
            # the full view's page a click opens (D117)
            "click_page": "used",
            # the What now panel a click opens instead, or "" (D131)
            "click_panel": "",
        },
        "glance": {
            "hero_text": "",
            "hero_unit": "GiB",
            "hero_caption": "",
            "stale_after": 0,
            "stale_steps": [],
            # what changed since the user last looked (D115), or ""
            "change_text": "",
            "disk": {
                "used": 0.0,
                "colour": "accent",
                "used_text": "",
                "free_text": "free",
            },
            "scanned": False,
            "running_text": views.RUNNING_TEXT,
            "where_rows": [],
            "footer_text": "",
            "footer_colour": "muted",
        },
        "exactness": {
            "full": False,
            "trend_only": False,
            "line": "",
            # before the first scan (scan_at 0) a scan is due
            "scan_due": True,
            "panel": None,
        },
        "trend": {
            "line": {"text": "", "style": "dim"},
            "rows": [],
            "sub_text": chart.SUB_TEXT,
        },
        "chart": {
            "points": [],
            "lines": [],
            "band": {"y": 0.0, "text": ""},
            "forecast": None,
            "jumps": [],
            "months": [],
            "note_text": "",
        },
        "reclaim": views.reclaim_view(None, now),
        "used": views.used_view(None, now, "muted", False),
        "palette": {"yellow": ""},
        "scan_at": 0,
        # every warning, gravest first, and the What now panels (D119, D120)
        "warnings": [],
        "panels": {},
        "strings": notices.strings(),
    }


# The Glance's whole-disk bar (D49): the used part in the level's colour,
# except Fine, whose level colour is muted: there it is accent.
BAR_COLOURS = {**levels.COLOURS, "fine": "accent"}


# The levels whose bar click opens Cleanup (D117)
CLICK_TO_CLEANUP = ("low", "critical", "blocked")


# The bar glyph (D59): Omarchy's bar draws status icons in foreground and
# keeps dim for inactive or empty, so Fine and Watch are foreground like
# every other icon; colour is for problems. The card keeps levels.COLOURS.
GLYPH_COLOURS = {**levels.COLOURS, "fine": "foreground"}


def stale_steps(t):
    """The Glance's urgent line once the Service has stalled (D67), as steps
    the QML picks by the clock (the last whose `after` has passed): "not
    updated since 14:54", from midnight "…since yesterday 14:54", from the
    next "…since Oct 22 14:54"."""
    clock = units.clock(t)
    day = units.local(t).date()
    return [
        {"after": t + STALE_AFTER, "text": f"not updated since {clock}"},
        {
            "after": units.midnight(day + datetime.timedelta(days=1)),
            "text": f"not updated since yesterday {clock}",
        },
        {
            "after": units.midnight(day + datetime.timedelta(days=2)),
            "text": f"not updated since {units.date_text(t)} {clock}",
        },
    ]


def disk_bar(avail, size, level_id):
    """{used (0..1 of the disk), colour, used_text, free_text} for the Glance."""
    used = max(size - avail, 0)
    return {
        "used": round(used / size, 4) if size else 0.0,
        "colour": BAR_COLOURS[level_id],
        "used_text": f"{units.gib(used)} used",
        "free_text": "free",
    }


def card_level(doc, alerts_ok, avail, settings):
    """(level, effective) as the last tick left them; without alerts.json,
    the raw level of the newest sample."""
    if not alerts_ok:
        level = levels.raw_level(avail, levels.lines(settings))
        return level, level
    return doc["level"], doc["effective"]


def metadata_percent(doc):
    """How much of btrfs's metadata is used, in whole percent rounded down,
    from the last tick's check; None without one."""
    meta = (doc or {}).get("metadata") or {}
    if meta.get("state") != "ok" or not meta.get("total"):
        return None
    return meta["used"] * 100 // meta["total"]


def build_card(samples, doc, alerts_ok, settings, settings_notice, now, known=None):
    """The card. `known` holds what the verb already computed this run (the
    mount table and root mount, the helper's status, the ordered series, the
    slow lane and the fast rate), so nothing is worked out twice."""
    known = dict(known or {})
    known.setdefault("readonly", health.readonly())
    if "boot" not in known:
        known["boot"] = health.boot_percent(known.get("table") or mounts.read())
    latest = newest(samples, now)
    if latest is None:
        return base_card(now, "no-samples", "No samples yet")
    card = base_card(now, "ok", "")
    avail, size = latest["avail"], latest["size"]
    level, eff = card_level(doc, alerts_ok, avail, settings)
    colour = levels.COLOURS[eff]
    card["level"] = {"id": eff, "word": levels.WORDS[eff], "colour": colour}
    # the free number beside the glyph from Watch on (D59)
    counted = levels.rank(eff) >= levels.rank("watch")
    card["bar"].update(
        text=units.bar_number(avail) if counted and settings["number_on_bar"] else "",
        colour=GLYPH_COLOURS[eff],
        # at Low and worse a click goes straight to what can be freed (D117)
        click_page="reclaim" if eff in CLICK_TO_CLEANUP else "used",
    )
    card["glance"].update(
        hero_text=units.gib_number(avail, floor=True),
        hero_caption=f"free of {units.gib_number(size)}",
        stale_after=latest["t"] + STALE_AFTER,
        stale_steps=stale_steps(latest["t"]),
        disk=disk_bar(avail, size, eff),
    )
    # metadata, not free space, lifts the level when eff differs (D47)
    known.update(level=eff, metadata=eff != level, totals_only=settings["totals_only"],
                 facts={"free": avail, "low": settings["low"],
                        "metadata": metadata_percent(doc if alerts_ok else None)})  # fmt: skip
    known.setdefault("ledgers", [])
    scan_fields(card, colour, size, settings_notice, now, known)
    series = known.get("series")
    if series is None:
        series = forecast.ordered(samples, now)
    lane = known.get("lane") or forecast.slow_lane(
        samples, now, settings["critical"] * GIB, series
    )
    rate = known.get("rate") or forecast.fast_rate(samples, now, series)
    card["trend"] = {
        "line": trend.glance_line(lane),
        # each big change's rows from the daily ledgers (D115)
        "rows": trend.rows(lane, rate, now, known.get("ledgers", [])),
        "sub_text": chart.SUB_TEXT,
    }
    card["chart"] = chart.build(samples, lane, now, settings, series)
    card["palette"] = theme.level_palette()
    return card


def load_state(path):
    """A state document the engine wrote, or None if missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == 1 else None


def exactness_now(scan, last_run, known=None):
    """exactness.block for this machine now: the root filesystem, the
    helper's state and its last run, measure.json (D76, D77, D79)."""
    known = known or {}
    if "root" in known:
        root = known["root"]
    else:
        root = mounts.root_mount(known.get("table") or mounts.read())
    fstype = root["fstype"] if root else ""
    if "status" in known:
        status = known["status"]
    else:
        status = measure.status() if fstype == "btrfs" else "absent"
    measured_at = scan["measured_at"] if scan and scan.get("measured") else 0
    return exactness.block(fstype, status, last_run, measured_at)


def scan_due(scan_at, last_run, mode, cleanup, now):
    """Service scans when this is true (§5, D94): more than a day after the
    last run (before the first, scan_at is 0), at once for a current helper
    that has not measured (D77), at once after one timeout, for the
    essentials-only retry (D121), and after a cleanup newer than the last run
    (D95). A run is the last scan, or the helper's last run, which a failed
    one does not replace (D79): a run that did not finish is not due again
    for a day (D101), and a cleanup is never due twice. One dated over an
    hour ahead of the clock waits (a wrong clock must not rescan every
    tick)."""
    ran = max(scan_at, (last_run or {}).get("measured_at") or 0)
    if now - ran > SCAN_EVERY or mode == "pending" or exactness.retrying(last_run):
        return True
    if cleanup is None or cleanup["t"] > now + units.FUTURE_SLACK:
        return False
    return cleanup["t"] > ran


def scan_fields(card, colour, size, settings_notice, now, known=None):
    """Everything that comes from the last scan and ledger: the Glance's
    footer and "where the space is" rows, the review condition (D58), and the
    Reclaim and Used views."""
    scan = load_state(state_file("scan.json"))
    doc = load_state(state_file("ledger.json"))
    last_run = load_state(state_file("measure.json"))
    cleanup = read_last_cleanup()
    ex = exactness_now(scan, last_run, known)
    # when it last scanned, shown or not: Service's daily cadence (§5)
    card["scan_at"] = scan["measured_at"] if scan else 0
    card["exactness"] = {k: ex[k] for k in ("full", "trend_only", "line", "panel")}
    measured_at = scan["measured_at"] if scan and scan.get("measured") else 0
    card["warnings"] = notices.build(
        known.get("level"), known.get("metadata"), known.get("readonly"),
        known.get("boot"), health.BOOT_LIMIT, ex, measured_at, settings_notice,
        plugin_bin(),
    )  # fmt: skip
    facts = {**known.get("facts", {}), "boot": known.get("boot"),
             "readonly": known.get("readonly"), "lines": settings_notice or {}}  # fmt: skip
    card["panels"] = notices.panels(facts, plugin_bin(), card["warnings"])
    card["bar"]["click_panel"] = notices.click_panel(card["warnings"])
    # a read-only disk could not keep a scan (D102)
    card["exactness"]["scan_due"] = not (known or {}).get("readonly") and scan_due(
        card["scan_at"], last_run, ex["mode"], cleanup, now
    )
    if not ex["full"]:
        # only what is exact: free, the bar, the levels and the trend (D76)
        scan = doc = None
    else:
        known["ledgers"] = changes.load_all(state_file(changes.FOLDER))
        card["glance"]["change_text"] = changes.line(
            known["ledgers"], changes.snapshot(doc) if doc else None,
            load_state(state_file("views.json")), now,
        )  # fmt: skip
    have = review.reclaimable(scan)  # the hero's bytes, worked out once
    card["glance"]["footer_text"] = views.reclaim_footer(scan, have)
    card["glance"]["where_rows"] = views.where_rows(doc)
    # before the first scan the Glance shows no rows and no footer (D67)
    card["glance"]["scanned"] = scan is not None
    # the review condition shows in the Glance only, never on the bar (D59)
    if review.holds(scan, size, load_state(state_file("review.json")), have):
        card["glance"]["footer_colour"] = "accent"
    by_t = (load_state(state_file("expected.json")) or {}).get("by_t") or {}
    expected = by_t.get(str(cleanup["t"])) if cleanup else None
    card["reclaim"] = views.reclaim_view(
        scan, now, cleanup, views.shared_readings(doc), have, expected,
        known.get("totals_only", False),
    )  # fmt: skip
    dua = known.get("dua", shutil.which("dua") is not None)
    card["used"] = views.used_view(doc, now, colour, dua)


def evaluate(doc, samples, sample, meta, settings, now, lane, rate):
    """Advance alerts.json by one sample; the notes to send, in order."""
    level = levels.next_level(doc["level"], sample["avail"], levels.lines(settings))
    eff = levels.effective(level, meta["pressure"])
    notes = []
    kind = alerts.level_kind(doc["effective"], eff, doc["sent"], now)
    if kind and settings["notifications"]:
        cause = "metadata" if eff != level else "space"
        lead = trend.low_lead(lane) if kind == "low" else ""
        notes.append(alerts.level_note(kind, sample["avail"], cause, lead))
        doc["sent"][kind] = now
    if not sample.get("fake"):
        notes += lane_notes(doc, lane, rate, settings, now, bool(notes))
    doc.update(
        level=level, effective=eff, metadata=meta, updated=now, settings=settings
    )
    return notes


def lane_notes(doc, lane, rate, settings, now, level_sent):
    """The fast and slow lanes advance on real samples only. A level
    notification already sent this tick carries the forecast, so the slow
    lane's would say the same thing twice (D17)."""
    out = []
    fire_fast, doc["fast"] = forecast.fast_decision(rate, doc["fast"], now)
    if fire_fast and settings["notifications"]:
        out.append(trend.fast_note(rate))
        doc["sent"]["fast"] = now
    fire_slow, armed = forecast.slow_decision(lane, doc["slow"], settings["horizon"])
    doc["slow"] = {"armed": armed}
    if fire_slow and settings["notifications"] and not level_sent:
        out.append(trend.slow_note(lane))
        doc["sent"]["slow"] = now
    return out


def cmd_tick(now, settings, settings_notice, loaded=None):
    # alerts.json is read again under the lock: another writer may have run
    # since main() read it
    path = state_file("samples.jsonl")
    table = mounts.read()
    root = mounts.root_mount(table)
    readonly = health.readonly()
    if readonly:
        return readonly_tick(now, settings, settings_notice, table, readonly)
    with state_lock():
        sample = take_sample(now)
        append_sample(path, sample)
        samples = compact_samples(path, load_samples(path), now)
        meta = levels.metadata(table, root)
        doc, _ = alerts.load(state_file("alerts.json"))
        series = forecast.ordered(samples, now)
        lane = forecast.slow_lane(samples, now, settings["critical"] * GIB, series)
        rate = forecast.fast_rate(samples, now, series)
        notes = evaluate(doc, samples, sample, meta, settings, now, lane, rate)
        write_json(state_file("alerts.json"), doc)
    known = {"table": table, "root": root, "series": series, "lane": lane, "rate": rate,
             "readonly": []}  # fmt: skip
    card = build_card(samples, doc, True, settings, settings_notice, now, known)
    card["notify"] = [{"argv": alerts.argv(n, plugin_bin())} for n in notes]
    # a read-only state that cleared is ready to notify again (D102)
    health.first_notice([])
    return card


def readonly_tick(now, settings, settings_notice, table, readonly):
    """A tick on a read-only disk (D102): nothing can be written, so the
    sample is shown but not kept, the levels stay as the last tick left them,
    and the one urgent notification goes out once per episode."""
    samples = load_samples(state_file("samples.jsonl")) + [take_sample(now)]
    doc, ok = alerts.load(state_file("alerts.json"))
    known = {"table": table, "readonly": readonly}
    card = build_card(samples, doc, ok, settings, settings_notice, now, known)
    if health.first_notice(readonly):
        # through Do Not Disturb as fast-fill (D148); the panel, never the
        # agent, which could not write its report
        note = {**health.note(readonly), "app": alerts.BREAKS_QUIET,
                "click": alerts.click_panel("readonly")}  # fmt: skip
        card["notify"] = [{"argv": alerts.argv(note, plugin_bin())}]
    return card


def protected_paths():
    """protect.json's absolute paths, or none if it is missing or invalid."""
    try:
        with open(protect_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    paths = doc.get("paths") if isinstance(doc, dict) else None
    if not isinstance(paths, list):
        return []
    return [p for p in paths if isinstance(p, str) and p.startswith("/")]


def lower_priority():
    """nice 19 and the idle I/O class (§5): the scan never competes with
    the user. Failing to lower it is not worth failing the scan; the ledger
    records what it got: {nice, io "idle" · "unchanged"}."""
    with contextlib.suppress(OSError):
        os.nice(19 - os.nice(0))
    io = "unchanged"
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        p = subprocess.run(
            ["ionice", "-c3", "-p", str(os.getpid())],
            capture_output=True,
            timeout=5,
            check=False,
        )
        io = "idle" if p.returncode == 0 else io
    return {"nice": os.nice(0), "io": io}


def cmd_scan(now, settings, settings_notice, loaded=None):
    priority = lower_priority()
    table = mounts.read()
    ctx = categories.Context(os.path.expanduser("~"), table, now, root=walk_root())
    cats = categories.scan_all(ctx, protected_paths())
    doc = ledger.measure(walk_root(), table, read_statvfs, now, foreign=ctx.foreign)
    doc["priority"] = priority
    doc["fstype"] = ctx.fstype
    categories.settle(cats, ctx)
    btrfs = ctx.fstype == "btrfs"
    status = measure.status() if btrfs else "absent"
    # after one timeout, the essentials only: no folder figures (D121)
    essentials = exactness.retrying(load_state(state_file("measure.json")))
    measured = (run_helper(cats, doc, now, status, essentials) if btrfs
                else {"state": "absent"})  # fmt: skip
    if measured.get("state") == "ok":
        # D54: the snapshots row from the helper's figure
        cats = [categories.snapshots(ctx, measured) if c["id"] == "snapshots" else c
                for c in cats]  # fmt: skip
    exact = measured.get("exact") or {}
    categories.apply_exact(cats, exact)
    meta, data_used = levels.on_disk(table, ctx.root_mount)
    exact_ok = exact.get("state") == "ok" and meta is not None and data_used is not None
    if exact_ok:
        # D71: every row on disk, adding up to Data used and the disk
        ledger.on_disk(doc, exact, data_used, meta)
    measured_scan = ctx.fstype == "ext4" or exact_ok
    with state_lock():
        if btrfs:
            last = last_run(measured, now, load_state(state_file("measure.json")))
            if last["exact_state"] == "ok" and not exact_ok:
                # measured, but Usage could not go on disk (no sysfs): not a
                # measured scan, so never shown as one (D78)
                last["exact_state"] = "no-metadata"
            write_json(state_file("measure.json"), last)
        old = load_state(state_file("scan.json"))
        record_expected(old)
        # D79: a run that did not measure never replaces a measured scan
        if measured_scan or not (old and old.get("measured")):
            atomic_write(
                state_file("scan.json"),
                json.dumps(
                    {
                        "schema": 1,
                        "measured_at": now,
                        "measured": measured_scan,
                        "categories": cats,
                        "measure": measured,
                    },
                    indent=1,
                )
                + "\n",
            )
            write_json(state_file("ledger.json"), doc)
            if measured_scan:
                keep_ledger(doc, now)
            # a cleanup lowers the review baseline to the new reclaimable (D58)
            rebased = review.rebase(
                load_state(state_file("review.json")), {"categories": cats}, now
            )
            if rebased is not None:
                write_json(state_file("review.json"), rebased)
    known = {"table": table, "root": ctx.root_mount, "status": status}
    return cmd_card(now, settings, settings_notice, loaded, known)


def keep_ledger(doc, now):
    """ledgers/<time>.json: every measured scan's ledger, for the change line
    and each big change's breakdown (D115, D126); files past
    changes.KEEP_DAYS are removed."""
    folder = state_file(changes.FOLDER)
    write_json(os.path.join(folder, changes.file_name(now)), changes.snapshot(doc))
    for name in changes.expired(os.listdir(folder), now):
        os.unlink(os.path.join(folder, name))  # own-file: the engine's own daily ledger


def record_expected(old):
    """expected.json: for each cleanup logged since the scan about to be
    replaced, the plugin's own figure from that scan (D111). Kept once
    written: a later scan no longer holds the figures from before it."""
    if not old:
        return
    doc = load_state(state_file("expected.json")) or {"schema": 1, "by_t": {}}
    entries, _ = read_cleanups()
    added = False
    for e in entries:
        key = str(e["t"])
        if e["t"] <= old["measured_at"] or key in doc["by_t"]:
            continue
        doc["by_t"][key] = cleanups.expected(e, old["categories"])
        added = True
    if added:
        write_json(state_file("expected.json"), doc)


def last_run(measured, now, before=None):
    """measure.json: how the helper's last run ended (exactness, D77, D79),
    and how many runs in a row have timed out (D101)."""
    exact = measured.get("exact") or {}
    timeouts, prev = 0, (before or {}).get("timeouts")
    if measured.get("state") == "timeout":
        timeouts = (prev if units.whole(prev, 0) else 0) + 1
    return {"schema": 1, "measured_at": now, "state": measured.get("state"),
            "exact_state": exact.get("state"),
            "reason": exact.get("reason") or measured.get("reason", ""),
            "timeouts": timeouts}  # fmt: skip


def run_helper(cats, doc, now, status, essentials=False):
    """The optional root helper (D53, D69–D73): only a current copy, only
    here, daily. Its request, the paths the scan found, is written first; an
    answer is applied only to that request (its sha256). `essentials` asks
    for no folder figures (D121)."""
    if status != "current":
        return measure.run(known=status)
    data = measure.request_bytes(measure.request_doc(cats, doc, now, essentials))
    with state_lock():
        atomic_write(state_file(measure.REQUEST_NAME), data.decode())
    return measure.run(hashlib.sha256(data).hexdigest(), known=status)


def cmd_seen(now, settings, settings_notice, loaded=None):
    """Reclaim was opened in the full view, or Ask agent ran: record the
    reclaimable now as the review baseline (D58)."""
    with state_lock():
        doc = review.seen(load_state(state_file("scan.json")), now)
        if doc is not None:
            write_json(state_file("review.json"), doc)
    return cmd_card(now, settings, settings_notice, loaded)


def cmd_viewed(now, settings, settings_notice, loaded=None):
    """The full view opened: a visit, for "since you last looked" (D115)."""
    with state_lock():
        doc = changes.visit(load_state(state_file("views.json")), now)
        write_json(state_file("views.json"), doc)
    return cmd_card(now, settings, settings_notice, loaded)


def cmd_card(now, settings, settings_notice, loaded=None, known=None):
    """`loaded` is main()'s (doc, ok) of alerts.json."""
    doc, ok = loaded or alerts.load(state_file("alerts.json"))
    samples = load_samples(state_file("samples.jsonl"))
    return build_card(samples, doc, ok, settings, settings_notice, now, known)


def parser():
    p = argparse.ArgumentParser(prog="disk_space.py")
    p.add_argument(
        "verb", choices=("tick", "card", "scan", "seen", "viewed", "report", "preview")
    )
    p.add_argument("dir", nargs="?", help="preview: the fixture state folder")
    p.add_argument("--now")
    for name in ("watch", "low", "critical", "horizon"):
        p.add_argument(f"--{name}", type=int)
    p.add_argument("--notifications", type=int, choices=(0, 1))
    p.add_argument("--number-on-bar", type=int, choices=(0, 1))
    p.add_argument("--totals-only", type=int, choices=(0, 1))
    form = p.add_mutually_exclusive_group()
    form.add_argument("--md", action="store_const", const="md", dest="form")
    form.add_argument("--json", action="store_const", const="json", dest="form")
    # the Ask agent prompt's first sentence, by level (D89)
    form.add_argument("--goal", action="store_const", const="goal", dest="form")
    # a warning's Ask agent: boot · readonly (D123, D124)
    p.add_argument("--topic", choices=("boot", "readonly"), default="")
    p.add_argument("--out", help="report: write here atomically instead of stdout")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        now = parse_now(args.now)
    except ValueError:
        print(f"not a valid time: {args.now}", file=sys.stderr)
        return 2
    if args.verb == "preview":
        return cmd_preview(args)
    loaded = alerts.load(state_file("alerts.json"))
    settings, settings_notice = settings_from(args, loaded[0].get("settings"))
    if args.verb == "report":
        return run_report(
            now, settings, args.form or "md", args.out, loaded, args.topic
        )
    if args.verb in ("tick", "scan", "seen", "viewed") and refuses_real_state():
        print(
            f"refusing to {args.verb}: {state_dir()} is the real state folder and "
            "KANSO_DISK_TESTING=1 (D38)",
            file=sys.stderr,
        )
        return 3
    verb = {"tick": cmd_tick, "card": cmd_card, "scan": cmd_scan, "seen": cmd_seen,
            "viewed": cmd_viewed}[args.verb]  # fmt: skip
    try:
        card = verb(now, settings, settings_notice, loaded)
    except (OSError, ValueError) as e:
        text = str(e).splitlines()[0] if str(e) else type(e).__name__
        print(json.dumps(base_card(now, "error", text), ensure_ascii=False))
        return 1
    print(json.dumps(card, ensure_ascii=False))
    return 0


def preview_known(spec):
    """`known` for a fixture: the root filesystem, the helper's status, what
    statvfs says of / and /home and /boot, and whether dua is there."""
    fstype = spec.get("fstype", "btrfs")
    root = {
        "mount_point": "/",
        "fstype": fstype,
        "source": "/dev/preview",
        "subvol": "/@",
    }
    return {"table": [root], "root": root, "status": spec.get("status", "current"),
            "readonly": list(spec.get("readonly") or []), "boot": spec.get("boot"),
            "dua": bool(spec.get("dua", True))}  # fmt: skip


def cmd_preview(args):
    """The card of a saved fixture state (D130): the real card, painted from
    files, with nothing measured, run or written."""
    global PREVIEW_DIR
    if not args.dir or not os.path.isfile(os.path.join(args.dir, "preview.json")):
        print("preview needs a fixture folder with preview.json", file=sys.stderr)
        return 2
    PREVIEW_DIR = os.path.abspath(args.dir)
    with open(os.path.join(PREVIEW_DIR, "preview.json"), encoding="utf-8") as f:
        spec = json.load(f)
    if spec.get("tz"):
        os.environ["TZ"] = spec["tz"]
        time.tzset()
    settings, notice = settings_from(
        parser().parse_args(["card"]), spec.get("settings")
    )
    loaded = alerts.load(state_file("alerts.json"))
    card = cmd_card(int(spec["now"]), settings, notice, loaded, preview_known(spec))
    # a preview never goes stale on the wall clock (D130)
    card["glance"].update(stale_after=0, stale_steps=[])
    print(json.dumps(card, ensure_ascii=False))
    return 0


def report_doc(now, settings, loaded=None, path=None):
    samples = load_samples(state_file("samples.jsonl"))
    latest = newest(samples, now)
    doc, ok = loaded or alerts.load(state_file("alerts.json"))
    level = "unknown"
    if latest is not None:
        level = card_level(doc, ok, latest["avail"], settings)[1]
    lane = forecast.slow_lane(samples, now, settings["critical"] * GIB)
    scan = load_state(state_file("scan.json"))
    last = load_state(state_file("measure.json"))
    ex = exactness_now(scan, last)
    # the ledger's sizes are on disk only where the card shows them (D78);
    # the scan's rows say "not measured" themselves
    ledger_doc = load_state(state_file("ledger.json")) if ex["full"] else None
    # each candidate path's subvolume, and whether the root snapshots hold it (D87)
    table = mounts.read()
    layout = (table, report.snapper_subvolume(walk_root()))
    health_now = {"readonly": health.readonly(), "boot": health.boot_percent(table)}
    out = report.build(latest, level, settings, lane, scan, ledger_doc,
                       os.path.expanduser("~"), protected_paths(), now, read_cleanups(),
                       layout, (load_state(state_file("expected.json")) or {}).get("by_t"),
                       os.path.abspath(path) if path else None, health_now)  # fmt: skip
    reason = (last or {}).get("reason", "") if ex["mode"] == "refused" else ""
    out["exactness"] = {"mode": ex["mode"], "line": "" if ex["full"] else ex["text"],
                        "reason": reason}  # fmt: skip
    return out


def run_report(now, settings, form, out, loaded=None, topic=""):
    """Print the report, or write it atomically to `out`: a reader of `out`
    never sees half a report."""
    try:
        doc = report_doc(now, settings, loaded, out)
        if form == "md":
            text = report.md(doc)
        elif form == "goal":
            text = report.prompt_lines(doc, topic) + "\n"
        else:
            text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
        if out:
            atomic_write(out, text)
        else:
            sys.stdout.write(text)
    except (OSError, ValueError) as e:
        print(f"report failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
