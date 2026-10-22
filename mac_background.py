"""Ovládání Liftaga mimo popředí.

Okno se na dobu měření schová. Tlačítka se mačkají přes AXUIElementPerformAction
(AXPress) a text se zapisuje do hodnoty prvku. Hledá se jen ve oknech, ne v
menu baru, a hledání má strop 8 s. Kurzor se nečte ani neposouvá. Klávesy do
procesu jdou jen záložně (CGEventPostToPid), nikdy do aktivního okna uživatele.
"""
import ctypes
import logging
import subprocess
import sys
import time

log = logging.getLogger("background")

# Jen schování, pozice a pid. Popisky a adresy sem nepatří: jejich hledání je v AX.
ACTION_SCRIPT = r'''
on run
  set axArgs to {%%AX_ARGS%%}
  set bid to item 1 of axArgs
  set mode to item 2 of axArgs
  tell application "System Events"
    set proc to first process whose bundle identifier is bid
    if mode is "pid" then
      return (unix id of proc) as text
    else if mode is "pos" then
      set p to position of window 1 of proc
      return ((item 1 of p) as integer as text) & "," & ((item 2 of p) as integer as text)
    else if mode is "hide" then
      set visible of proc to false
      set frontmost of proc to false
      my giveBack(item 6 of axArgs)
    else if mode is "show" then
      set visible of proc to true
      set frontmost of proc to false
      my giveBack(item 6 of axArgs)
    else if mode is "place" then
      set x to (item 4 of axArgs) as integer
      set y to (item 5 of axArgs) as integer
      set visible of proc to true
      try
        set position of window 1 of proc to {x, y}
      end try
      set frontmost of proc to false
      my giveBack(item 6 of axArgs)
    end if
  end tell
end run

on giveBack(appName)
  if appName is "" then return
  tell application "System Events"
    try
      set frontmost of (first process whose name is appName) to true
    end try
  end tell
end giveBack
'''

SKIP_ROLES = frozenset({"AXMenuBar", "AXMenu", "AXMenuItem", "AXMenuBarItem"})
PRESS_ROLES = frozenset({
    "AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
    "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
    "AXGroup", "AXCell", "AXRow",
})
VALUE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"})
PREFIX_ROLES = frozenset({
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXGroup",
})
_EXACT_KEYS = ("desc", "name", "title", "axid", "placeholder")
_SEARCH_LIMIT = 8
_NODE_LIMIT = 8000
_UTF8 = 0x08000100


class AxError(RuntimeError):
    pass


def applescript_literal(value):
    """Text, který smí do zdroje AppleScriptu. Čárky, uvozovky a lomítka zůstanou data."""
    text = "" if value is None else str(value)
    parts = []
    buf = []

    def flush():
        if buf:
            parts.append('"' + "".join(buf) + '"')
            buf.clear()

    for ch in text:
        if ch == "\\":
            buf.append("\\\\")
        elif ch == '"':
            buf.append('\\"')
        elif ch in "\r\n":
            flush()
            parts.append("return")
        elif ord(ch) < 32:
            flush()
            parts.append(f"(ASCII character {ord(ch)})")
        else:
            buf.append(ch)
    flush()
    return " & ".join(parts) if parts else '""'


def build_script(args):
    literals = ", ".join(f"({applescript_literal(a)})" for a in args)
    if "%%AX_ARGS%%" not in ACTION_SCRIPT:
        raise AxError("Šablona skriptu nemá místo pro argumenty")
    return ACTION_SCRIPT.replace("%%AX_ARGS%%", literals, 1)


def classify(info, wanted):
    """exact, prefix, nebo None. Prázdný popisek se neshoduje s ničím."""
    if not wanted or not info:
        return None
    for key in _EXACT_KEYS:
        if info.get(key) == wanted:
            return "exact"
    if len(wanted) < 12:
        return None
    role = info.get("role") or ""
    if role not in PREFIX_ROLES:
        return None
    desc = info.get("desc") or ""
    name = info.get("name") or ""
    if len(desc) > 8 and wanted.startswith(desc):
        return "prefix"
    if len(name) > 8 and wanted.startswith(name):
        return "prefix"
    return None


def search_tree(roots, wanted, children_of, describe, deadline, limit=_NODE_LIMIT,
                release=None, accept_exact=None):
    """První přesná shoda ve stromech oken. Menu bar se neotevírá.

    accept_exact, když je dané, pustí dál shodu, která mu nesedí, a nechá si ji
    jako zálohu. První prefix se použije, až když přesná shoda není.
    """
    def drop(node):
        if release is not None and node is not None:
            release(node)

    fallback = None
    prefix = None
    seen = 0
    stack = list(reversed(list(roots or ())))
    try:
        while stack:
            if time.monotonic() >= deadline:
                raise AxError("AX hledání překročilo limit času")
            node = stack.pop()
            seen += 1
            if seen > limit:
                drop(node)
                raise AxError("AX hledání překročilo limit prvků")
            keep = False
            try:
                info = describe(node) or {}
                role = info.get("role") or ""
                if role in SKIP_ROLES:
                    continue
                kind = classify(info, wanted)
                if kind == "exact" and (accept_exact is None or accept_exact(info)):
                    keep = True
                    drop(fallback)
                    drop(prefix)
                    fallback = None
                    prefix = None
                    for leftover in stack:
                        drop(leftover)
                    stack.clear()
                    return node
                if kind == "exact" and fallback is None:
                    fallback = node
                    keep = True
                elif kind == "prefix" and prefix is None:
                    prefix = node
                    keep = True
                stack.extend(reversed(list(children_of(node) or ())))
            finally:
                if not keep:
                    drop(node)
        if fallback is not None:
            found = fallback
            fallback = None
            return found
        if prefix is not None:
            found = prefix
            prefix = None
            return found
        raise AxError("AX element not found")
    finally:
        drop(fallback)
        drop(prefix)
        for leftover in stack:
            drop(leftover)


def _run(args, timeout=5):
    # Argumenty nejdou na příkazový řádek: osascript by je bral jako zdroj skriptu.
    proc = subprocess.run(
        ["osascript", "-"],
        input=build_script(args).encode("utf-8"),
        capture_output=True,
        timeout=timeout,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        lines = (err or out).strip().splitlines()
        msg = lines[-1] if lines else "osascript selhal"
        raise AxError(msg[:300])
    return out.strip()


def _shell(bundle_id, mode, front_name=None, x="", y=""):
    return _run(
        [bundle_id, mode, "", "" if x == "" else int(x), "" if y == "" else int(y), front_name or ""],
        timeout=5,
    )


def frontmost_name():
    """Jméno aplikace, kterou má uživatel v popředí. None, když to nejde zjistit."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to name of first process whose frontmost is true'],
            text=True, capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    name = (proc.stdout or "").strip()
    return name or None


def launch_hidden(bundle_id):
    """Spustí appku, aniž by ji přenesl do popředí."""
    subprocess.run(["open", "-g", "-b", bundle_id], check=False, capture_output=True)


def window_position(bundle_id):
    try:
        raw = _shell(bundle_id, "pos")
        x, y = raw.split(",")
        return int(x), int(y)
    except (AxError, OSError, subprocess.TimeoutExpired, ValueError):
        return None


def place(bundle_id, x, y, front_name=None):
    """Vrátí okno na uloženou pozici a fokus uživateli. Kurzorem nehýbe."""
    _shell(bundle_id, "place", front_name, x, y)


def hide_app(bundle_id, front_name=None):
    """Schová okno a vrátí fokus uživateli. Kurzorem nehýbe."""
    _shell(bundle_id, "hide", front_name)


def show_unfocused(bundle_id, front_name=None):
    """Nechá okno existovat za ostatními, bez aktivace a bez pohybu kurzoru."""
    _shell(bundle_id, "show", front_name)


def _refuse_order(wanted):
    text = (wanted or "").strip()
    if text == "Objednat" or text.startswith("Objednat "):
        raise AxError("Bot nesmí stisknout Objednat")


def element_exists(bundle_id, wanted):
    """True, když je prvek ve oknech appky. Nic nemačká a kurzorem nehýbe."""
    if sys.platform != "darwin" or not (wanted or "").strip():
        return False
    try:
        _ax_action(bundle_id, wanted, "exists")
    except AxError:
        return False
    return True


def press(bundle_id, wanted):
    log.info("AXPress %s", (wanted or "")[:80])
    _refuse_order(wanted)
    _ax_action(bundle_id, wanted, "press")


def set_value(bundle_id, wanted, value):
    log.info("AXValue %s", (wanted or "")[:80])
    _ax_action(bundle_id, wanted, "set", value)


def focus_element(bundle_id, wanted, front_name=None):
    _ax_action(bundle_id, wanted, "focus")
    try:
        hide_app(bundle_id, front_name)
    except (AxError, OSError, subprocess.TimeoutExpired):
        log.warning("Fokus uživatele se nepodařilo vrátit")


def process_pid(bundle_id):
    try:
        return int(_shell(bundle_id, "pid"))
    except subprocess.TimeoutExpired as exc:
        raise AxError("Zjištění procesu Liftaga překročilo 5 s") from exc


def _ax_action(bundle_id, wanted, mode, value=""):
    if sys.platform != "darwin":
        raise AxError("Zpřístupnění funguje jen na macOS")
    if not wanted:
        raise AxError("AX element not found")
    pid = process_pid(bundle_id)
    ax = _Ax()
    app = ax.application(pid)
    try:
        accept = None
        if mode == "press":
            accept = lambda info: (info.get("role") or "") in PRESS_ROLES
        elif mode == "set":
            accept = lambda info: (info.get("role") or "") in VALUE_ROLES
        deadline = time.monotonic() + _SEARCH_LIMIT
        node = search_tree(
            ax.roots(app),
            wanted,
            ax.children,
            lambda el: ax.describe(el, wanted),
            deadline,
            release=ax.release,
            accept_exact=accept,
        )
        try:
            if mode == "press":
                ax.perform_press(node)
            elif mode == "set":
                ax.set_value(node, value)
            elif mode == "exists":
                return
            else:
                ax.focus(node)
        finally:
            ax.release(node)
    finally:
        ax.release(app)


class _Ax:
    """Omezené čtení stromu oken. Na prvek je strop 0,25 s, ať System Events nezamrzne."""

    def __init__(self):
        self.cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/"
            "HIServices.framework/HIServices")
        self._bind()
        self.k_windows = self._sym("kAXWindowsAttribute")
        self.k_children = self._sym("kAXChildrenAttribute")
        self.k_role = self._sym("kAXRoleAttribute")
        self.k_desc = self._sym("kAXDescriptionAttribute")
        self.k_title = self._sym("kAXTitleAttribute")
        self.k_value = self._sym("kAXValueAttribute")
        self.k_ident = self._sym("kAXIdentifierAttribute")
        self.k_placeholder = self._sym("kAXPlaceholderValueAttribute")
        self.k_focused = self._sym("kAXFocusedAttribute")
        self.k_press = self._sym("kAXPressAction")
        self.k_true = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
        self._string_tid = self.cf.CFStringGetTypeID()
        self._array_tid = self.cf.CFArrayGetTypeID()

    def _bind(self):
        ax, cf = self.ax, self.cf
        ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
        ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
        ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementSetAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        ax.AXUIElementSetAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementPerformAction.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ax.AXUIElementPerformAction.restype = ctypes.c_int32
        ax.AXUIElementSetMessagingTimeout.argtypes = [ctypes.c_void_p, ctypes.c_float]
        ax.AXUIElementSetMessagingTimeout.restype = ctypes.c_int32
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRetain.argtypes = [ctypes.c_void_p]
        cf.CFRetain.restype = ctypes.c_void_p
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetTypeID.argtypes = []
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFArrayGetTypeID.argtypes = []
        cf.CFArrayGetTypeID.restype = ctypes.c_ulong
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool

    def _sym(self, name):
        return ctypes.c_void_p.in_dll(self.ax, name)

    def release(self, ref):
        if ref:
            self.cf.CFRelease(ref)

    def _arm(self, el):
        if el:
            self.ax.AXUIElementSetMessagingTimeout(el, ctypes.c_float(0.25))

    def application(self, pid):
        app = self.ax.AXUIElementCreateApplication(int(pid))
        if not app:
            raise AxError("Proces Liftaga nemá zpřístupnění")
        self._arm(app)
        return app

    def _copy(self, el, attr):
        out = ctypes.c_void_p()
        err = self.ax.AXUIElementCopyAttributeValue(el, attr, ctypes.byref(out))
        if err != 0 or not out.value:
            return None
        return out.value

    def _string(self, el, attr):
        ref = self._copy(el, attr)
        if not ref:
            return ""
        try:
            if self.cf.CFGetTypeID(ref) != self._string_tid:
                return ""
            n = self.cf.CFStringGetLength(ref)
            buf = ctypes.create_string_buffer(max(int(n) * 4 + 1, 1))
            if not self.cf.CFStringGetCString(ref, buf, len(buf), _UTF8):
                return ""
            return buf.value.decode("utf-8", "replace")
        finally:
            self.cf.CFRelease(ref)

    def _elements(self, el, attr):
        self._arm(el)
        arr = self._copy(el, attr)
        if not arr:
            return []
        try:
            if self.cf.CFGetTypeID(arr) != self._array_tid:
                return []
            count = min(int(self.cf.CFArrayGetCount(arr)), 2000)
            out = []
            for i in range(count):
                item = self.cf.CFArrayGetValueAtIndex(arr, i)
                if item:
                    self.cf.CFRetain(item)
                    self._arm(item)
                    out.append(item)
            return out
        finally:
            self.cf.CFRelease(arr)

    def roots(self, app):
        windows = self._elements(app, self.k_windows)
        if windows:
            return windows
        return self._elements(app, self.k_children)

    def children(self, el):
        return self._elements(el, self.k_children)

    def describe(self, el, wanted):
        self._arm(el)
        role = self._string(el, self.k_role)
        if role in SKIP_ROLES:
            return {"role": role}
        desc = self._string(el, self.k_desc)
        title = self._string(el, self.k_title)
        placeholder = self._string(el, self.k_placeholder)
        axid = ""
        if wanted and wanted not in (desc, title, placeholder):
            axid = self._string(el, self.k_ident)
        return {
            "role": role,
            "desc": desc,
            "name": title,
            "title": title,
            "axid": axid,
            "placeholder": placeholder,
        }

    def perform_press(self, el):
        self._arm(el)
        err = self.ax.AXUIElementPerformAction(el, self.k_press)
        if err != 0:
            raise AxError(f"AXPress selhal ({err})")

    def set_value(self, el, text):
        self._arm(el)
        made = self.cf.CFStringCreateWithCString(None, str(text).encode("utf-8"), _UTF8)
        if not made:
            raise AxError("Text nejde předat zpřístupnění")
        try:
            err = self.ax.AXUIElementSetAttributeValue(el, self.k_value, made)
        finally:
            self.cf.CFRelease(made)
        if err != 0:
            raise AxError(f"AXValue selhal ({err})")

    def focus(self, el):
        self._arm(el)
        err = self.ax.AXUIElementSetAttributeValue(el, self.k_focused, self.k_true)
        if err != 0:
            raise AxError(f"AX fokus selhal ({err})")


def type_to_pid(bundle_id, text):
    """Zapíše text přímo procesu. Kurzor ani aktivní okno se nemění."""
    if sys.platform != "darwin":
        raise AxError("Zápis do procesu funguje jen na macOS")
    pid = process_pid(bundle_id)
    cg, cf = _frameworks()
    cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    cg.CGEventKeyboardSetUnicodeString.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16),
    ]
    cg.CGEventPostToPid.argtypes = [ctypes.c_int32, ctypes.c_void_p]
    command = 1 << 20

    def post(event):
        cg.CGEventPostToPid(pid, event)
        cf.CFRelease(event)

    def key(code, down, flags=0):
        event = cg.CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            cg.CGEventSetFlags(event, flags)
        post(event)

    # Vybrat vše a smazat jen v cílovém procesu (ne v okně, které má uživatel).
    key(0x00, True, command)
    key(0x00, False, command)
    key(0x33, True)
    key(0x33, False)
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            raise AxError("Znak mimo BMP nelze zapsat do procesu")
        units = (ctypes.c_uint16 * 1)(code)
        for down in (True, False):
            event = cg.CGEventCreateKeyboardEvent(None, 0, down)
            cg.CGEventKeyboardSetUnicodeString(event, 1, units)
            post(event)


def _frameworks():
    cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    return cg, cf
