"""The optional root helper's snapshot measurement (D53).

`kanso-disk-space measure-install` puts bin/kanso-disk-measure at INSTALLED,
root-owned, with a sudoers rule for exactly that path and no arguments. The
daily scan calls run(), which never runs a helper that differs from the
plugin's copy (sha256), runs a matching one with `sudo -n` under a hard
timeout, and trusts nothing in its output beyond the fields it checks.

    state  ok · absent · out-of-date · refused · denied · timeout ·
           unsupported · incomplete · invalid · error · link
"link" is the helper refusing because the plugin folder or its copy is
reached through a symlink (exit 3, D98), with its own line as `reason`
(D110).
"timeout" is the run killed at TIMEOUT, or a compsize pass inside it that
ran out of time (the helper's timed_out, D101). Only "ok" carries numbers:
snapshots, root_bytes, all_bytes, only_bytes, and the exact block
(D69–D73): {state ok · refused · incomplete · stale-request, reason, rows
{home, system, var, swap, snapshots}, children {path: bytes}, unreached
[names], categories {id: {state, now_bytes, after_bytes, each {path:
{now_bytes, after_bytes}}} (D99), or for a kept row {state, size_bytes}
(D104)}.

The paths come from a request the engine writes as the user just before it
runs the helper (request_doc, REQUEST_NAME); a response is applied only when
it names that request's sha256.

Test hooks, honoured only under the test suite (KANSO_DISK_TESTING=1, as
D38): KANSO_DISK_TEST_MEASURE_INSTALLED (the installed path),
KANSO_DISK_TEST_MEASURE_ARGV (a JSON argv run instead of sudo -n), and
KANSO_DISK_TEST_MEASURE_TIMEOUT (seconds).
"""

import hashlib
import json
import os
import subprocess

import categories
import units

INSTALLED = "/usr/local/libexec/kanso-disk-measure"
PLUGIN_COPY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bin",
    "kanso-disk-measure",
)
# Each compsize pass inside the helper stops at 300 s; the whole run here.
TIMEOUT = 660
OUT_OF_DATE = "measure helper out of date: run kanso-disk-space measure-install"
FIELDS = ("snapshots", "root_bytes", "all_bytes", "only_bytes")
REQUEST_NAME = "measure-request.json"
ROWS = ("home", "system", "var", "swap", "snapshots")
CHILDREN_PER_ROW = 3
SUDO = ("sudo", "-n")
# the helper's refusal for a symlink on the way to its plugin copy (D98)
LINK_EXIT = 3
LINK_WORDS = "which the helper does not follow"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hook(name):
    """A KANSO_DISK_TEST_MEASURE_* value, only under the test suite."""
    if os.environ.get("KANSO_DISK_TESTING") != "1":
        return None
    return os.environ.get(f"KANSO_DISK_TEST_MEASURE_{name}") or None


def installed_path():
    return hook("INSTALLED") or INSTALLED


def status():
    """absent · out-of-date · current, without running anything."""
    path = installed_path()
    if not os.path.exists(path):
        return "absent"
    try:
        same = sha256(path) == sha256(PLUGIN_COPY)
    except OSError:
        return "out-of-date"
    return "current" if same else "out-of-date"


def parse(text):
    """{state, ...} from the helper's one JSON line; never trusted further."""
    lines = text.strip().splitlines()
    try:
        doc = json.loads(lines[-1]) if lines else None
    except ValueError:
        doc = None
    # only a sha-matched helper runs, and it speaks schema 2
    if not isinstance(doc, dict) or doc.get("schema") != 2:
        return {"state": "invalid"}
    if doc.get("supported") is False:
        return {"state": "unsupported"}
    if doc.get("supported") is not True:
        return {"state": "invalid"}
    if doc.get("complete") is not True:
        return {"state": "timeout" if doc.get("timed_out") is True else "incomplete"}
    if not all(units.whole(doc.get(k), 0) for k in (*FIELDS, "measured_at")):
        return {"state": "invalid"}
    # the helper clamps: run-to-run churn can put all a little under root
    # when the snapshots hold almost nothing (D53)
    if doc["only_bytes"] != max(0, doc["all_bytes"] - doc["root_bytes"]):
        return {"state": "invalid"}
    sha = doc.get("request_sha256")
    return {"state": "ok", "measured_at": doc["measured_at"]} | {
        k: doc[k] for k in FIELDS
    } | {"exact": parse_exact(doc.get("exact")),
         "request_sha256": sha if isinstance(sha, str) else ""}  # fmt: skip


def parse_exact(block):
    """The exact block, checked field by field; anything malformed is
    {"state": "invalid"}, and no row is measured by it."""
    if not isinstance(block, dict) or block.get("state") not in (
        "ok",
        "refused",
        "incomplete",
    ):
        return {"state": "invalid"}
    if block["state"] != "ok":
        reason = block.get("reason")
        return {
            "state": block["state"],
            "reason": reason if isinstance(reason, str) else "",
        }
    rows, children = block.get("rows"), block.get("children")
    unreached, cats = block.get("unreached"), block.get("categories")
    if (
        not isinstance(rows, dict)
        or set(rows) != set(ROWS)
        or not all(units.whole(v, 0) for v in rows.values())
        or not isinstance(children, dict)
        or not all(
            isinstance(k, str) and units.whole(v, 0) for k, v in children.items()
        )
        or not isinstance(unreached, list)
        or not all(isinstance(u, str) for u in unreached)
        or not isinstance(cats, dict)
    ):
        return {"state": "invalid"}
    out_cats = {}
    for cid, c in cats.items():
        if not isinstance(c, dict) or c.get("state") != "ok":
            out_cats[cid] = {
                "state": c.get("state") if isinstance(c, dict) else "invalid"
            }
            continue
        if cid in categories.KEPT:
            size = c.get("size_bytes")
            out_cats[cid] = ({"state": "ok", "size_bytes": size}
                             if units.whole(size, 0) else {"state": "invalid"})  # fmt: skip
            continue
        if not freed(c):
            out_cats[cid] = {"state": "invalid"}
            continue
        out_cats[cid] = {
            "state": "ok",
            "now_bytes": c["now_bytes"],
            "after_bytes": c.get("after_bytes"),
            "each": each_of(c.get("each")),
        }
    return {"state": "ok", "rows": dict(rows), "children": dict(children),
            "unreached": list(unreached), "categories": out_cats}  # fmt: skip


def freed(c):
    """A {now_bytes, after_bytes} the engine may read: whole bytes, after
    None or whole."""
    if not isinstance(c, dict):
        return False
    after = c.get("after_bytes")
    return units.whole(c.get("now_bytes"), 0) and (
        after is None or units.whole(after, 0)
    )


def each_of(each):
    """The folder figures (D99); a malformed one is dropped, never shown."""
    if not isinstance(each, dict):
        return {}
    return {
        p: {"now_bytes": e["now_bytes"], "after_bytes": e.get("after_bytes")}
        for p, e in each.items()
        if isinstance(p, str) and freed(e)
    }


def request_doc(cats, ledger_doc, now, essentials=False):
    """What the helper is asked to measure: each measured category's paths
    and frees, and each folder row's named folders (D72). `essentials` (the
    retry after a timeout, D121) leaves out every per-folder figure: no
    Usage folders, no offered folder alone; the helper skips what a request
    does not name, so this needs no change to it."""
    out = {"schema": 1, "written_at": now, "categories": {}, "children": {}}
    for c in cats:
        if c["id"] in categories.KEPT and c["state"] == "ok" and c.get("measure_paths"):
            # its size only: shown, never offered (D104)
            out["categories"][c["id"]] = {"kept": True, "paths": c["measure_paths"]}
            continue
        if (
            c["id"] not in categories.MEASURED
            or c["state"] != "ok"
            or not c["reclaimable"]
        ):
            continue
        paths = categories.paths_of(c)
        if paths:
            out["categories"][c["id"]] = {"frees": c["frees"], "paths": sorted(paths)}
            if c.get("each") and not essentials:
                # the offered folders, each alone, biggest first (D99)
                out["categories"][c["id"]]["each"] = c["each"]
    for row in [] if essentials else (ledger_doc or {}).get("rows", []):
        if row["id"] in ("home", "system", "var") and row["children"]:
            out["children"][row["id"]] = [
                os.path.join(row["path"], c["path"])
                for c in row["children"][:CHILDREN_PER_ROW]
            ]
    return out


def request_bytes(doc):
    return (json.dumps(doc, sort_keys=True) + "\n").encode()


def refused_under_tests():
    """Under the test suite the real installed helper is never run, whatever
    the environment says (D60): a test that forgets the INSTALLED hook gets
    "refused", never `sudo -n` of the real thing."""
    if os.environ.get("KANSO_DISK_TESTING") != "1":
        return False
    return os.path.realpath(installed_path()) == os.path.realpath(INSTALLED)


def run(request_sha="", known=None):
    """The measurement, or why there is none. Never raises. The exact block
    is kept only when it answers the request with `request_sha`."""
    if refused_under_tests():
        return {"state": "refused"}
    # the caller's status() when it already has one
    state = status() if known is None else known
    if state == "absent":
        return {"state": "absent"}
    if state == "out-of-date":
        # never run a helper that is not the plugin's own copy
        return {"state": "out-of-date", "message": OUT_OF_DATE}
    argv_hook = hook("ARGV")
    argv = json.loads(argv_hook) if argv_hook else [*SUDO, installed_path()]
    timeout = int(hook("TIMEOUT") or TIMEOUT)
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"state": "timeout"}
    except OSError:
        return {"state": "error"}
    if p.returncode != 0:
        line = (p.stderr.strip().splitlines() or [""])[-1]
        if p.returncode == LINK_EXIT and LINK_WORDS in line:
            # a dotfiles tool's symlink, not a failure: its own words (D110)
            return {"state": "link", "reason": line}
        denied = "password" in p.stderr or "not allowed" in p.stderr
        return {"state": "denied" if denied else "error"}
    out = parse(p.stdout)
    exact = out.get("exact")
    if (
        exact
        and exact.get("state") == "ok"
        and out.get("request_sha256") != request_sha
    ):
        # an answer to another request (a race, or a stale run): not applied
        out["exact"] = {"state": "stale-request", "reason": ""}
    return out
