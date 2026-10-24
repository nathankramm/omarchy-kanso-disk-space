"""The Low level's yellow, from the active theme (§4.7, §8.3).

Commons.Color has foreground, background, accent, urgent and muted, but no
warning colour, so the engine reads `yellow` from the theme's colors.toml and
lightens or darkens it to 3:1 on the popup surface, the way Kanso Screen
Time's engine does for its rows (its D30; this is a port of that code). The
QML paints every other level colour from Commons.Color directly. Without a
usable yellow: Okabe-Ito's yellow, adapted the same way.
"""

import os
import re

import tomllib

# Commons/Color.qml's own defaults, used when no theme loads
SHELL_BACKGROUND = "#101315"
SHELL_FOREGROUND = "#cacccc"
OKABE_ITO_YELLOW = "#f0e442"
SWATCH_MIN_RATIO = 3.0  # WCAG 1.4.11
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def theme_dir():
    # Commons/Color.qml reads $HOME/.local/state/omarchy/current/theme,
    # not XDG_STATE_HOME, so neither does this.
    return os.path.join(
        os.path.expanduser("~"), ".local", "state", "omarchy", "current", "theme"
    )


def user_shell_toml():
    return os.path.join(os.path.expanduser("~"), ".config", "omarchy", "shell.toml")


def rgb8(h):
    return tuple(int(h[i : i + 2], 16) for i in (1, 3, 5))


def hex8(rgb):
    return "#" + "".join(f"{c:02x}" for c in rgb)


def to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(h):
    r, g, b = (to_linear(c / 255) for c in rgb8(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = rel_luminance(a), rel_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix_step(toward, base, step):
    """step % of toward over base, per channel in integers, halves up."""
    return hex8(
        (t * step + b * (100 - step) + 50) // 100
        for t, b in zip(rgb8(toward), rgb8(base), strict=True)
    )


def to_three_to_one(h, bg):
    """The colour, lightened or darkened (toward whichever of black and white
    contrasts more with bg) by the least whole step that reaches 3:1."""
    toward = (
        "#000000"
        if contrast_ratio("#000000", bg) >= contrast_ratio("#ffffff", bg)
        else "#ffffff"
    )
    for step in range(101):
        c = mix_step(toward, h, step)
        if contrast_ratio(c, bg) >= SWATCH_MIN_RATIO:
            return c
    return toward


def load_toml(path):
    """The document, or None if missing or invalid; read-only."""
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):  # tomllib.TOMLDecodeError is a ValueError
        return None
    return doc if isinstance(doc, dict) else None


def hex_colours(doc):
    return {
        k: v.lower()
        for k, v in (doc or {}).items()
        if isinstance(v, str) and HEX_RE.fullmatch(v)
    }


def popup_background(colors):
    """The popup surface, the way Commons/Color.qml resolves popups.background:
    the user's shell.toml over the theme's, a role word taken from the theme,
    else colors.toml's background."""
    bg = colors.get("background", SHELL_BACKGROUND)
    fg = colors.get("foreground", SHELL_FOREGROUND)
    roles = {
        "background": bg,
        "foreground": fg,
        "text": fg,
        "accent": colors.get("accent", fg),
        "muted": colors.get("muted", fg),
        "urgent": colors.get("red", fg),
    }
    chosen = bg
    for path in (os.path.join(theme_dir(), "shell.toml"), user_shell_toml()):
        doc = load_toml(path)
        popups = doc.get("popups") if doc else None
        value = popups.get("background") if isinstance(popups, dict) else None
        if isinstance(value, str):
            chosen = value
    v = chosen.strip().lower()
    if v in roles:
        return roles[v]
    m = HEX_RE.match(v)
    return m.group(0) if m else bg


def level_palette():
    """{yellow}: the Low colour at 3:1 on the popup; the theme's yellow, else
    Okabe–Ito's."""
    colors = hex_colours(load_toml(os.path.join(theme_dir(), "colors.toml")))
    yellow = colors.get("yellow", OKABE_ITO_YELLOW)
    return {"yellow": to_three_to_one(yellow, popup_background(colors))}
