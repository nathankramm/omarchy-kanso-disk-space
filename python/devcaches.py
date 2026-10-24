"""Developer caches outside ~/.cache (D128) and Electron apps' caches inside
~/.config (D129): scanners in categories.py's shape.

A developer cache appears only when its tool is installed, the tool itself
names the folder (npm config get cache, pnpm store path, bun pm cache, go env
GOMODCACHE, rustup toolchain list -v; cargo prints no path, so its documented
home layout, $CARGO_HOME/registry/cache), and the folder holds at least
DEV_MIN. Each uses the tool's own cleaner, checked against its documentation
or --help (npm 11.20.0 and go 1.27.3 on the test machine; pnpm, bun, rustup and cargo
from their documentation on 2026-10-23, none installed there):

    npm cache clean --force     removes <cache>/_cacache (npm's cache.js)
    pnpm store prune            only what no project uses: what it frees
                                cannot be measured from paths, so the row
                                is never a figure (D78)
    bun pm cache rm             everything inside the cache folder
    go clean -modcache          the whole module cache
    rustup toolchain uninstall  one toolchain: only one that is not the
                                default and not pinned (a directory override
                                or a rust-toolchain file under the home)
    cargo                       no stable cleaner (`cargo clean gc` needs
                                -Zgc): its registry cache goes to the Trash

An Electron app's caches are only folders named in ELECTRON_CACHES directly
in its folder (never Local Storage, IndexedDB, Service Worker or Cookies),
offered only while the app is closed (its SingletonLock names no live
process, and no process runs with that folder as --user-data-dir); a running
app is "close <app> first". They go to the Trash.

Under the test suite only KANSO_DISK_TEST_DEV_TOOLS answers ({"<argv joined
by spaces>": "<its stdout>"}; a command not in it is not installed),
KANSO_DISK_TEST_RUNNING ([app folders]) says which apps run, and
KANSO_DISK_TEST_DEV_MIN (bytes) stands in for DEV_MIN, so a test needs no
gigabyte of files.
"""

import json
import os
import re
import stat

import categories
import names as filenames
import tomllib
import units
import walk

GIB = units.GIB
DEV_MIN = GIB
TRASH = ("gio", "trash", "--")
RUSTUP_ASK = ["rustup", "toolchain", "list", "-v"]
OVERRIDES_ASK = ["rustup", "override", "list"]
# How deep under the home a rust-toolchain file is looked for, and folders
# never entered: the search must stay a small part of the daily scan
PIN_DEPTH = 4
PIN_SKIP = frozenset({"node_modules", "target", ".git", ".cache", ".local", ".cargo",
                      ".rustup", ".npm", "go"})  # fmt: skip
PIN_FILES = ("rust-toolchain.toml", "rust-toolchain")
# Chromium's cache folders, by the names Chromium gives them (D129)
ELECTRON_CACHES = ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
                   "DawnWebGPUCache", "GrShaderCache", "GraphiteDawnCache",
                   "ShaderCache")  # fmt: skip


def testing():
    return os.environ.get("KANSO_DISK_TESTING") == "1"


def tool(ctx, argv):
    """(exit code, stdout) of a read-only query; 127 without the tool. Under
    the test suite only KANSO_DISK_TEST_DEV_TOOLS answers."""
    if testing():
        said = json.loads(os.environ.get("KANSO_DISK_TEST_DEV_TOOLS") or "{}")
        key = " ".join(argv)
        return (0, said[key]) if key in said else (127, "")
    code, out, _ = ctx.run(argv)
    return code, out


def named(ctx, argv):
    """The folder a tool names, or None: the first line of its answer, an
    absolute path to a real folder (no symlink)."""
    code, out = tool(ctx, argv)
    path = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
    if not path.startswith("/") or os.path.normpath(path) != path:
        return None
    try:
        return path if stat.S_ISDIR(os.lstat(path).st_mode) else None
    except OSError:
        return None


def size_of(ctx, paths):
    return sum(walk.measure(p, ctx.foreign)[0] or 0 for p in paths)


def dev_min():
    hook = os.environ.get("KANSO_DISK_TEST_DEV_MIN") if testing() else None
    return int(hook) if hook else DEV_MIN


def dev(cid, ctx, folder, paths, real, dry, undo, note, qualifier="", items=None,
        measured=True):  # fmt: skip
    """A developer cache's row, or None under DEV_MIN (D128)."""
    total = size_of(ctx, paths)
    if total < dev_min():
        return None
    fields = {}
    if not measured:
        # what the cleaner frees is not a set of paths: never a figure (D78)
        fields = {"unmeasurable": True, "exact": False}
    return categories.result(
        cid, ctx, folder=folder, bytes=total, reclaimable=total,
        frees=ctx.frees(folder), undo=undo, qualifier=qualifier,
        items=items if items is not None else [{"path": p} for p in paths],
        measure_paths=sorted(paths) if measured else [],
        commands={"dry_run": dry, "real": real}, note=note, **fields,
    )  # fmt: skip


def npm_cache(ctx):
    cache = named(ctx, ["npm", "config", "get", "cache"])
    if cache is None:
        return None
    # `npm cache clean --force` removes the content store, <cache>/_cacache
    store = os.path.join(cache, "_cacache")
    if not os.path.isdir(store):
        return None
    return dev("npm-cache", ctx, cache, [store], [["npm", "cache", "clean", "--force"]],
               [["npm", "cache", "ls"]], "packages re-download on the next install",
               "npm's content store; its logs and npx folder stay.")  # fmt: skip


def pnpm_store(ctx):
    store = named(ctx, ["pnpm", "store", "path"])
    if store is None:
        return None
    return dev("pnpm-store", ctx, store, [store], [["pnpm", "store", "prune"]],
               [], "pruned packages re-download when a project needs them",
               "prune removes only the packages no project uses; how much that is "
               "cannot be measured from paths.", qualifier="prune keeps what projects use",
               measured=False)  # fmt: skip


def bun_cache(ctx):
    cache = named(ctx, ["bun", "pm", "cache"])
    if cache is None:
        return None
    try:
        inside = sorted(os.path.join(cache, n) for n in os.listdir(cache))
    except OSError:
        return None
    # rm empties the folder and keeps it (it may be a symlink or a mount)
    inside = [p for p in inside if not filenames.unusual(p)]
    return dev("bun-cache", ctx, cache, inside, [["bun", "pm", "cache", "rm"]],
               [["bun", "pm", "cache", "rm", "--dry-run"]],
               "packages re-download; a project with globalStore needs bun install again",
               "Everything inside Bun's cache folder, its global store included.",
               items=[{"path": cache}])  # fmt: skip


def go_modules(ctx):
    cache = named(ctx, ["go", "env", "GOMODCACHE"])
    if cache is None:
        return None
    return dev("go-modules", ctx, cache, [cache], [["go", "clean", "-modcache"]],
               [["go", "clean", "-n", "-modcache"]],
               "modules re-download on the next build",
               "The whole module download cache.")  # fmt: skip


TOOLCHAIN_LINE = re.compile(r"^(\S+?)(?: \(([^)]*)\))?(?: (/.*))?$")


def toolchains(ctx):
    """[(name, path, default)] as `rustup toolchain list -v` names them, or
    None without rustup."""
    code, out = tool(ctx, RUSTUP_ASK)
    if code != 0:
        return None
    found = []
    for line in out.splitlines():
        m = TOOLCHAIN_LINE.match(line.strip())
        if m and m.group(3):
            found.append((m.group(1), m.group(3), "default" in (m.group(2) or "")))
    return found


def pinned(ctx):
    """Every toolchain a directory override or a rust-toolchain file under
    the home pins (rustup's overrides.html: the file's [toolchain] channel,
    or a legacy file's bare name)."""
    pins = set()
    code, out = tool(ctx, OVERRIDES_ASK)
    if code == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].strip():
                pins.add(parts[1].strip())
    for path in pin_files(ctx.home):
        channel = channel_of(path)
        if channel:
            pins.add(channel)
    return pins


def pin_files(home):
    out = []
    for top, dirs, files in os.walk(home):
        depth = top[len(home) :].count(os.sep)
        dirs[:] = [] if depth >= PIN_DEPTH else sorted(
            d for d in dirs if d not in PIN_SKIP and not os.path.islink(os.path.join(top, d))
        )  # fmt: skip
        out += [os.path.join(top, f) for f in PIN_FILES if f in files]
    return out


def channel_of(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(65536)
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace")
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # the legacy rust-toolchain file: a bare name
        first = text.strip().splitlines()[0].strip() if text.strip() else ""
        return first if re.fullmatch(r"[A-Za-z0-9._-]+", first) else ""
    channel = (doc.get("toolchain") or {}).get("channel")
    return channel if isinstance(channel, str) else ""


def held(name, pins):
    """A pin names a toolchain as given or without its host triple."""
    return any(name == p or name.startswith(p + "-") for p in pins)


def rust_toolchains(ctx):
    found = toolchains(ctx)
    if not found:
        return None
    pins = pinned(ctx)
    spare = [(n, p) for n, p, default in found
             if not default and not held(n, pins) and not filenames.unusual(p)]  # fmt: skip
    if not spare:
        return None
    folder = os.path.dirname(spare[0][1])
    return dev("rust-toolchains", ctx, folder, [p for _, p in spare],
               [["rustup", "toolchain", "uninstall", n] for n, _ in spare], [RUSTUP_ASK],
               "rustup reinstalls a toolchain on demand: a large download",
               "Toolchains that are not the default and that no override or "
               "rust-toolchain file under the home pins.",
               qualifier=f"{len(spare)} unpinned", items=[
                   {"path": p, "name": n} for n, p in spare])  # fmt: skip


def cargo_registry(ctx):
    if tool(ctx, ["cargo", "--version"])[0] != 0:
        return None
    home = os.environ.get("CARGO_HOME") or os.path.join(ctx.home, ".cargo")
    cache = os.path.join(home, "registry", "cache")
    if not os.path.isdir(cache) or os.path.islink(cache):
        return None
    return dev("cargo-registry", ctx, cache, [cache], [[*TRASH, cache]],
               [["du", "-sh", "--", cache]], ".crate files re-download on the next build",
               "cargo has no stable command to clean it (cargo clean gc needs -Zgc). "
               "Moved to the Trash, it frees once the Trash is emptied.")  # fmt: skip


DEV_SCANNERS = (npm_cache, pnpm_store, bun_cache, go_modules, rust_toolchains,
                cargo_registry)  # fmt: skip


# ---- Electron apps' caches (D129) ------------------------------------------


def electron_apps(config):
    """[(app name, folder, [cache folders])]: an Electron app keeps Chromium's
    "Local State" in its folder and, unlike a browser, no Default profile."""
    out = []
    try:
        apps = sorted(os.listdir(config))
    except OSError:
        return out
    for app in apps:
        folder = os.path.join(config, app)
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        if not os.path.isfile(os.path.join(folder, "Local State")):
            continue
        if os.path.isdir(os.path.join(folder, "Default")):
            continue
        caches = [os.path.join(folder, n) for n in ELECTRON_CACHES
                  if real_dir(os.path.join(folder, n))]  # fmt: skip
        if caches:
            out.append((app, folder, caches))
    return out


def real_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def running(folder):
    """Whether the app runs: its SingletonLock ("<host>-<pid>") names a live
    process, or a process was started with it as --user-data-dir."""
    if testing():
        return folder in json.loads(os.environ.get("KANSO_DISK_TEST_RUNNING") or "[]")
    try:
        target = os.readlink(os.path.join(folder, "SingletonLock"))
    except OSError:
        target = ""
    host, _, pid = target.rpartition("-")
    here = host == os.uname().nodename
    if pid.isdigit() and here and os.path.exists(f"/proc/{pid}"):
        return True
    return user_data_dir_in_use(folder)


def user_data_dir_in_use(folder):
    flag = f"--user-data-dir={folder}".encode()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if flag in f.read().split(b"\0"):
                    return True
        except OSError:
            continue
    return False


def app_name(app):
    """An app's folder name as the card says it: "obsidian" reads "Obsidian"."""
    name = filenames.shown(app)[:20]
    return name[:1].upper() + name[1:]


def waiting_text(apps):
    """ "close Claude first", "close Claude and Obsidian first", "close 3 apps
    first": the running apps whose caches wait for them to close (D134)."""
    if not apps:
        return ""
    if len(apps) == 1:
        return f"close {app_name(apps[0])} first"
    if len(apps) == 2:
        return f"close {app_name(apps[0])} and {app_name(apps[1])} first"
    return f"close {len(apps)} apps first"


def electron_caches(ctx):
    """Every allow-listed cache folder, running apps' too: measuring is
    read-only, so the row's figure is exact for all of them; while an app
    runs the row says to close it first and the report tells the agent to
    wait (D134)."""
    config = os.path.join(ctx.home, ".config")
    busy, paths, items = [], [], []
    for app, folder, caches in electron_apps(config):
        caches = [c for c in caches if not filenames.unusual(c)]
        if not caches or size_of(ctx, caches) == 0:
            continue
        running_now = running(folder)
        if running_now:
            busy.append(app)
        paths += caches
        for c in caches:
            item = {"path": c, "real": [*TRASH, c]}
            if running_now:
                item.update(waiting=True, name=app_name(app))
            items.append(item)
    if not items:
        return None
    total = size_of(ctx, paths)
    n = len({os.path.dirname(c) for c in paths})
    qualifier = waiting_text(busy) or f"{n} app{'s' if n != 1 else ''}"
    return categories.result(
        "electron-caches", ctx, folder=config, bytes=total, reclaimable=total,
        frees=ctx.frees(config), undo="rebuilds as the app runs; it may start slower once",
        qualifier=qualifier, items=items, measure_paths=sorted(paths),
        waiting=waiting_text(busy),
        commands={"dry_run": [["du", "-sh", "--", c] for c in paths],
                  "real": [[*TRASH, c] for c in paths]},
        note=("Only Chromium's cache folders (" + ", ".join(ELECTRON_CACHES) + "), never "
              "Local Storage, IndexedDB, Service Worker or Cookies. An app's caches are "
              "moved only once it is closed. Moved to the Trash, they free once it is "
              "emptied."),
    )  # fmt: skip
