"""The XDG user directories from ~/.config/user-dirs.dirs (D32).

An entry that resolves to the home folder itself (`"$HOME/"`, the spelling
xdg-user-dirs writes for a folder the user turned off) names no folder, so it
is skipped: never protected as "the whole home", never walked as Downloads.
"""

import os
import re

DEFAULTS = {
    "DESKTOP": "Desktop",
    "DOCUMENTS": "Documents",
    "DOWNLOAD": "Downloads",
    "MUSIC": "Music",
    "PICTURES": "Pictures",
    "PUBLICSHARE": "Public",
    "TEMPLATES": "Templates",
    "VIDEOS": "Videos",
}
LINE = re.compile(r'\s*XDG_([A-Z]+)_DIR="(.*)"\s*$')


def configured(home):
    """{name: absolute path or None (set to the home folder)} for every entry
    in user-dirs.dirs; {} without the file."""
    home = os.path.normpath(home)
    found = {}
    try:
        with open(
            os.path.join(home, ".config", "user-dirs.dirs"), encoding="utf-8"
        ) as f:
            for line in f:
                m = LINE.match(line)
                if m:
                    path = os.path.normpath(m.group(2).replace("$HOME", home))
                    found[m.group(1)] = None if path == home else path
    except OSError:
        pass
    return found


def resolve(home, name):
    """One user directory: its configured path, its default when it has no
    entry, or None when its entry is the home folder."""
    found = configured(home)
    if name in found:
        return found[name]
    return os.path.join(home, DEFAULTS[name])


def all_dirs(home):
    """Every user directory that names a folder, absolute, in a fixed order,
    each once."""
    found = configured(home)
    out = []
    for name, default in DEFAULTS.items():
        path = found[name] if name in found else os.path.join(home, default)
        if path is not None and path not in out:
            out.append(path)
    return out
