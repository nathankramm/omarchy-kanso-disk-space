"""The unprivileged category scanners (§7), one function per category.

Each returns result()'s fields, among them:
    state   ok · needs sudo · absent (not installed) · not running · error
    bytes   what it holds; None whenever it could not be measured, never 0
    frees   now · after updates (§6: off `/`'s mount it frees now; on `@` only
            once the snapshots holding it rotate) · n/a
    items   [{path, bytes, ...}] for the report, absolute paths; an offered
            folder measured alone also has frees_bytes (D99)
    exact   reclaimable is what cleaning frees: a measured nothing here;
            otherwise only once settle() (ext4, D76) or apply_exact() (the
            helper on btrfs, D69) measured it. Never an estimate (D78)
No scanner deletes, moves or changes anything. Commands are only ever
read-only (`journalctl --disk-usage`, `docker system df`, `ollama list`), and
Docker is asked only when its socket is readable AND the daemon already runs,
so a scan never socket-activates dockerd.
"""

import json
import os
import re
import shutil
import stat
import subprocess

import devcaches
import journal as journals
import mounts
import names as filenames
import units
import userdirs
import walk

GIB = units.GIB
MIB = 1024**2
DAY = units.DAY
BIG_DOWNLOAD = GIB
OLD_DOWNLOAD = 90 * DAY
UNUSED_MODEL = 60 * DAY
JOURNAL_KEEP = 500 * MIB  # D23: the report suggests vacuuming down to this
TIMEOUT = 10
# the App caches folders the report gives commands for, each measured alone (D99)
EACH = 5


class Context:
    """Where to look and how to run things; tests swap in a fake tree."""

    def __init__(self, home, mount_table, now, root="/", run=None):
        self.home = home
        self.mounts = mount_table
        self.now = now
        self.root = root
        self.foreign = walk.foreign_mount_points(mount_table)
        self.run = run or run_command
        self.root_mount = mounts.root_mount(mount_table)
        # the filesystem decides what can be exact (D76)
        self.fstype = self.root_mount["fstype"] if self.root_mount else ""

    def path(self, absolute):
        """A system path, under the test root when there is one."""
        return (
            os.path.join(self.root, absolute.lstrip("/"))
            if self.root != "/"
            else absolute
        )

    def frees(self, path):
        return "now" if mounts.frees_now(path, self.mounts) else "after updates"

    def frees_system(self, absolute):
        """frees() for a system path such as /var/log/journal."""
        return self.frees(self.path(absolute))


def run_command(argv):
    """(exit code, stdout, stderr); (127, "", "not found") without the tool."""
    if shutil.which(argv[0]) is None:
        return 127, "", "not found"
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return p.returncode, p.stdout, p.stderr


def result(cid, ctx, **fields):
    out = {
        "id": cid,
        # its own label, even for a scanner that failed (D62)
        "label": dict(LABELS)[cid],
        "state": "ok",
        "bytes": None,
        "reclaimable": None,
        "frees": "n/a",
        "undo": "",
        "qualifier": "",
        "items": [],
        "commands": {"dry_run": [], "real": []},
        "note": "",
        "unreadable": [],
        "measured_at": ctx.now,
        # the paths the measure helper measures for this row (D72); [] when
        # it measures none
        "measure_paths": [],
        # the offered folders it also measures one at a time (D99)
        "each": [],
        # its standard folder: the one place a totals-only report names
        # (D127); "" when it has none
        "folder": "",
    }
    out.update(fields)
    if "exact" not in fields:
        # only a measured nothing is exact before settle() or apply_exact()
        out["exact"] = out["reclaimable"] == 0
    return out


def needs_sudo(cid, ctx, **fields):
    return result(cid, ctx, state="needs sudo", **fields)


AUR_CACHES = ("yay", "paru")


# A cache with its own cleaner (D107): the command that asks the tool where
# its cache is, and the one that empties it, at once rather than through the
# Trash. Each was checked read-only (uv 0.13.5, mise 2026.10.17, go 1.27.3;
# pip's `cache dir` and `cache purge` from its documentation, pip is not on
# the test machine). A folder gets the cleaner only when the tool names it as its cache.
CACHE_TOOLS = {
    "uv": (["uv", "cache", "dir"], ["uv", "cache", "clean"]),
    "mise": (["mise", "-C", "/", "cache", "path"], ["mise", "cache", "clear"]),
    "pip": (["pip", "cache", "dir"], ["pip", "cache", "purge"]),
    "go-build": (["go", "env", "GOCACHE"], ["go", "clean", "-cache"]),
}
# Folders whose undo costs more than "regenerates as the apps run" (D108)
COSTLY_UNDO = {
    "huggingface": "models re-download on next use",
    "torch": "models re-download on next use",
    "ms-playwright": "its browsers re-download on next use",
    "restic": (
        "restic re-fetches its index from the backup repository: the next "
        "backup is slower and uses the network"
    ),
    "spotify": "offline downloads download again, and Spotify may ask you to log in",
    "rbw": "rbw must log in and sync again before it can show a password",
    "debuginfod_client": "debug symbols re-download the next time a crash is diagnosed",
}
# `--` ends the options wherever a tool takes a path (D114)
TRASH_PATTERN = ["gio", "trash", "--", "<its absolute path>"]


def cache_tool(ctx, path):
    """The tool's own cleaner for `path`, when the tool says `path` is its
    cache; else None. Under the test suite only KANSO_DISK_TEST_CACHE_TOOLS
    ({name: folder}) answers, never the real tools."""
    name = os.path.basename(path)
    if name not in CACHE_TOOLS:
        return None
    ask, clean = CACHE_TOOLS[name]
    if os.environ.get("KANSO_DISK_TESTING") == "1":
        said = json.loads(os.environ.get("KANSO_DISK_TEST_CACHE_TOOLS") or "{}").get(
            name
        )
    else:
        code, out, _ = ctx.run(ask)
        said = out.strip() if code == 0 else None
    if not said or os.path.realpath(said) != os.path.realpath(path):
        return None
    return clean


def app_caches(ctx):
    top = os.path.join(ctx.home, ".cache")
    items, unreadable, total = [], [], 0
    try:
        names = sorted(os.listdir(top))
    except FileNotFoundError:
        names = []
    except OSError:
        return needs_sudo("app-caches", ctx, frees=ctx.frees(top))
    for name in names:
        if name in AUR_CACHES:
            continue
        path = os.path.join(top, name)
        size, bad, _ = walk.measure(path, ctx.foreign)
        unreadable += bad
        if size:
            items.append(cache_item(ctx, path, size))
    items.sort(key=lambda i: (-i["bytes"], i["path"]))
    # an unusual name is listed, never offered or counted (D114)
    usual = [i for i in items if not i.get("unusual")]
    total = sum(i["bytes"] for i in usual)
    offered = [i["path"] for i in usual[:EACH]]
    tools = [i["real"] for i in usual if i["real"][0] != "gio"]
    return result(
        "app-caches",
        ctx,
        folder=top,
        bytes=total,
        reclaimable=total,
        frees=ctx.frees(top),
        undo="regenerates as the apps run",
        items=items,
        qualifier=f"{len(items)} apps" if items else "",
        each=offered,
        commands={
            "dry_run": [["du", "-sh", "--", p] for p in offered],
            # every listed folder has its own command (D107): a tool's
            # cleaner, else the Trash
            "real": tools + [i["real"] for i in usual[:EACH] if i["real"][0] == "gio"],
            "pattern": TRASH_PATTERN if usual else [],
        },
        note=(
            "Per folder, with its app closed; never the whole of ~/.cache. A folder "
            "with its own cleaner is emptied by it and frees the space at once; any "
            "other goes to the Trash and frees the space once the Trash is emptied."
        ),
        unreadable=unreadable,
    )


def cache_item(ctx, path, size):
    """{path, bytes, real, undo?}: the folder's own command and, where
    regenerating it costs more than time, what undoing it means; an unusual
    name has no command (D114)."""
    if filenames.unusual(path):
        return {"path": path, "bytes": size, "unusual": True}
    item = {"path": path, "bytes": size,
            "real": cache_tool(ctx, path) or ["gio", "trash", "--", path]}  # fmt: skip
    undo = COSTLY_UNDO.get(os.path.basename(path))
    if undo:
        item["undo"] = undo
    return item


def aur_cache(ctx):
    """yay's build folders, never its top-level files (completion.cache,
    vcs.json), so no `yay -Y --gendb` is needed afterwards (D109); paru's
    folder whole. Every one to the Trash: no yay or paru command frees
    exactly these (D88)."""
    items, total, paths = [], 0, []
    for name in AUR_CACHES:
        path = os.path.join(ctx.home, ".cache", name)
        gone = yay_builds(path) if name == "yay" else [path]
        # a build folder with an unusual name is never offered (D114)
        gone = [p for p in gone if not filenames.unusual(p)]
        size = sum(walk.measure(p, ctx.foreign)[0] or 0 for p in gone)
        if size:
            items.append({"path": path, "bytes": size, "tool": name})
            paths += gone
            total += size
    return result(
        "aur-cache",
        ctx,
        folder=os.path.join(ctx.home, ".cache"),
        bytes=total,
        reclaimable=total,
        frees=ctx.frees(os.path.join(ctx.home, ".cache")),
        undo="rebuilds on the next install",
        items=items,
        measure_paths=paths,
        commands={
            "dry_run": [["du", "-sh", "--", i["path"]] for i in items],
            "real": [["gio", "trash", "--", p] for p in paths],
        },
        note=aur_note(items),
    )


def yay_builds(path):
    """yay's build folders: the real folders directly under its cache."""
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    out = []
    for name in names:
        p = os.path.join(path, name)
        try:
            if stat.S_ISDIR(os.lstat(p).st_mode):
                out.append(p)
        except OSError:
            continue
    return out


def aur_note(items):
    if not items:
        return ""
    note = (
        "With the AUR helper not running. Moved to the Trash, it frees the space "
        "once the Trash is emptied."
    )
    if any(i["tool"] == "yay" for i in items):
        note += (
            " yay: only its build folders; vcs.json and completion.cache stay, "
            "so nothing needs rebuilding."
        )
    return note


MISE_INSTALLS = ".local/share/mise/installs"


def mise_prunable(ctx):
    """(exit code, `mise ls --prunable --json`'s document or None). Run from
    / so no project config in the scan's folder is loaded (and tracked).
    Under the test suite only KANSO_DISK_TEST_MISE answers ("absent", or the
    document), never the real mise."""
    if os.environ.get("KANSO_DISK_TESTING") == "1":
        hook = os.environ.get("KANSO_DISK_TEST_MISE") or "absent"
        return (127, None) if hook == "absent" else (0, json.loads(hook))
    code, out, _ = ctx.run(["mise", "-C", "/", "ls", "--prunable", "--json"])
    try:
        return code, json.loads(out) if code == 0 else None
    except ValueError:
        return code, None


def mise_versions(ctx):
    """D100: exactly the versions `mise prune --tools` deletes: `mise ls
    --prunable` lists them, sparing a version a running program was started
    from as prune does (checked on mise 2026.10.17). Their install folders,
    each a real folder the user owns under ~/.local/share/mise/installs; one
    anywhere else is not measured. No row (None) when mise is absent or
    nothing is prunable. prune also clears each version's download record in
    ~/.cache/mise, which App caches counts."""
    code, doc = mise_prunable(ctx)
    if code == 127:
        return None
    if not isinstance(doc, dict):
        return result(
            "mise-versions", ctx, state="error", note="mise ls --prunable failed"
        )
    top = os.path.join(ctx.home, MISE_INSTALLS)
    items, outside = [], []
    for tool, versions in sorted(doc.items()):
        for v in versions if isinstance(versions, list) else []:
            if not isinstance(v, dict) or v.get("installed") is not True:
                continue
            path = v.get("install_path")
            if not mise_folder(path, top):
                outside.append(str(path))
                continue
            size, _, _ = walk.measure(path, ctx.foreign)
            items.append({"path": path, "bytes": size or 0,
                          "name": f"{tool}@{v.get('version')}"})  # fmt: skip
    if outside:
        return result("mise-versions", ctx, state="error",
                      note=f"mise lists versions outside {top}: {outside[0]}")  # fmt: skip
    if not items:
        return None
    total = sum(i["bytes"] for i in items)
    n = len(items)
    return result(
        "mise-versions",
        ctx,
        folder=top,
        bytes=total,
        reclaimable=total,
        frees=ctx.frees(top),
        undo="reinstalls on next use",
        items=items,
        qualifier=f"{n} old version{'s' if n != 1 else ''}",
        commands={
            "dry_run": [["mise", "prune", "--dry-run", "--tools"]],
            # --yes: without a terminal mise stops for confirmation (D141)
            "real": [["mise", "prune", "--tools", "--yes"]],
        },
        note=(
            "Versions no config file or running program uses. Omarchy's updates "
            "install new ones and never prune."
        ),
    )


def mise_folder(path, top):
    """A plain absolute path below `top`, a real folder (no symlink) the
    user owns: the only kind the helper measures (D72)."""
    if not isinstance(path, str) or os.path.normpath(path) != path:
        return False
    if not path.startswith(top.rstrip("/") + "/"):
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()


def in_git_work_tree(path, stop):
    d = os.path.dirname(path)
    while d.startswith(stop) and len(d) >= len(stop):
        if os.path.exists(os.path.join(d, ".git")):
            return True
        d = os.path.dirname(d)
    return False


def downloads(ctx, protected=()):
    top = userdirs.resolve(ctx.home, "DOWNLOAD")
    items = []
    if top is None:
        return result(
            "downloads",
            ctx,
            state="absent",
            bytes=0,
            reclaimable=0,
            note="XDG_DOWNLOAD_DIR is the home folder itself, so there is no Downloads folder.",
        )
    if not os.path.isdir(top):
        return result("downloads", ctx, bytes=0, reclaimable=0, frees=ctx.frees(top))
    w = walk.Walk(ctx.foreign, lambda p: p)
    w.run(top)
    for path, size in sorted(w.sizes.items()):
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            continue
        if not os.path.isfile(path) or os.path.islink(path) or size <= BIG_DOWNLOAD:
            continue
        if ctx.now - st.st_mtime < OLD_DOWNLOAD:
            continue
        if in_git_work_tree(path, top) or any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in protected
        ):
            continue
        item = {"path": path, "bytes": size, "modified": int(st.st_mtime)}
        if filenames.unusual(path):
            # listed, never offered or counted (D114)
            item["unusual"] = True
        items.append(item)
    items.sort(key=lambda i: (-i["bytes"], i["path"]))
    usual = [i for i in items if not i.get("unusual")]
    total = sum(i["bytes"] for i in usual)
    n = len(usual)
    return result(
        "downloads",
        ctx,
        folder=top,
        bytes=total,
        reclaimable=total,
        frees=ctx.frees(top),
        undo="restore from the Trash until it is emptied",
        items=items,
        qualifier=f"{n} file{'s' if n != 1 else ''} over 1 GiB" if n else "",
        commands={
            "dry_run": [["ls", "-l", "--", i["path"]] for i in usual],
            "real": [["gio", "trash", "--", i["path"]] for i in usual],
        },
        note="Files over 1 GiB untouched for 90 days. The Trash holds them until emptied.",
    )


def trash(ctx):
    top = os.path.join(ctx.home, ".local", "share", "Trash")
    size, bad, _ = walk.measure(top, ctx.foreign)
    if size is None:
        return needs_sudo("trash", ctx, frees=ctx.frees(top))
    return result(
        "trash",
        ctx,
        folder=top,
        bytes=size,
        reclaimable=size,
        frees=ctx.frees(top),
        undo="none: emptying is final",
        items=[{"path": top, "bytes": size}] if size else [],
        commands={
            "dry_run": [["gio", "list", "trash://"]],
            "real": [["gio", "trash", "--empty"]],
        },
        note="Emptying the Trash is final: ask first.",
        unreadable=bad,
    )


PKG_SUFFIX = re.compile(r"\.pkg\.tar(\.[a-z0-9]+)?(\.sig)?$")


def package_name(filename):
    """'linux-7.2.8-1-x86_64.pkg.tar.zst' → 'linux'; None if not a package."""
    stem = PKG_SUFFIX.sub("", filename)
    if stem == filename:
        return None
    parts = stem.rsplit("-", 3)
    return parts[0] if len(parts) == 4 else None


def pacman_cache(ctx):
    """Reclaimable = every cached version but the newest of each package
    (by modification time; paccache -rk1 compares versions, D23)."""
    top = ctx.path("/var/cache/pacman/pkg")
    shown = "/var/cache/pacman/pkg"
    try:
        names = sorted(os.listdir(top))
    except FileNotFoundError:
        return result(
            "pacman-cache",
            ctx,
            state="absent",
            frees=ctx.frees_system(shown),
        )
    except OSError:
        return needs_sudo("pacman-cache", ctx, frees=ctx.frees_system(shown))
    newest, total, files = {}, 0, []
    for name in names:
        path = os.path.join(top, name)
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            continue
        size = st.st_blocks * 512
        total += size
        pkg = package_name(name)
        files.append((name, pkg, size, path, st))
        if pkg and not name.endswith(".sig"):
            newest[pkg] = max(newest.get(pkg, (0, "")), (st.st_mtime, name))
    keep = {name for _, name in newest.values()} | {
        name + ".sig" for _, name in newest.values()
    }
    gone = [
        (size, path, st)
        for name, pkg, size, path, st in files
        if pkg and name not in keep
    ]
    reclaimable = sum(size for size, _, _ in gone)
    # the qualifier counts what would go, not what is cached (D62)
    old = sum(1 for _, path, _ in gone if not path.endswith(".sig"))
    return result(
        "pacman-cache",
        ctx,
        folder=shown,
        bytes=total,
        reclaimable=reclaimable,
        frees=ctx.frees_system(shown),
        undo="re-downloads if a downgrade is needed",
        qualifier=f"{old} old version{'s' if old != 1 else ''}" if old else "",
        measure_paths=sorted(path for _, path, _ in gone),
        items=[{"path": shown, "bytes": total}],
        commands={
            "dry_run": [["paccache", "-dk1"]],
            "real": [["sudo", "paccache", "-rk1"]],
        },
        note="omarchy update prunes this itself before its snapshot; cleaning by hand is "
        "for when the update is blocked.",
    )


def docker_running(ctx):
    code, out, _ = ctx.run(["systemctl", "is-active", "docker.service"])
    return code == 0 and out.strip() == "active"


SIZE_RE = re.compile(r"^([\d.]+)\s*([kKMGTP]?)B$")
DECIMAL = {"": 1, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}


def docker_size(text):
    """Docker prints decimal units ("1.2GB", "512.3MB"); None if unreadable."""
    m = SIZE_RE.match(text.strip().split(" ")[0])
    return int(float(m.group(1)) * DECIMAL[m.group(2)]) if m else None


def docker(ctx, socket="/var/run/docker.sock"):
    data_root = "/var/lib/docker"
    # D52: installed? running? only then readable? The socket is never
    # touched while docker.service is inactive (socket activation would
    # start dockerd), and no row shows unless the daemon already runs.
    if ctx.run(["docker", "--version"])[0] == 127:
        return result("docker", ctx, state="absent")
    if not docker_running(ctx):
        return result(
            "docker", ctx, state="not running", frees=ctx.frees_system(data_root)
        )
    if not os.access(ctx.path(socket), os.R_OK | os.W_OK):
        return needs_sudo(
            "docker",
            ctx,
            frees=ctx.frees_system(data_root),
            note="Its storage driver cannot be read without sudo, so this assumes it frees after updates.",
        )
    code, out, err = ctx.run(["docker", "system", "df", "--format", "json"])
    if code != 0:
        return result("docker", ctx, state="error", note=err.strip()[:200])
    total = reclaim = 0
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        total += docker_size(row.get("Size", "")) or 0
        reclaim += docker_size(row.get("Reclaimable", "")) or 0
    code, driver, _ = ctx.run(["docker", "info", "-f", "{{.Driver}}"])
    btrfs = code == 0 and driver.strip() == "btrfs"
    # The btrfs driver keeps layers as nested subvolumes, which snapshots of @
    # do not include; overlay2 keeps plain files on @.
    frees = (
        "now"
        if btrfs or mounts.frees_now(ctx.path(data_root), ctx.mounts)
        else "after updates"
    )
    return result(
        "docker",
        ctx,
        bytes=total,
        reclaimable=reclaim,
        frees=frees,
        undo="images re-pull; build cache rebuilds",
        # Docker's own figure: its layers map to paths only through its
        # databases, so it is shown, never counted (D75)
        qualifier="reported by Docker",
        reported="docker",
        commands={
            "dry_run": [["docker", "system", "df", "-v"]],
            "real": [
                # --force: each prune asks [y/N] otherwise (D141)
                ["docker", "builder", "prune", "--force"],
                ["docker", "image", "prune", "-a", "--force"],
            ],
        },
    )


# Where Ollama keeps its models: the user's, or the system service's (Arch
# runs it as user ollama with its home in /var/lib/ollama).
OLLAMA_STORES = ("~/.ollama/models", "/var/lib/ollama/.ollama/models",
                 "/var/lib/ollama/models", "/usr/share/ollama/.ollama/models")  # fmt: skip


def ollama_store(ctx):
    for store in OLLAMA_STORES:
        path = (os.path.join(ctx.home, store[2:]) if store.startswith("~/")
                else ctx.path(store))  # fmt: skip
        if os.path.isdir(os.path.join(path, "manifests")):
            return path
    return None


def ollama_manifests(store):
    """[(model name, manifest path, mtime, {blob paths})] from the store's
    manifests: plain JSON, each listing its config and layer digests."""
    top = os.path.join(store, "manifests")
    out = []
    for d, _, names in os.walk(top):
        for n in names:
            path = os.path.join(d, n)
            try:
                st = os.lstat(path)
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                continue
            digests = [doc.get("config", {}).get("digest")]
            digests += [layer.get("digest") for layer in doc.get("layers") or []]
            blobs = {os.path.join(store, "blobs", dg.replace(":", "-"))
                     for dg in digests if isinstance(dg, str) and dg.startswith("sha256:")}  # fmt: skip
            rel = os.path.relpath(path, top).split(os.sep)
            name = f"{rel[-2]}:{rel[-1]}" if len(rel) >= 2 else n
            out.append((name, path, st.st_mtime, blobs))
    # by name: os.walk's order is the directory's
    return sorted(out, key=lambda m: m[0])


def ollama(ctx):
    """D74: models unused for 60 days (their manifest's age, what `ollama
    list` calls modified), and exactly what `ollama rm` deletes: their
    manifests and the blobs no kept model references."""
    store = ollama_store(ctx)
    if store is None:
        return result("ollama", ctx, state="absent")
    models = ollama_manifests(store)
    stale = [m for m in models if ctx.now - m[2] >= UNUSED_MODEL]
    kept = set().union(*[m[3] for m in models if m not in stale])
    gone = sorted({b for m in stale for b in m[3]} - kept)
    gone_paths = [p for p in gone if os.path.lexists(p)] + sorted(m[1] for m in stale)

    def allocated(path):
        try:
            return os.lstat(path).st_blocks * 512
        except OSError:
            return 0

    every = set().union(*[m[3] for m in models]) if models else set()
    n = len(stale)
    items = [{"name": m[0], "path": m[1], "unused_days": int((ctx.now - m[2]) // DAY)}
             for m in stale]  # fmt: skip
    return result(
        "ollama",
        ctx,
        folder=store,
        bytes=sum(allocated(b) for b in every),
        reclaimable=sum(allocated(p) for p in gone_paths),
        frees=ctx.frees(store),
        undo="ollama pull brings a model back",
        items=items,
        qualifier=f"{n} unused for 60 days" if n else "",
        measure_paths=gone_paths,
        commands={
            "dry_run": [["ollama", "list"]],
            "real": [
                ["ollama", "rm", "--", m[0]]
                for m in stale
                if not filenames.unusual(m[0])
            ],
        },
        note='"Unused" is the manifest\'s age, the date `ollama list` shows as modified.',
    )


JOURNAL_RE = re.compile(r"take up ([\d.]+)([BKMGT])")
BINARY = {"B": 1, "K": 1024, "M": MIB, "G": GIB, "T": 1024 * GIB}


def journal(ctx):
    code, out, err = ctx.run(["journalctl", "--disk-usage"])
    m = JOURNAL_RE.search(out)
    if code != 0 or not m:
        return needs_sudo(
            "journal",
            ctx,
            frees=ctx.frees_system("/var/log/journal"),
            note=(err or out).strip()[:200],
        )
    size = int(float(m.group(1)) * BINARY[m.group(2)])
    # the files journald itself removes (D106); none known when one could
    # not be read
    gone, unreadable = journals.vacuumed(ctx.path("/var/log/journal"), JOURNAL_KEEP)
    if unreadable:
        gone = []
    return result(
        "journal",
        ctx,
        folder="/var/log/journal",
        bytes=size,
        reclaimable=sum(allocated(p) for p in gone)
        if gone
        else max(0, size - JOURNAL_KEEP),
        frees=ctx.frees_system("/var/log/journal"),
        undo="none: old logs are gone",
        measure_paths=gone,
        commands={
            "dry_run": [["journalctl", "--disk-usage"]],
            "real": [["sudo", "journalctl", "--vacuum-size=500M"]],
        },
    )


def allocated(path):
    try:
        return os.lstat(path).st_blocks * 512
    except OSError:
        return 0


def snapper_layout(ctx):
    """(standard, limit): standard is snapper's root config for / on a btrfs
    root with its snapshots at /.snapshots (D54), read without root; the
    helper checks it again as root. limit is NUMBER_LIMIT ("" if unset)."""
    root = ctx.root_mount
    try:
        with open(ctx.path("/etc/snapper/configs/root"), encoding="utf-8") as f:
            conf = f.read()
    except OSError:
        return False, ""
    m = re.search(r'^\s*NUMBER_LIMIT="(\d+)', conf, re.MULTILINE)
    standard = (
        root is not None
        and root["fstype"] == "btrfs"
        and re.search(r'^SUBVOLUME="/"$', conf, re.MULTILINE) is not None
        and re.search(r'^FSTYPE="btrfs"$', conf, re.MULTILINE) is not None
        and os.path.isdir(ctx.path("/.snapshots"))
    )
    return standard, m.group(1) if m else ""


SNAPPER_LIST = ["sudo", "snapper", "-c", "root", "--csvout", "list", "--columns",
                "number,cleanup,date"]  # fmt: skip
# /.snapshots is root-only, so the user's shell cannot expand the glob: root's
# must. What deleting every snapshot frees, without the helper (D57): the TOTAL
# "Disk Usage" of the second minus the first. compsize counts each extent once
# across all its paths, at its on-disk size (docs/machine-facts.md, dev repo).
SNAPSHOT_COMPSIZE = [
    ["sudo", "sh", "-c", "compsize -b -x /"],
    ["sudo", "sh", "-c", "compsize -b -x / /.snapshots/*/snapshot"],
]


def snapshots(ctx, measured=None):
    """Snapper's rollback points. With the root helper's measurement (D53):
    exact, in their own group, never in the reclaimable total (D54).
    Without it: "?" under Needs sudo on the standard layout, no row on any
    other (no btrfs root, no snapper, another layout)."""
    standard, limit = snapper_layout(ctx)
    if not standard:
        return result("snapshots", ctx, state="absent")
    if measured and measured.get("state") == "ok":
        size = measured["only_bytes"]
        return result(
            "snapshots",
            ctx,
            bytes=size,
            reclaimable=size,
            # the root helper's compsize difference (D53)
            exact=True,
            frees="rollback",
            undo="none: an update can no longer be rolled back to it",
            qualifier=f"{measured['snapshots']} kept for rollback",
            commands={"dry_run": [SNAPPER_LIST], "real": []},
            note=(
                "Rollback points for omarchy update, measured with compsize: deleting "
                "them all frees this much. Ask before deleting any, one numbered "
                "snapshot at a time (the cleanup skill's rule 6)."
            ),
        )
    return needs_sudo(
        "snapshots",
        ctx,
        qualifier=f"{limit} kept for rollback" if limit else "",
        commands={
            "dry_run": [
                SNAPPER_LIST,
                *SNAPSHOT_COMPSIZE,
            ],
            "real": [],
        },
        note=(
            "Their space is inside the Used view's remainder. These commands only "
            "measure (D57): what deleting every snapshot frees is the second compsize "
            "TOTAL minus the first, about a minute, so ask first. Deleting a snapshot "
            "follows the cleanup skill's rule 6."
        ),
    )


def coredumps(ctx):
    """Shown, never offered: systemd ages them out, and Omarchy's crash
    diagnosis reads them (D24). The report shows their size (D104): exact on
    ext4 from the walk, on btrfs once the helper measured the dump files
    (size_bytes); None until then, never 0 while they exist."""
    top = ctx.path("/var/lib/systemd/coredump")
    size, _, _ = walk.measure(top, ctx.foreign)
    if size is None:
        return needs_sudo(
            "coredumps",
            ctx,
            frees=ctx.frees_system("/var/lib/systemd/coredump"),
        )
    try:
        dumps = sorted(os.path.join(top, n) for n in os.listdir(top))
    except OSError:
        dumps = []
    dumps = [p for p in dumps if os.path.isfile(p) and not os.path.islink(p)]
    return result(
        "coredumps",
        ctx,
        bytes=size,
        reclaimable=0,
        frees=ctx.frees_system("/var/lib/systemd/coredump"),
        undo="",
        note="Keep: Omarchy's crash diagnosis reads them, and systemd removes old ones itself.",
        measure_paths=dumps,
        size_bytes=0 if not size else None,
    )


def paths_of(c):
    """The paths a row frees: its own list, else its items' paths."""
    return c.get("measure_paths") or [
        i["path"]
        for i in c.get("items") or []
        if i.get("path") and not i.get("unusual")
    ]


# Rows measured by path, with the helper on btrfs or natively on ext4 (D69,
# D76); Docker reports its own figure (D75)
MEASURED = ("app-caches", "aur-cache", "downloads", "trash", "pacman-cache", "journal",
            "ollama", "mise-versions", "npm-cache", "bun-cache", "go-modules",
            "rust-toolchains", "cargo-registry", "electron-caches")  # fmt: skip
# Rows shown, never offered: only their own size is measured (D104)
KEPT = ("coredumps",)


def settle(cats, ctx):
    """No estimate is ever shown (D78): on ext4 every measured row is the
    exact bytes deleting its paths frees (D76); everywhere else a row is
    exact only once the helper has measured it (apply_exact)."""
    for c in cats:
        if c["state"] != "ok" or c["id"] == "snapshots":
            continue
        if c["id"] in KEPT and ctx.fstype == "ext4":
            # no compression, no shared extents: the walk's size is exact
            c["size_bytes"] = c["bytes"]
        if not c["reclaimable"]:
            c["exact"] = True
            continue
        c["exact"] = False
        # a tool reports space its paths were not found for (an unreadable
        # folder lists none): not measured, never an exact 0
        paths = paths_of(c)
        if ctx.fstype == "ext4" and c["id"] in MEASURED and paths:
            freed, unreadable = walk.contained(paths, ctx.foreign)
            if not unreadable:
                c.update(reclaimable=freed, exact=True,
                         measured="native")  # fmt: skip
                settle_each(c, ctx)
    return cats


def settle_each(c, ctx):
    """On ext4, each offered folder alone, as the whole row (D99)."""
    for item in c["items"]:
        if item.get("path") in c["each"]:
            freed, unreadable = walk.contained([item["path"]], ctx.foreign)
            if not unreadable:
                item["frees_bytes"] = freed


def apply_exact(cats, exact):
    """The measure helper's exact bytes on the rows it measured (D69, D70):
    what deleting the row frees now, or, for a row that frees after updates,
    what it frees once the snapshots rotate. A row it refused, or did not
    measure, stays not measured (D78)."""
    if not exact or exact.get("state") != "ok":
        return cats
    for c in cats:
        e = exact["categories"].get(c["id"])
        if not e or e.get("state") != "ok" or c["state"] != "ok":
            continue
        if c["id"] in KEPT:
            c["size_bytes"] = e.get("size_bytes")
            continue
        c.update(reclaimable=frees_of(c, e), exact=True,
                 measured="helper", now_bytes=e["now_bytes"])  # fmt: skip
        for item in c["items"]:
            one = (e.get("each") or {}).get(item.get("path"))
            if one is not None:
                item["frees_bytes"] = frees_of(c, one)
    return cats


def frees_of(c, e):
    """The helper's figure the row shows: what it frees once the snapshots
    rotate for a row that frees after updates (D70), else what it frees now."""
    after = c["frees"] == "after updates" and e.get("after_bytes") is not None
    return e["after_bytes"] if after else e["now_bytes"]


# Every category's id and label, in scan order: a scanner that raises keeps its
# label on the card (D62).
LABELS = (
    ("app-caches", "App caches"),
    ("aur-cache", "AUR build cache"),
    ("mise-versions", "Old mise versions"),
    ("npm-cache", "npm cache"),
    ("pnpm-store", "pnpm store"),
    ("bun-cache", "Bun cache"),
    ("go-modules", "Go module cache"),
    ("rust-toolchains", "Rust toolchains"),
    ("cargo-registry", "Cargo registry cache"),
    ("electron-caches", "App caches in ~/.config"),
    ("downloads", "Downloads"),
    ("trash", "Trash"),
    ("pacman-cache", "Package cache"),
    ("docker", "Docker images and build cache"),
    ("ollama", "Ollama models"),
    ("journal", "System logs"),
    ("snapshots", "Snapshots"),
    ("coredumps", "Core dumps"),
)


def scan_all(ctx, protected=()):
    """Every category, in the blueprint's order; one failing never stops the rest."""
    out = []
    scanners = (
        app_caches,
        aur_cache,
        mise_versions,
        *devcaches.DEV_SCANNERS,
        devcaches.electron_caches,
        lambda c: downloads(c, protected),
        trash,
        pacman_cache,
        docker,
        ollama,
        journal,
        snapshots,
        coredumps,
    )
    for (cid, _), scan in zip(LABELS, scanners, strict=True):
        try:
            row = scan(ctx)
        except (OSError, ValueError) as e:
            row = result(cid, ctx, state="error", note=str(e)[:200])
        # a scanner with nothing to say has no row (mise absent, D100)
        if row is not None:
            out.append(row)
    return out
