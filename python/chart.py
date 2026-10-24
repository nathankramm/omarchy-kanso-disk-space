"""The Trend view's chart, as 0..1 coordinates the QML paints as given (§8.5).

The x axis runs from 90 days ago to `horizon` days ahead, so the forecast has
room to reach its date. Free space is drawn on a scale of at least 100 GiB,
so the level lines (50 · 30 · 15 · 10) are always visible; a bigger disk's
scale steps up in round numbers. One point per 12 hours (the last sample in
each), plus the newest sample; each point carries its own hover label, so the
QML never formats a date or a size.
"""

import datetime

import forecast
import levels
import units

GIB = units.GIB
DAY = units.DAY
PAST = 90 * DAY
# The Trend view's line under the hero, naming what the chart shows.
SUB_TEXT = f"last {PAST // DAY} days · free space"
BUCKET = 12 * 3600
# While the level lines crowd at the bottom of a roomy disk's scale, only the
# Watch line is drawn (D65): Watch to Critical under this share of the height.
CROWDED = 0.06


def y_top(peak):
    """A round scale top: 100 GiB, then 50 GiB steps to 500, then 100s."""
    top = 100 * GIB
    while top < peak:
        top += 50 * GIB if top < 500 * GIB else 100 * GIB
    return top


def points(series, start):
    by_bucket = {}
    for s in series:
        if s["t"] >= start:
            by_bucket[(s["t"] - start) // BUCKET] = s
    picked = [by_bucket[k] for k in sorted(by_bucket)]
    if (
        series
        and series[-1]["t"] >= start
        and (not picked or picked[-1] is not series[-1])
    ):
        picked.append(series[-1])
    return picked


def month_starts(start, now):
    """Local first-of-month midnights in [start, now]."""
    out = []
    d = units.local(start).date().replace(day=1)
    while True:
        d = (d + datetime.timedelta(days=32)).replace(day=1)
        t = units.midnight(d)
        if t > now:
            return out
        if t >= start:
            out.append((t, units.MONTHS[d.month - 1]))


def build(samples, lane, now, settings, series=None):
    """{points, lines, band, forecast, jumps, months, note_text}."""
    series = forecast.ordered(samples, now) if series is None else series
    start, end = now - PAST, now + settings["horizon"] * DAY

    def x(t):
        return round(min(max((t - start) / (end - start), 0.0), 1.0), 4)

    shown = points(series, start)
    top = y_top(max([s["avail"] for s in shown], default=0))

    def y(size):
        return round(min(max(size, 0), top) / top, 4)

    lines = [
        {
            "y": y(settings[name] * GIB),
            "text": f"{name} {settings[name]}",
            "colour": levels.COLOURS[name],
        }
        for name in ("watch", "low", "critical")
    ]
    if lines[0]["y"] - lines[-1]["y"] < CROWDED:
        lines = lines[:1]
    out = {
        "points": [
            {
                "x": x(s["t"]),
                "y": y(s["avail"]),
                "label": f"{units.date_text(s['t'])} · {units.free_gib(s['avail'])}",
            }
            for s in shown
        ],
        "lines": lines,
        "band": {"y": y(levels.BLOCKED_ENTER), "text": "update blocked"},
        "forecast": None,
        "jumps": [
            {"x": x(t), "text": f"{units.signed_gib(d)} {units.date_text(t)}"}
            for t, d in lane.get("jumps", [])
            if t >= start
        ],
        "months": [{"x": x(t), "text": text} for t, text in month_starts(start, now)],
        "note_text": note_text(lane, len(shown)),
    }
    if lane["state"] == "falling" and shown:
        out["forecast"] = forecast_line(shown[-1], lane, settings, end, x, y)
    return out


FILLS_IN = "the trend fills in as samples arrive"


def note_text(lane, points):
    """The line centred on a chart that cannot draw a trend yet (D51): the
    level lines and whatever samples exist stay; this says why there is no
    more. The learning count is the Trend row's, so it is not repeated here.
    "" once the lane has fitted and there are two points to join."""
    if lane["state"] == "no-data":
        return f"no samples yet · {FILLS_IN}"
    if lane["state"] == "learning" or points < 2:
        return FILLS_IN
    return ""


def forecast_line(last, lane, settings, end, x, y):
    """The dashed line from the newest sample to Critical, clipped at the
    chart's right edge (then no dot and no date)."""
    critical = settings["critical"] * GIB
    cross = last["t"] + lane["days"] * DAY
    if cross <= end:
        return {
            "from": {"x": x(last["t"]), "y": y(last["avail"])},
            "to": {"x": x(cross), "y": y(critical)},
            "dot": True,
            "label": units.date_text(cross),
        }
    at_end = last["avail"] + lane["per_day"] * (end - last["t"]) / DAY
    return {
        "from": {"x": x(last["t"]), "y": y(last["avail"])},
        "to": {"x": 1.0, "y": y(at_end)},
        "dot": False,
        "label": "",
    }
