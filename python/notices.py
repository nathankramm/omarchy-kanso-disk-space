"""Every warning in one shared design, each with a next step (D119–D124, D131).

A warning is three lines: the fact (urgent for a read-only disk and for
Critical or worse, else the text colour; it starts with a capital or a path),
one plain sentence on what it means, and at most two quiet links ("What now
›", "Ask agent ›", "Rescan ›", "Why ›", "How to fix ›"). The full view shows
all three under each page's hero. The Glance, passive, shows the fact and
`glance`: one short sentence, and on the first warning the click hint, what
a click on the bar icon does for it (D131): Cleanup for Low, Critical and
Blocked; the warning's What now panel for a read-only disk, /boot, metadata
and the level settings (`click_panel`); the Update flow for the helper; the
full view showing the warning for the rest. "What now" opens a panel inside
the card: the state as its hero, "what now", at most three short steps (the
first with the figure), a command beside a step with Copy, and "‹ Back".

Every string fits one line at card width with its worst-case values
(tests/test_notices.py): FACT_CHARS for the caption lines, STEP_CHARS for a
step or a command. No "Error", no exclamation marks.

A link is {text, verb, argv, panel}: verb panel (opens `panel`), rescan (a
scan), run (closes the card and runs argv) or copy (runs argv, stays open).
"""

import health
import units

# characters of the caption and body fonts across the card (408 units at
# JetBrains Mono's 0.6 em advance: 68 and 56), less a margin of one caption
# character and two body ones
FACT_CHARS = 67
STEP_CHARS = 54
WHAT_NOW = "What now ›"
ASK = "Ask agent ›"
RESCAN = "Rescan ›"
WHY = "Why ›"
HOW = "How to fix ›"
BACK = "‹ Back"
COPY = "Copy"
COPIED = "Copied"
COPY_DETAILS = "Copy details ›"
CAPTION = "what now"
# the order warnings are shown in: the gravest first
ORDER = ("readonly", "metadata", "blocked", "critical", "boot", "low", "slow",
         "failed", "link", "shared", "refused", "update", "settings")  # fmt: skip
# what a click on the bar icon does for the first warning (D131)
CLEAN_UP = "Click to clean up."
WHAT_TO_DO = "Click for what to do."
TO_UPDATE = "Click to update it."
DETAILS = "Click for details."
HINTS = {"low": CLEAN_UP, "critical": CLEAN_UP, "blocked": CLEAN_UP,
         "readonly": WHAT_TO_DO, "boot": WHAT_TO_DO, "metadata": WHAT_TO_DO,
         "settings": WHAT_TO_DO, "update": TO_UPDATE}  # fmt: skip
# the warnings whose click opens their What now panel
CLICK_PANELS = ("readonly", "boot", "metadata", "settings")
LEVEL_WORDS = {
    # (fact, full-view meaning, Glance sentence)
    "low": ("Disk space is low",
            "A cleanup now keeps updates running; they stop below 10 GiB.",
            "A cleanup now keeps updates running."),
    "critical": ("Disk space is critical",
                 "Updates stop below 10 GiB free. Free some space soon.",
                 "Updates stop below 10 GiB free."),
    "blocked": ("Updates are blocked",
                "They need 10 GiB free. Space freed now unblocks them.",
                "Updates need 10 GiB free."),
}  # fmt: skip
# the README's sections the links open (bin/kanso-disk-space readme)
README = {"link": "symlinks", "shared": "other-users", "refused": "exact-sizes",
          "settings": "settings"}  # fmt: skip


def link(text, verb, argv=(), panel=""):
    return {"text": text, "verb": verb, "argv": list(argv), "panel": panel}


def warning(wid, fact, meaning, glance, links=(), urgent=False):
    return {"id": wid, "fact": fact, "meaning": meaning, "glance": glance,
            "links": list(links), "colour": "urgent" if urgent else "foreground"}  # fmt: skip


def ask(bin_path, topic=""):
    return link(ASK, "run", [bin_path, "ask", *([topic] if topic else [])])


def readme(bin_path, wid, text):
    return link(text, "run", [bin_path, "readme", README[wid]])


def what_now(panel):
    return link(WHAT_NOW, "panel", panel=panel)


def readonly_warning(paths, bin_path):
    """D124: the report needs a writable /home, so Ask agent only then."""
    links = [what_now("readonly")]
    if "/home" not in paths:
        links.append(ask(bin_path, "readonly"))
    return warning("readonly", f"{health.is_are(paths)[0].upper()}{health.is_are(paths)[1:]}",
                   "Your files are safe to read; saving fails until a restart.",
                   "Saving fails until a restart.", links, urgent=True)  # fmt: skip


def level_warning(level, bin_path):
    fact, meaning, glance = LEVEL_WORDS[level]
    links = [what_now(level), ask(bin_path)]
    return warning(level, fact, meaning, glance, links, urgent=level != "low")


def sizes_from(measured_at):
    """Line 2 of a run that did not finish: what the card still shows, in
    full and in the Glance's few words."""
    if measured_at:
        when = units.date_text(measured_at)
        return (
            f"Sizes are from {when}; free space and warnings stay exact.",
            f"Sizes are from {when}.",
        )
    return (
        "Only free space and warnings are exact until one finishes.",
        "Only free space is exact for now.",
    )


def exactness_warning(ex, measured_at, bin_path):
    """The exactness issue as a warning (D119), or None."""
    issue, text = ex.get("issue"), ex.get("text") or ""
    if issue in ("slow", "failed"):
        meaning, glance = sizes_from(measured_at)
        return warning(issue, text, meaning, glance, [link(RESCAN, "rescan")])
    if issue == "link":
        return warning("link", text,
                       "The helper never follows links; only free space is exact.",
                       "The helper never follows links.",
                       [readme(bin_path, "link", HOW)])  # fmt: skip
    if issue == "shared":
        fact, _, why = text.partition(": ")
        why = f"{why[0].upper()}{why[1:]}"
        return warning("shared", fact, f"{why}, so the helper measures nothing.",
                       f"{why}.", [readme(bin_path, "shared", WHY)])  # fmt: skip
    if issue == "refused":
        return warning("refused", text, "Only free space and warnings are exact.",
                       "Only free space is exact.",
                       [readme(bin_path, "refused", WHY)])  # fmt: skip
    if issue == "update":
        # the full view is the Update panel itself: no link
        return warning("update", "Helper update needed",
                       "Exact sizes pause until it's updated.",
                       "Exact sizes pause until it's updated.")  # fmt: skip
    return None


def build(level, metadata, readonly, boot, boot_limit, ex, measured_at, settings_notice,
          bin_path):  # fmt: skip
    """[warning], gravest first: a read-only / or /home (D102), metadata
    lifting the level, the level itself from Low, /boot from its limit
    (D103), the exactness issue, invalid settings. The first carries the
    click hint in its Glance line (D131)."""
    out = []
    if readonly:
        out.append(readonly_warning(readonly, bin_path))
    if metadata:
        out.append(warning("metadata", "Metadata is nearly full",
                           "Writes can fail while free space still shows.",
                           "Writes can fail while space shows free.",
                           [what_now("metadata"), ask(bin_path)], urgent=True))  # fmt: skip
    elif level in LEVEL_WORDS:
        out.append(level_warning(level, bin_path))
    if boot is not None and boot >= boot_limit:
        out.append(warning("boot", f"/boot is {boot}% full",
                           "New snapshots get no boot entry until old entries go.",
                           "New snapshots get no boot entry.",
                           [what_now("boot"), ask(bin_path, "boot")]))  # fmt: skip
    found = exactness_warning(ex, measured_at, bin_path)
    if found:
        out.append(found)
    if settings_notice:
        out.append(warning("settings", "Level settings are out of order",
                           "Using the defaults (50 · 30 · 15 GiB) until Watch > Low > Critical.",
                           "Using the defaults until fixed.",
                           [what_now("settings"), readme(bin_path, "settings", HOW)]))  # fmt: skip
    out.sort(key=lambda w: ORDER.index(w["id"]))
    if out:
        out[0]["glance"] += " " + HINTS.get(out[0]["id"], DETAILS)
    return out


def click_panel(warnings):
    """The panel a click on the bar icon opens (D131): the first warning's
    What now, for a read-only disk, /boot, metadata or the level settings;
    else "" (Cleanup, the Update flow or the page that shows the warning)."""
    if warnings and warnings[0]["id"] in CLICK_PANELS:
        return warnings[0]["id"]
    return ""


def step(text, command=(), placeholder=""):
    """A panel step; a command reads as typed and copies with wl-copy. A
    `placeholder` the user fills in ("<ID>") is shown muted after the
    command and never copied: the clipboard ends where they type (D131)."""
    line = " ".join(command)
    copy = []
    if command:
        copy = ["wl-copy", "--", f"{line} " if placeholder else line]
    return {"text": text, "command": line, "placeholder": placeholder,
            "copy_argv": copy}  # fmt: skip


def footer(action="", argv=()):
    """A panel's pinned footer, as a page's (D94)."""
    verb = "run" if argv else ""
    return {"left": "", "action": action, "verb": verb, "argv": list(argv)}


def panel(title, steps, foot):
    return {"title": title, "caption": CAPTION, "steps": steps, "footer": foot}


def panels(facts, bin_path, warnings=None):
    """{id: {title, caption, steps [{text, command, placeholder, copy_argv}],
    footer}}: at most three steps each, the first with the figure (D120,
    D131); with `warnings`, only the panels their links open. `facts` holds
    the figures: readonly, boot, metadata (% used or None), free (bytes),
    low, lines (the settings as given)."""
    every = all_panels(facts, bin_path)
    if warnings is None:
        return every
    wanted = {k["panel"] for w in warnings for k in w["links"] if k["verb"] == "panel"}
    return {pid: p for pid, p in every.items() if pid in wanted}


def all_panels(facts, bin_path):
    free = units.free_gib(facts.get("free") or 0)
    meta = facts.get("metadata")
    lines = facts.get("lines") or {}
    ro = health.is_are(facts.get("readonly") or ["/"])
    clear = facts.get("low", 30) + 5
    agent = footer(ASK, [bin_path, "ask"])
    return {
        "readonly": panel("Read-only", [
            step(f"{ro[0].upper()}{ro[1:]}; your files still read."),
            step("Save open work to another drive, like a USB stick."),
            step("Restart. If it comes back, the disk needs checking."),
        ], footer(COPY_DETAILS, [bin_path, "details"])),
        "boot": panel("Boot space", [
            step(f"/boot is {facts.get('boot')}% full. Nothing breaks today."),
            step("List the boot entries:", ["sudo", "limine-snapper-list"]),
            # limine-snapper-remove takes IDs ("1 3 5..7"): no picker, no
            # oldest option, so the user types the ID after the copied words
            step("Remove the oldest by its ID:", ["sudo", "limine-snapper-remove"],
                 "<ID>"),
        ], footer(ASK, [bin_path, "ask", "boot"])),
        "metadata": panel("Metadata", [
            step(f"Metadata is {meta}% used. Nothing is lost." if meta is not None
                 else "Metadata is nearly full. Nothing is lost."),
            step("See how btrfs uses its space:",
                 ["sudo", "btrfs", "filesystem", "usage", "/"]),
            step("Freeing data gives it room again: ask the agent."),
        ], agent),
        "low": panel("Low", [
            step(f"{free} free; Low clears above {clear} GiB."),
            step("Cleanup lists what frees space now, largest first."),
            step("Some space returns only after old snapshots rotate."),
        ], agent),
        "critical": panel("Critical", [
            step(f"{free} free; updates stop below 10 GiB."),
            step("Cleanup lists what frees space now, largest first."),
            step("Ask agent cleans only what you approve."),
        ], agent),
        "blocked": panel("Blocked", [
            step(f"{free} free; updates need 10 GiB."),
            step("Free space first: Cleanup's main list frees at once."),
            step("Then update:", ["omarchy", "update"]),
        ], agent),
        "settings": panel("Level settings", [
            step(f"Watch {lines.get('watch')}, Low {lines.get('low')}, Critical "
                 f"{lines.get('critical')}: out of order."),
            step("Open the bar's widget settings for this disk."),
            step("Set Watch > Low > Critical > 10 GiB."),
        ], footer(HOW, [bin_path, "readme", README["settings"]])),
    }  # fmt: skip


def strings():
    """The words every panel and warning share: Back and Copy."""
    return {"back": BACK, "copy": COPY, "copied": COPIED}
