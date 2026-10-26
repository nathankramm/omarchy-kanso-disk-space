// Pure helpers shared by the QML and Node tests. No formatting and no maths
// on sizes: the engine owns those (the Iron Rule).

// The colour for an engine colour role. Roles are the card's `colour` strings;
// "yellow" is the engine's contrast-checked theme yellow, because Commons.Color
// has no warning token. An unknown role paints muted rather than throwing.
function colourOf(role, color, palette) {
  if (!color) return ""
  if (role === "yellow")
    return palette && palette.yellow ? palette.yellow : color.urgent
  if (role === "foreground") return color.foreground
  if (role === "accent") return color.accent
  if (role === "urgent") return color.urgent
  return color.muted
}

// Where the chart's note goes (pixels, its top left), so it never overlaps
// drawn data (D131): the samples' line and the area under it fill the box
// from the first to the last point, from the highest point down to the
// baseline. The note takes the wider empty side when it fits there at its
// usual height, else the empty band above the data, else the top left.
// Geometry only: the engine owns every number on the card.
function notePlace(points, noteW, noteH, width, plotH, pad) {
  var usualY = Math.round(plotH * 0.42 - noteH / 2)
  if (!points || points.length === 0)
    return { x: Math.round((width - noteW) / 2), y: usualY }
  var minX = 1
  var maxX = 0
  var maxY = 0
  for (var i = 0; i < points.length; i++) {
    minX = Math.min(minX, points[i].x)
    maxX = Math.max(maxX, points[i].x)
    maxY = Math.max(maxY, points[i].y)
  }
  var left = minX * width - pad
  var right = width - maxX * width - pad
  var side =
    left >= right
      ? { from: 0, room: left }
      : { from: maxX * width + pad, room: right }
  if (side.room >= noteW + 2 * pad)
    return {
      x: Math.round(side.from + (side.room - noteW) / 2),
      y: usualY,
    }
  var above = plotH - maxY * plotH - pad
  if (above >= noteH + pad)
    return {
      x: Math.round((width - noteW) / 2),
      y: Math.round((above - noteH) / 2),
    }
  return { x: 0, y: 0 }
}

// The Service's clock (D150). Qt's Timer counts awake time only: it stops in
// suspend, so a 15-minute Timer fires as late after a resume as the machine
// slept. The card's times are wall-clock epochs. So the tick is due by the
// wall clock (a whole interval since the last one started, or the clock went
// back past it), checked every 30 s and when a card opens.
function tickDue(lastTick, now, interval) {
  return now < lastTick || now - lastTick >= interval
}

// Awake seconds left before the card counts as stalled: a newer sample
// (another stale_after) starts the count again from the engine's deadline;
// else `awake` seconds of awake time went by. A sleep spends none of it.
function awakeLeft(left, staleAfter, seenStaleAfter, now, awake) {
  if (staleAfter !== seenStaleAfter) return staleAfter - now
  return left - awake
}

// Stalled (D67): the plugin itself is stuck, a tick overdue while the
// machine was awake; never because it slept. No deadline (0) never stalls.
function stalled(staleAfter, left) {
  return staleAfter > 0 && left <= 0
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    colourOf: colourOf,
    notePlace: notePlace,
    tickDue: tickDue,
    awakeLeft: awakeLeft,
    stalled: stalled,
  }
}
