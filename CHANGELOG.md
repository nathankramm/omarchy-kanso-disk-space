# Changelog

## 1.0.0

First release.

- Free space on `/` in the bar, measured the way `omarchy update` gates on
  it, with Watch, Low and Critical levels you can set and Omarchy's own
  10 GiB update line.
- Hover for a glance; click for Usage, Cleanup and Trend. Usage adds up to
  the disk; Cleanup says what frees space now and what only after updates;
  Trend forecasts Critical as a range.
- Warnings with What now panels, notifications when things get worse, and a
  line for what changed since you last looked.
- Exact sizes on btrfs through an optional read-only helper (one click to set
  up, update or remove), and on ext4 without one. The card says when a
  measurement is running, the first one after setup included.
- Ask agent: an exact report and a cleanup skill for your coding agent, with
  a receipt of what it freed. Totals only for cloud agents.
- Developer caches outside `~/.cache`, and Electron apps' caches in
  `~/.config`, counted while the app is open and cleaned once it is closed.
- Clear guidance for a read-only disk and a nearly full `/boot`.
