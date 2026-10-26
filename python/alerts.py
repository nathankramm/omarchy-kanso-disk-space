"""alerts.json and the notification decisions (§6, §8.6).

alerts.json remembers the level and when each notification was last sent, so
a shell restart, which reruns Service and its first tick, never sends a
notification twice. A notification goes out only on a move into a WORSE
level; improving is silent. Critical repeats at most once a day (D13).
"""

import json

import levels
import units

SCHEMA = 1
CRITICAL_REPEAT = 86400
APP_NAME = "Kanso Disk Space"
GLYPH = "\uf0a0"  # a plain drive outline,  (D41: picked over nf-md-harddisk in P7)


def empty():
    return {
        "schema": SCHEMA,
        "level": "fine",
        "effective": "fine",
        "sent": {},
        "slow": {"armed": True},
        "fast": {"last_check": None, "last_fire": 0},
        "metadata": None,
        "updated": 0,
        "settings": None,
    }


def load(path):
    """(doc, ok): ok is False for a missing or corrupt file, which starts over
    from Fine, as on a first run: at worst one notification repeats."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return empty(), False
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return empty(), False
    base = empty()
    base.update({k: v for k, v in doc.items() if k in base})
    if base["level"] not in levels.LEVELS or base["effective"] not in levels.LEVELS:
        return empty(), False
    if not isinstance(base["sent"], dict):
        base["sent"] = {}
    if not isinstance(base["slow"], dict):
        base["slow"] = {"armed": True}
    if not isinstance(base["fast"], dict) or not valid_check(
        base["fast"].get("last_check")
    ):
        base["fast"] = {"last_check": None, "last_fire": 0}
    return base, True


def valid_check(check):
    if check is None:
        return True
    return (
        isinstance(check, dict) and isinstance(check.get("t"), int) and "hit" in check
    )


def level_kind(prev, new, sent, now):
    """The level notification to send on a move from `prev` to `new`, or None."""
    if levels.rank(new) <= levels.rank(prev) or new in ("fine", "watch"):
        return None
    if (
        new == "critical"
        and now - sent.get("critical", -CRITICAL_REPEAT) < CRITICAL_REPEAT
    ):
        return None
    return new


# One verb on every toast that opens Cleanup (D66, D146).
CLEAN_UP = "Click to clean up."
# A toast's click opens the card, never the agent (D145): bin's `open`, the
# bar widget's IPC. Cleanup, as the bar's click at Low and worse (D117), or a
# warning's What now panel, as the bar's click opens it (D131).
CLICK_CLEANUP = ["open", "reclaim"]


def click_panel(panel_id):
    return ["open", "panel", panel_id]


# The app name Do Not Disturb lets through at critical urgency (D9, D148;
# Delivery.js bypassesQuiet): fast-fill and read-only only.
BREAKS_QUIET = "notify-send"


# (urgency, title, body) per note; "metadata" is Critical lifted by btrfs
# metadata (D47). D66: space freed under / (subvolume @) comes back only as
# Omarchy's root snapshots rotate.
NOTES = {
    "low": ("normal", "Disk space low · {free} free", "{lead}" + CLEAN_UP),
    "metadata": ("normal", "Metadata nearly full · {free} free",
                 "Writes can fail while space still shows free. Click to check."),
    "critical": ("normal", "Disk space critical · {free} free",
                 f"Updates need 10 GiB free. {CLEAN_UP}"),
    "blocked": ("critical", "Updates blocked · {free} free",
                f"Updates need 10 GiB free. {CLEAN_UP}"),
}  # fmt: skip


def level_note(kind, avail, cause, forecast_text=""):
    """{kind, urgency, app, title, body} for low, critical and blocked. No
    path from the home folder ever appears: notification history is not
    private (§8.6)."""
    key = "metadata" if kind == "critical" and cause == "metadata" else kind
    if key not in NOTES:
        key = kind = "blocked"
    urgency, title, body = NOTES[key]
    lead = f"{forecast_text} " if forecast_text and kind == "low" else ""
    return {
        "kind": kind,
        "urgency": urgency,
        "app": APP_NAME,
        "title": title.format(free=units.free_gib(avail)),
        "body": body.format(lead=lead),
        # metadata's "Click to check." opens its What now panel, as the bar does
        "click": click_panel("metadata") if key == "metadata" else CLICK_CLEANUP,
    }


def argv(note, plugin_bin):
    """The exact omarchy-notification-send command. --app-name and -u are
    always given: the script's default app name, omarchy-action, would break
    through Do Not Disturb (D9). --exec must come last: the note's click, bin's
    `open` (D145); a note without one, or no plugin_bin, has no click."""
    cmd = [
        "omarchy-notification-send",
        "--app-name",
        note["app"],
        "-u",
        note["urgency"],
        "-g",
        GLYPH,
        note["title"],
        note["body"],
    ]
    click = note.get("click")
    return cmd + ["--exec", plugin_bin, *click] if plugin_bin and click else cmd
