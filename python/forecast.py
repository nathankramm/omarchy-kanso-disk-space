"""The two forecast lanes (§6 "Slow lane", "Fast lane").

Both read only real samples (never KANSO_DISK_FAKE_AVAIL's), in time order: a
sample whose clock went backwards is dropped, as is one from more than an
hour in the future.

Slow lane: the median of pairwise slopes (Theil–Sen) over the samples since
the last jump (a step of more than 5 GiB between consecutive samples) and
within 14 days. It needs 5 days and 96 samples. With more than 63 samples it
fits 63 evenly spaced ones (index round-half-up(k * (n - 1) / 62), k = 0..62,
so 1,953 pairs, under the blueprint's 2,000; D19). The range is the 10th and
90th percentile slopes (linear interpolation, position p * (m - 1)). The
target is Critical, never zero.

Fast lane: the rate over the last hour of contiguous samples (no gap over 30
minutes, one boot). A check runs every 30 minutes; two checks in a row that
each see the 10 GiB gate within 6 hours fire it, at most once in 6 hours.
"""

import itertools

import levels
import units

GIB = units.GIB
DAY = units.DAY
HOUR = 3600
JUMP = 5 * GIB
SLOW_WINDOW = 14 * DAY
SLOW_MIN_SPAN = 5 * DAY
SLOW_MIN_SAMPLES = 96
FIT_POINTS = 63
REARM_DAYS = 21
GAP = 30 * 60
FAST_WINDOW = HOUR
FAST_SLACK = 5 * 60  # a tick lands a little after its 15-minute mark (D18)
FAST_MIN_SPAN = 30 * 60
# a check per 30 minutes, with 2 minutes of tick slack (D18)
CHECK_EVERY = 30 * 60 - 2 * 60
CHECK_PAIR = HOUR  # two checks are "in a row" if the earlier is within the hour
FAST_HORIZON = 6 * HOUR
FAST_COOLDOWN = 6 * HOUR


def ordered(samples, now):
    """Real samples in strictly increasing time; clock jumps back dropped."""
    out = []
    for s in samples:
        if s.get("fake") or s["t"] > now + units.FUTURE_SLACK:
            continue
        if out and s["t"] <= out[-1]["t"]:
            continue
        out.append(s)
    return out


def jumps(series):
    """[(t, delta)] for each step of more than 5 GiB between neighbours."""
    return [
        (b["t"], b["avail"] - a["avail"])
        for a, b in itertools.pairwise(series)
        if abs(b["avail"] - a["avail"]) > JUMP
    ]


def since_last_jump(series):
    start = 0
    for i in range(1, len(series)):
        if abs(series[i]["avail"] - series[i - 1]["avail"]) > JUMP:
            start = i
    return series[start:]


def fit_points(series):
    n = len(series)
    if n <= FIT_POINTS:
        return series
    last = FIT_POINTS - 1
    return [series[(2 * k * (n - 1) + last) // (2 * last)] for k in range(FIT_POINTS)]


def median(values):
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def percentile(values, p):
    s = sorted(values)
    pos = p * (len(s) - 1)
    lo = int(pos)
    if lo + 1 >= len(s):
        return s[-1]
    return s[lo] + (s[lo + 1] - s[lo]) * (pos - lo)


def slopes(points):
    return [
        (b["avail"] - a["avail"]) / (b["t"] - a["t"])
        for i, a in enumerate(points)
        for b in points[i + 1 :]
    ]


def days_until(avail, line, per_day):
    """Days until `avail` falls to `line` at `per_day` bytes a day (< 0)."""
    if per_day >= 0:
        return None
    return (avail - line) / -per_day


def slow_lane(samples, now, critical_line, series=None):
    """{state, ...}: state no-data · learning · steady · falling · freeing ·
    below (already under Critical). Rates are bytes a day. Below Critical but
    still above the update gate, the lane also forecasts the gate (D50):
    gate_days, gate_days_soonest, gate_days_latest from the same fit."""
    series = ordered(samples, now) if series is None else series
    if not series:
        return {"state": "no-data", "jumps": []}
    window = [s for s in since_last_jump(series) if s["t"] >= now - SLOW_WINDOW]
    # the oldest sample: Big changes says "none in 90 days" only once the
    # samples reach that far back (D133)
    out = {"jumps": jumps(series), "first_t": series[0]["t"]}
    span = window[-1]["t"] - window[0]["t"] if window else 0
    out["span_days"] = span // DAY
    out["samples"] = len(window)
    if span < SLOW_MIN_SPAN or len(window) < SLOW_MIN_SAMPLES:
        return {**out, "state": "learning"}
    all_slopes = slopes(fit_points(window))
    per_day = median(all_slopes) * DAY
    low, high = percentile(all_slopes, 0.10) * DAY, percentile(all_slopes, 0.90) * DAY
    out.update(per_day=per_day, per_day_low=low, per_day_high=high)
    avail = series[-1]["avail"]
    if per_day < 0 and avail <= critical_line:
        if avail > levels.BLOCKED_ENTER:
            out.update(
                gate_days=days_until(avail, levels.BLOCKED_ENTER, per_day),
                gate_days_soonest=days_until(avail, levels.BLOCKED_ENTER, low),
                gate_days_latest=days_until(avail, levels.BLOCKED_ENTER, high),
            )
        return {**out, "state": "below"}
    if units.rate_tenths(per_day) == 0:
        return {**out, "state": "steady"}
    if per_day > 0:
        return {**out, "state": "freeing"}
    return {
        **out,
        "state": "falling",
        "days": days_until(avail, critical_line, per_day),
        "days_soonest": days_until(avail, critical_line, low),
        "days_latest": days_until(avail, critical_line, high),
    }


def slow_decision(lane, state, horizon_days):
    """(fire, armed): fire once when Critical is under the horizon; re-arm
    only once the projection passes 21 days or stops falling."""
    armed = state.get("armed", True)
    if lane["state"] in ("no-data", "learning", "below"):
        return False, armed
    if lane["state"] != "falling" or lane["days"] > REARM_DAYS:
        return False, True
    if armed and lane["days"] < horizon_days:
        return True, False
    return False, armed


def contiguous_tail(series):
    """The newest run with no gap over 30 minutes and one boot id."""
    if not series:
        return []
    start = len(series) - 1
    while start > 0:
        a, b = series[start - 1], series[start]
        if b["t"] - a["t"] > GAP or a.get("boot_id") != b.get("boot_id"):
            break
        start -= 1
    return series[start:]


def fast_rate(samples, now, series=None):
    """{state ok · insufficient, rate (bytes/s), hour_delta, avail, t, boot_id}."""
    run = contiguous_tail(ordered(samples, now) if series is None else series)
    if not run:
        return {"state": "insufficient"}
    last = run[-1]
    window = [s for s in run if s["t"] >= last["t"] - FAST_WINDOW - FAST_SLACK]
    first = window[0]
    base = {"avail": last["avail"], "t": last["t"], "boot_id": last.get("boot_id", "")}
    if last["t"] - first["t"] < FAST_MIN_SPAN:
        return {**base, "state": "insufficient"}
    rate = (last["avail"] - first["avail"]) / (last["t"] - first["t"])
    return {**base, "state": "ok", "rate": rate, "hour_delta": rate * HOUR}


def gate_in(rate):
    """Seconds until the 10 GiB gate at `rate`, or None when not falling."""
    if rate["state"] != "ok" or rate["rate"] >= 0:
        return None
    return (rate["avail"] - levels.BLOCKED_ENTER) / -rate["rate"]


def fast_hit(rate):
    seconds = gate_in(rate)
    return (
        seconds is not None
        and rate["avail"] > levels.BLOCKED_ENTER
        and seconds <= FAST_HORIZON
    )


def fast_decision(rate, state, now):
    """(fire, new state). state: {last_check: {t, hit, boot_id} | None, last_fire}."""
    last_check = state.get("last_check")
    last_fire = state.get("last_fire", 0)
    if rate["state"] != "ok":
        return False, {"last_check": last_check, "last_fire": last_fire}
    if last_check and rate["t"] - last_check["t"] < CHECK_EVERY:
        return False, {"last_check": last_check, "last_fire": last_fire}
    hit = fast_hit(rate)
    check = {"t": rate["t"], "hit": hit, "boot_id": rate["boot_id"]}
    in_a_row = (
        last_check is not None
        and last_check["hit"]
        and last_check.get("boot_id") == rate["boot_id"]
        and rate["t"] - last_check["t"] <= CHECK_PAIR
    )
    fire = hit and in_a_row and now - last_fire >= FAST_COOLDOWN
    return fire, {"last_check": check, "last_fire": now if fire else last_fire}
