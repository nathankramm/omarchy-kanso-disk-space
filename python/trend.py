"""The forecast's words: the Glance trend line, the Trend view's rows, and the
slow- and fast-lane notifications (§8.5, §8.6)."""

import alerts
import changes
import forecast
import units

ARROW_DOWN = "▼"
ARROW_UP = "▲"
DASH = "—"


def whole_days(days):
    """Whole days, rounded down: a forecast never promises extra time."""
    return int(days) if days is not None else None


LEARNING_DAYS = forecast.SLOW_MIN_SPAN // forecast.DAY


def fit_days(lane):
    return min(14, lane.get("span_days", 0))


def days_phrase(lane, prefix=""):
    """When a forecast lands, as one honest range (D65): "in 9–14 days", "in
    11 days" when both ends agree, "within 3 days" from today, "in 9+ days"
    with no latest end."""
    soonest = whole_days(lane[f"{prefix}days_soonest"])
    latest = whole_days(lane[f"{prefix}days_latest"])
    if latest is None:
        return f"in {soonest}+ days"
    if soonest == latest:
        return units.in_days_text(soonest)
    if soonest <= 0:
        return f"within {latest} day{'s' if latest != 1 else ''}"
    return f"in {soonest}–{latest} days"


def ready_text(lane):
    """The learning lane, counted down (D65): "ready in 4 days"; the Glance
    puts "trend" before it, the Trend row's label does (D133)."""
    left = max(LEARNING_DAYS - lane["span_days"], 1)
    return f"ready {units.in_days_text(left)}"


def rate_text(lane):
    return f"{units.rate_number(lane['per_day'])} GiB a day"


def glance_line(lane):
    """{text, style dim · regular · bold}; the forecast range is inside the
    text (D65)."""
    state = lane["state"]
    if state == "no-data":
        text, style = "no samples yet", "dim"
    elif state == "learning":
        text, style = f"trend {ready_text(lane)}", "dim"
    elif state == "steady":
        text, style = f"steady · no growth in {fit_days(lane)} days", "dim"
    elif state == "freeing":
        text, style = f"{ARROW_UP} freeing {rate_text(lane)}", "regular"
    elif state == "below" and lane.get("gate_days") is not None:
        # Critical: the next line that matters is the update gate (D50)
        text = f"{ARROW_DOWN} {rate_text(lane)} · update blocked {days_phrase(lane, 'gate_')}"
        style = "bold"
    elif state == "below":
        text, style = f"{ARROW_DOWN} {rate_text(lane)} · below Critical", "bold"
    else:
        text = f"{ARROW_DOWN} {rate_text(lane)} · Critical {days_phrase(lane)}"
        style = "bold"
    return {"text": text, "style": style}


def trend_value(lane):
    state = lane["state"]
    if state == "no-data":
        return glance_line(lane)["text"], ""
    if state == "learning":
        # beside its label the count drops "trend" (D133)
        return ready_text(lane), ""
    # the value alone: a note read on as "1.1 GiB a day past 13 days" (D68)
    if state == "steady":
        return "steady", ""
    arrow = ARROW_UP if state == "freeing" else ARROW_DOWN
    return f"{arrow} {rate_text(lane)}", ""


def critical_value(lane):
    """The chart's dot already carries the date (D65); None hides the row
    until there is a forecast."""
    if lane["state"] == "falling":
        return days_phrase(lane), ""
    if lane["state"] == "below":
        return "now", ""
    return None


def last_hour_value(rate):
    if rate["state"] != "ok":
        return DASH, ""
    note = "fast" if forecast.fast_hit(rate) else ""
    return units.signed_gib(round(rate["hour_delta"])), note


JUMP_DAYS = 90  # the chart's span: Big changes looks this far back


def recent_jumps(lane, now):
    return [(t, d) for t, d in lane["jumps"] if t >= now - JUMP_DAYS * forecast.DAY][
        -2:
    ]


def jumps_value(lane, now):
    """The Big changes row when there are none; else None (one row each).
    "none in 90 days" only once the samples reach 90 days back, before
    that "none so far" (D133)."""
    if recent_jumps(lane, now):
        return None
    first = lane.get("first_t")
    if first is not None and now - first >= JUMP_DAYS * forecast.DAY:
        return f"none in {JUMP_DAYS} days", ""
    return "none so far", ""


def rows(lane, rate, now, ledgers=()):
    """The Trend view's key-value rows: [{label, value, note, note_colour}];
    a note is muted, except "fast", which is urgent (D65). Under Big changes,
    each change's date and the rows that changed across it (D115)."""
    out = []
    for label, pair in (
        ("Trend", trend_value(lane)),
        ("Critical", critical_value(lane)),
        ("Last hour", last_hour_value(rate)),
        ("Big changes", jumps_value(lane, now)),
    ):
        if pair is None:
            continue
        value, note = pair
        colour = "urgent" if note == "fast" else "muted"
        out.append(
            {"label": label, "value": value, "note": note, "note_colour": colour}
        )
    # one row per big change under one label, each with its full breakdown
    # from the daily ledgers, else the change in used space itself (D118)
    for k, (t, d) in enumerate(recent_jumps(lane, now)):
        # the headline is the change the chart marks, as used space (D126)
        note = changes.across(ledgers, t, -d) or changes.used_text(-d)
        out.append({"label": "Big changes" if k == 0 else "", "value": units.date_text(t),
                    "note": note, "note_colour": "muted"})  # fmt: skip
    return out


def low_lead(lane):
    """The Low notification's first sentence, from the slow lane (D66)."""
    if lane["state"] != "falling":
        return ""
    return f"Critical {days_phrase(lane)} at this rate."


def slow_note(lane):
    """The forecast toast quotes the range the card shows, "Critical in 9–14
    days" (D125); it still fires on the median (forecast.slow_decision)."""
    return {
        "kind": "slow",
        "urgency": "normal",
        "app": alerts.APP_NAME,
        "title": f"Critical {days_phrase(lane)}",
        "body": f"Free space is falling {rate_text(lane)}. {alerts.CLEAN_UP}",
    }


def fast_note(rate):
    """The one notification that breaks through Do Not Disturb: critical, as
    notify-send (§6 "Fast lane"; Delivery.js bypassesQuiet)."""
    hours = int(forecast.gate_in(rate) // forecast.HOUR)
    when = "within the hour" if hours < 1 else f"in about {hours} h"
    return {
        "kind": "fast",
        "urgency": "critical",
        "app": "notify-send",
        # one toast line (tests/test_alerts.py TITLE_BUDGET, D66)
        "title": f"Disk filling fast · {units.signed_gib(round(rate['hour_delta']))} in 1 h",
        "body": f"Updates will be blocked {when} at this rate.",
    }
