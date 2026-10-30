"""cdx - a visual `cd` for Windows cmd.exe.

Keys
  type            filter folders live (top match is pre-selected)
  Up / Down       move the highlight
  Enter / Right   open the highlighted folder
  Backspace       delete a filter character; with an empty filter, go up a level
  Left            go up a level
  Ctrl+Enter      cd to the highlighted folder (if you typed/moved) or the current folder;
                  on a highlighted .bat file: cd to its folder and run it
  Ctrl+G          same as Ctrl+Enter (fallback for terminals that swallow it)
  Ctrl+H          show/hide hidden + system folders
  Ctrl+R          list of your most used / most recent folders (same filter keys; Ctrl+R,
                  Left or Backspace on an empty filter goes back)
  Esc / Ctrl+C    cancel, stay where you are

The chosen path is written to the file given by --out (cdx.cmd then runs the
real `cd /d` in the parent cmd). If a .bat file was chosen, its file name goes
to "<out>.run" and cdx.cmd calls it after the cd. Without --out the folder (and
the .bat's full path, if any) is printed to stdout.
"""
import ctypes
import json
import os
import shutil
import sys
import time
import unicodedata

IS_WIN = os.name == "nt"
HISTORY_FILE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "cdx", "history.json")
HISTORY_MAX = 200
HAS_DRIVES = IS_WIN  # a "This PC" view above drive roots
RUNNABLE = (".bat",)  # file types that are listed and run by Ctrl+Enter


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def _fold_char(c):
    """Lowercase and drop accents, always returning exactly one character."""
    d = unicodedata.normalize("NFKD", c)
    base = d[0] if d else c
    low = base.lower()
    return low if len(low) == 1 else base


def fold(s):
    """Case- and accent-insensitive form of s (same length, so positions line up)."""
    return "".join(_fold_char(c) for c in s)


def match(query, name):
    """Return (score, matched_positions) or None. Lower score = better."""
    if not query:
        return 0, []
    q, n = fold(query), fold(name)
    i = n.find(q)
    if i != -1:
        if i == 0:
            score = 0                      # prefix
        elif not n[i - 1].isalnum():
            score = 1                      # start of a word
        else:
            score = 2                      # anywhere
        return score, list(range(i, i + len(q)))
    pos, j = [], 0                         # fuzzy: letters in order
    for idx, c in enumerate(n):
        if j < len(q) and c == q[j]:
            pos.append(idx)
            j += 1
    if j == len(q):
        return 3, pos
    return None


# --------------------------------------------------------------------------
# File system helpers
# --------------------------------------------------------------------------

def list_drives():
    if IS_WIN:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return ["%s:\\" % chr(65 + i) for i in range(26) if mask & (1 << i)]
    return ["/"]


def _is_hidden(entry):
    if IS_WIN:
        try:
            return bool(entry.stat().st_file_attributes & 0x6)  # HIDDEN | SYSTEM
        except (OSError, AttributeError):
            return False
    return entry.name.startswith(".")


# --------------------------------------------------------------------------
# History (most used / most recent folders)
# --------------------------------------------------------------------------

def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_visit(path, now=None):
    """Count a cd to `path`; keeps only the HISTORY_MAX best entries. Never raises."""
    try:
        hist = load_history()
        now = time.time() if now is None else now
        key = os.path.normpath(path)
        item = hist.get(key)
        n = item.get("n", 0) if isinstance(item, dict) else 0
        hist[key] = {"n": n + 1, "t": now}
        if len(hist) > HISTORY_MAX:
            best = sorted(hist, key=lambda p: _frecency(hist[p], now), reverse=True)[:HISTORY_MAX]
            hist = {p: hist[p] for p in best}
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f)
    except Exception:
        pass


def _frecency(item, now):
    try:
        days = max(0.0, (now - item["t"]) / 86400.0)
        return item["n"] / (1.0 + days)
    except (KeyError, TypeError):
        return 0.0


def frequent_folders(now=None):
    """Existing folders from the history, best first."""
    now = time.time() if now is None else now
    hist = load_history()
    ranked = sorted(hist, key=lambda p: _frecency(hist[p], now), reverse=True)
    return [p for p in ranked if os.path.isdir(p)]


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def ell(s, w):
    if w <= 0:
        return ""
    if len(s) <= w:
        return s
    return s[:w] if w == 1 else s[: w - 1] + "\u2026"


def ell_left(s, w):
    if w <= 0:
        return ""
    if len(s) <= w:
        return s
    return s[-w:] if w == 1 else "\u2026" + s[-(w - 1):]


def style_name(shown, positions, selected, base=""):
    on_code, off_code = ("\x1b[4m", "\x1b[24m") if selected else ("\x1b[1;33m", "\x1b[22;39m" + base)
    pset, out, on = set(positions), [], False
    for i, ch in enumerate(shown):
        want = i in pset
        if want and not on:
            out.append(on_code)
            on = True
        elif on and not want:
            out.append(off_code)
            on = False
        out.append(ch)
    if on:
        out.append(off_code)
    return "".join(out)


# --------------------------------------------------------------------------
# Browser state
# --------------------------------------------------------------------------

class Browser:
    def __init__(self, path, show_hidden=False):
        self.path = path            # str, or None for the drives list
        self.show_hidden = show_hidden
        self.recent = False         # showing the most used / most recent folders list
        self.query = ""
        self.sel = 0
        self.top = 0
        self.moved = False          # user pressed an arrow key since last navigation
        self.error = ""
        self.entries = []           # (name, full, hidden, is_file)
        self.view = []              # (name, full, positions, is_file)
        self.load()

    # -- loading / filtering ------------------------------------------------
    def load(self):
        self.error = ""
        self.entries = []
        if self.recent:
            self.entries = [(p, p, False, False) for p in frequent_folders()]
        elif self.path is None:
            self.entries = [(d, d, False, False) for d in list_drives()]
        else:
            try:
                with os.scandir(self.path) as it:
                    for e in it:
                        try:
                            if e.is_dir():
                                self.entries.append((e.name, e.path, _is_hidden(e), False))
                            elif e.name.lower().endswith(RUNNABLE) and e.is_file():
                                self.entries.append((e.name, e.path, _is_hidden(e), True))
                        except OSError:
                            continue
            except OSError as ex:
                self.error = "Can't open this folder (%s)" % (ex.strerror or "error")
        self.refilter()

    def refilter(self):
        q = self.query
        show_h = self.show_hidden or q.startswith(".")
        rows = []
        for idx, (name, full, hidden, is_file) in enumerate(self.entries):
            if hidden and not show_h:
                continue
            m = match(q, name)
            if m is None:
                continue
            # the recent list keeps its ranking (idx) instead of sorting by name
            tie = ("%08d" % idx) if self.recent else name.lower()
            rows.append((m[0], is_file, len(name) if q else 0, tie, name, full, m[1]))
        rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))   # folders before files on equal score
        self.view = [(r[4], r[5], r[6], r[1]) for r in rows]
        self.sel = min(self.sel, max(0, len(self.view) - 1))

    def _reset_nav(self):
        self.query, self.sel, self.top, self.moved = "", 0, 0, False

    # -- actions --------------------------------------------------------------
    def enter(self):
        if not self.view or self.view[self.sel][3]:
            return                          # nothing here, or a file (Ctrl+Enter runs it)
        self.path = self.view[self.sel][1]
        self.recent = False
        self._reset_nav()
        self.load()

    def toggle_recent(self):
        self.recent = not self.recent
        self._reset_nav()
        self.load()

    def up(self):
        if self.recent:
            self.toggle_recent()
            return
        if self.path is None:
            return
        came_from = self.path
        parent = os.path.dirname(self.path)
        if parent == self.path:             # at a drive root
            if not HAS_DRIVES:
                return
            self.path = None
        else:
            self.path = parent
        self._reset_nav()
        self.load()
        for i, (_, full, _, _) in enumerate(self.view):
            if os.path.normcase(full) == os.path.normcase(came_from):
                self.sel = i
                break

    def move(self, delta, wrap=True):
        n = len(self.view)
        if not n:
            return
        if wrap and abs(delta) == 1:
            self.sel = (self.sel + delta) % n
        else:
            self.sel = min(max(self.sel + delta, 0), n - 1)
        self.moved = True

    @staticmethod
    def _pick(item):
        full, is_file = item[1], item[3]
        return (os.path.dirname(full), full) if is_file else (full, None)

    def target(self):
        """What Ctrl+Enter would do right now: (folder to cd to, .bat to run there or None)."""
        if self.path is None or self.recent:
            return self._pick(self.view[self.sel]) if self.view else None
        if (self.query or self.moved) and self.view:
            return self._pick(self.view[self.sel])
        return self.path, None

    def handle(self, key, page=10):
        if key == "up":
            self.move(-1)
        elif key == "down":
            self.move(1)
        elif key == "pgup":
            self.move(-page, wrap=False)
        elif key == "pgdn":
            self.move(page, wrap=False)
        elif key == "home":
            self.sel, self.moved = 0, True
        elif key == "end":
            self.sel, self.moved = max(0, len(self.view) - 1), True
        elif key in ("enter", "right"):
            self.enter()
        elif key == "left":
            self.up()
        elif key == "backspace":
            if self.query:
                self.query = self.query[:-1]
                self.sel, self.top, self.moved = 0, 0, False
                self.refilter()
            else:
                self.up()
        elif key == "clear":
            self.query = ""
            self.sel, self.top, self.moved = 0, 0, False
            self.refilter()
        elif key == "hidden":
            self.show_hidden = not self.show_hidden
            self.refilter()
        elif key == "recent":
            self.toggle_recent()
        elif len(key) == 1 and key.isprintable():
            if key == " " and not self.query:
                return
            self.query += key
            self.sel, self.top, self.moved = 0, 0, False
            self.refilter()

    # -- drawing --------------------------------------------------------------
    def render(self, w, h):
        w = max(w, 24)
        usable = w - 1                       # never touch the last column
        list_h = max(3, h - 7)
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + list_h:
            self.top = self.sel - list_h + 1
        self.top = max(0, min(self.top, max(0, len(self.view) - list_h)))

        if self.recent:
            title = "Most used / recent folders"
        else:
            title = "This PC (drives)" if self.path is None else self.path
        dim, off = "\x1b[2m", "\x1b[0m"
        lines = [
            " \x1b[1;36mcdx\x1b[0m  \x1b[1m%s\x1b[0m" % ell_left(title, usable - 6),
            " \x1b[32m>\x1b[0m %s\x1b[7m \x1b[0m" % ell_left(self.query, usable - 4),
            dim + "\u2500" * usable + off,
        ]

        if not self.view:
            empty = "No history yet: folders you cd to will show up here" if self.recent else "No subfolders or .bat files"
            msg = self.error or ("No folder matches \"%s\"" % self.query if self.query else empty)
            lines.append("   " + dim + ell(msg, usable - 3) + off)
            blanks = list_h - 1
        else:
            blanks = 0
            for i in range(self.top, min(len(self.view), self.top + list_h)):
                name, _, pos, is_file = self.view[i]
                shown = ell(name, usable - 3)
                base = "\x1b[36m" if is_file else ""        # .bat files in cyan
                body = style_name(shown, pos, i == self.sel, base)
                if i == self.sel:
                    pad = " " * (usable - 3 - len(shown))
                    lines.append("\x1b[7m > " + body + pad + off)
                else:
                    lines.append("   " + base + body + ("\x1b[39m" if is_file else ""))
            blanks = list_h - (min(len(self.view), self.top + list_h) - self.top)
        lines.extend([""] * blanks)

        count = " %d/%d " % (self.sel + 1, len(self.view)) if self.view else " 0 "
        lines.append(dim + "\u2500" * (usable - len(count)) + count + off)

        tgt = self.target()
        if tgt and tgt[1]:
            label = " Ctrl+Enter: run %s in " % ell(os.path.basename(tgt[1]), 30)
        else:
            label = " Ctrl+Enter: cd to "
        if tgt:
            lines.append("\x1b[1m%s\x1b[0m%s" % (label, ell_left(tgt[0], usable - len(label))))
        else:
            lines.append("\x1b[1m Ctrl+Enter:\x1b[0m nothing selected")
        hint = " Enter open \u00b7 Backspace up \u00b7 ^H hidden:%s \u00b7 ^R recent \u00b7 Esc cancel" % ("on" if self.show_hidden else "off")
        lines.append(dim + ell(hint, usable) + off)

        return "\x1b[H" + "\x1b[K\r\n".join(lines) + "\x1b[K\x1b[J"


# --------------------------------------------------------------------------
# Main loop (I/O is injected so it can be tested)
# --------------------------------------------------------------------------

def run(start, read_key, write, get_size, show_hidden=False):
    b = Browser(start, show_hidden)
    while True:
        w, h = get_size()
        write(b.render(w, h))
        key = read_key()
        if key == "esc":
            return None
        if key == "go":
            t = b.target()
            if t:
                return t
            continue
        b.handle(key, page=max(3, h - 7))


# --------------------------------------------------------------------------
# Windows console plumbing
# --------------------------------------------------------------------------

class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", ctypes.c_int32),
                ("wRepeatCount", ctypes.c_uint16),
                ("wVirtualKeyCode", ctypes.c_uint16),
                ("wVirtualScanCode", ctypes.c_uint16),
                ("uChar", ctypes.c_uint16),
                ("dwControlKeyState", ctypes.c_uint32)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_uint8 * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", ctypes.c_uint16), ("Event", _EVENT)]


_VK = {0x1B: "esc", 0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
       0x21: "pgup", 0x22: "pgdn", 0x24: "home", 0x23: "end"}


class WinConsole:
    def __enter__(self):
        k = self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetStdHandle.restype = ctypes.c_void_p
        k.GetStdHandle.argtypes = [ctypes.c_uint32]
        k.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        k.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.ReadConsoleInputW.argtypes = [ctypes.c_void_p, ctypes.POINTER(INPUT_RECORD),
                                        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        k.FlushConsoleInputBuffer.argtypes = [ctypes.c_void_p]
        self.hin, self.hout = k.GetStdHandle(-10), k.GetStdHandle(-11)

        m_in, m_out = ctypes.c_uint32(), ctypes.c_uint32()
        if not (k.GetConsoleMode(self.hin, ctypes.byref(m_in)) and
                k.GetConsoleMode(self.hout, ctypes.byref(m_out))):
            raise SystemExit("cdx must be run from a console window (cmd.exe).")
        self.old_in, self.old_out = m_in.value, m_out.value
        # raw key events: no line editing, no echo, Ctrl+C arrives as a key
        k.SetConsoleMode(self.hin, self.old_in & ~(0x0001 | 0x0002 | 0x0004 | 0x0200))
        # ANSI escape sequences on
        k.SetConsoleMode(self.hout, self.old_out | 0x0001 | 0x0004)
        k.FlushConsoleInputBuffer(self.hin)
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
        self.write("\x1b[?1049h\x1b[?25l")   # alternate screen, hide cursor
        return self

    def __exit__(self, *exc):
        self.write("\x1b[?25h\x1b[?1049l")
        self.k.SetConsoleMode(self.hin, self.old_in)
        self.k.SetConsoleMode(self.hout, self.old_out)
        return False

    def write(self, s):
        sys.stdout.write(s)
        sys.stdout.flush()

    @staticmethod
    def size():
        c, r = shutil.get_terminal_size((80, 25))
        return c, r

    def read_key(self):
        rec, n = INPUT_RECORD(), ctypes.c_uint32(0)
        while True:
            if not self.k.ReadConsoleInputW(self.hin, ctypes.byref(rec), 1, ctypes.byref(n)):
                raise OSError("cannot read console input")
            if rec.EventType == 4:           # window resized
                return "resize"
            if rec.EventType != 1:
                continue
            ev = rec.Event.KeyEvent
            if not ev.bKeyDown:
                continue
            vk, ch, state = ev.wVirtualKeyCode, chr(ev.uChar), ev.dwControlKeyState
            ctrl = bool(state & 0x000C)      # left/right Ctrl
            if ch == "\n" or (vk == 0x0D and ctrl):
                return "go"                  # Ctrl+Enter (or Ctrl+J)
            if vk == 0x0D:
                return "enter"
            if ch == "\x07":
                return "go"                  # Ctrl+G
            if ch == "\x03":
                return "esc"                 # Ctrl+C
            if vk == 0x08:
                return "clear" if ctrl else "backspace"
            if ctrl and vk == 0x48:
                return "hidden"              # Ctrl+H (Backspace has its own vk, 0x08)
            if ctrl and vk == 0x52:
                return "recent"              # Ctrl+R
            if vk in _VK:
                return _VK[vk]
            if ch and ch.isprintable():
                return ch


def _cmd_text(path):
    """(text, codepage) that cmd's `set /p` will read back correctly; names the
    console code page can't hold fall back to the 8.3 short path."""
    if not IS_WIN:
        return path, "utf-8"
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetConsoleCP.restype = ctypes.c_uint32
    enc = "cp%d" % k.GetConsoleCP()
    try:
        path.encode(enc)
        return path, enc
    except (UnicodeEncodeError, LookupError):
        buf = ctypes.create_unicode_buffer(1024)
        k.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        short = buf.value if k.GetShortPathNameW(path, buf, 1024) else path
        return short, enc


def encode_for_cmd(path):
    """Bytes cmd's `set /p` will read back correctly under the current code page."""
    text, enc = _cmd_text(path)
    return text.encode(enc, "replace")


def main(argv):
    out_file = None
    if "--out" in argv:
        i = argv.index("--out")
        out_file = argv[i + 1] if i + 1 < len(argv) else None
    if not IS_WIN:
        sys.stderr.write("cdx is built for Windows cmd.exe.\n")
        return 2
    result = None
    try:
        with WinConsole() as con:
            result = run(os.path.abspath(os.getcwd()), con.read_key, con.write, con.size)
    except KeyboardInterrupt:
        pass
    if not result:
        return 1
    dest, bat = result
    record_visit(dest)
    if out_file:
        if bat:
            text, enc = _cmd_text(bat)          # folder and file name both have to survive cmd
            dest_bytes = os.path.dirname(text).encode(enc, "replace")
            with open(out_file + ".run", "wb") as f:
                f.write(os.path.basename(text).encode(enc, "replace") + b"\r\n")
        else:
            dest_bytes = encode_for_cmd(dest)
        with open(out_file, "wb") as f:      # written last: cdx.cmd keys off this file
            f.write(dest_bytes + b"\r\n")
    else:
        print(dest)
        if bat:
            print(bat)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
