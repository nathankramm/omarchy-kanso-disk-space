"""File and folder names are data, never instructions (D114).

Other programs choose most of the names the report lists (a website names a
download, an app names its cache folder), and an agent reads the report. So:
  - a name is shown escaped: control, newline and bidi characters, and bytes
    that are not UTF-8, as visible escapes; a backslash as two;
  - it is capped at CAP characters, with a short hash of the true name;
  - a path whose true name holds any of those characters is unusual: listed,
    never offered, with no command, since a command made from the escaped
    name would point at another file.
Commands themselves are built from the true path (report.cmd quotes them).
"""

import hashlib

CAP = 200
# bidi marks and embeddings/overrides/isolates (U+200E/F, U+202A–202E,
# U+2066–2069): they reorder what the reader sees
BIDI = frozenset([0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)])
NAMED = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def odd(ch):
    """A character a name must not carry into a command or a reader's view:
    C0 and C1 controls, DEL, bidi, and os.fsdecode's stand-ins for bytes that
    are not UTF-8 (lone surrogates)."""
    o = ord(ch)
    return o < 0x20 or 0x7F <= o <= 0x9F or o in BIDI or 0xD800 <= o <= 0xDFFF


def unusual(text):
    return isinstance(text, str) and any(odd(ch) for ch in text)


def escape(ch):
    if ch in NAMED:
        return NAMED[ch]
    o = ord(ch)
    if 0xDC80 <= o <= 0xDCFF:
        # a byte that is not UTF-8, as os.fsdecode carries it
        return f"\\x{o - 0xDC00:02x}"
    return f"\\u{o:04x}" if o > 0xFF else f"\\x{o:02x}"


def shown(text):
    """`text` with every odd character a visible escape, at most CAP
    characters: a longer one is cut, with the first 8 hex digits of its true
    name's sha256."""
    out = "".join(
        "\\\\" if ch == "\\" else escape(ch) if odd(ch) else ch for ch in text
    )
    if len(out) <= CAP:
        return out
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"{out[: CAP - 20]}… [sha256 {digest}]"
