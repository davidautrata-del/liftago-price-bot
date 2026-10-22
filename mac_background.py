"""Ovládání Liftaga mimo popředí.

Okno se na dobu měření schová. Tlačítka se mačkají akcí AXPress a text se nastavuje
přes hodnotu prvku, takže běh nebere myš ani fokus. Klávesy do procesu jdou jen
záložně (CGEventPostToPid), nikdy do aktivního okna uživatele.
"""
import ctypes
import logging
import subprocess
import sys

log = logging.getLogger("background")

# Hodnoty z adres a popisků se do skriptu vkládají jen jako %%AX_ARGS%% (escapované literály).
ACTION_SCRIPT = r'''
on run
  set axArgs to {%%AX_ARGS%%}
  set bid to item 1 of axArgs
  set mode to item 2 of axArgs
  set wanted to item 3 of axArgs
  tell application "System Events"
    set proc to first process whose bundle identifier is bid
    if mode is "press" then
      my pressIn(proc, wanted)
      set frontmost of proc to false
    else if mode is "set" then
      my setIn(proc, wanted, item 4 of axArgs)
      set frontmost of proc to false
    else if mode is "focus" then
      my focusIn(proc, wanted)
      set frontmost of proc to false
      if item 4 of axArgs is not "" then
        try
          set frontmost of (first process whose name is (item 4 of axArgs)) to true
        end try
      end if
    else if mode is "pid" then
      return (unix id of proc) as text
    else if mode is "pos" then
      set p to position of window 1 of proc
      return ((item 1 of p) as integer as text) & "," & ((item 2 of p) as integer as text)
    else if mode is "hide" then
      set visible of proc to false
      set frontmost of proc to false
      if item 6 of axArgs is not "" then
        try
          set frontmost of (first process whose name is (item 6 of axArgs)) to true
        end try
      end if
    else if mode is "place" then
      set x to (item 4 of axArgs) as integer
      set y to (item 5 of axArgs) as integer
      set visible of proc to true
      try
        set position of window 1 of proc to {x, y}
      end try
      set frontmost of proc to false
      if item 6 of axArgs is not "" then
        try
          set frontmost of (first process whose name is (item 6 of axArgs)) to true
        end try
      end if
    end if
  end tell
end run

on attrText(el, kind)
  tell application "System Events"
    try
      if kind is "desc" then return description of el as text
      if kind is "name" then return name of el as text
      if kind is "title" then return title of el as text
      if kind is "value" then return value of el as text
      if kind is "role" then return role of el as text
      if kind is "axid" then return (value of attribute "AXIdentifier" of el) as text
    end try
  end tell
  return ""
end attrText

on exactMatch(el, wanted)
  if wanted is "" then return false
  if attrText(el, "desc") is wanted then return true
  if attrText(el, "name") is wanted then return true
  if attrText(el, "title") is wanted then return true
  if attrText(el, "axid") is wanted then return true
  return false
end exactMatch

on prefixMatch(el, wanted)
  if (count wanted) < 12 then return false
  set roleName to attrText(el, "role")
  if roleName is not "AXButton" and roleName is not "AXTextField" and roleName is not "AXTextArea" and roleName is not "AXSearchField" and roleName is not "AXGroup" then
    return false
  end if
  set desc to attrText(el, "desc")
  set nm to attrText(el, "name")
  if (count desc) > 8 then
    if wanted starts with desc then return true
  end if
  if (count nm) > 8 then
    if wanted starts with nm then return true
  end if
  return false
end prefixMatch

on pressIn(proc, wanted)
  tell application "System Events"
    tell proc
      set nodes to entire contents
    end tell
    repeat with el in nodes
      if my exactMatch(el, wanted) then
        set roleName to my attrText(el, "role")
        if roleName is "AXButton" or roleName is "AXCheckBox" or roleName is "AXRadioButton" or roleName is "AXPopUpButton" then
          try
            perform action "AXPress" of el
            return
          end try
        end if
      end if
    end repeat
    repeat with el in nodes
      if my exactMatch(el, wanted) then
        try
          perform action "AXPress" of el
          return
        end try
      end if
    end repeat
    repeat with el in nodes
      if my prefixMatch(el, wanted) then
        try
          perform action "AXPress" of el
          return
        end try
      end if
    end repeat
  end tell
  error "AX element not found"
end pressIn

on setIn(proc, wanted, newValue)
  tell application "System Events"
    tell proc
      set nodes to entire contents
    end tell
    repeat with el in nodes
      if my exactMatch(el, wanted) then
        set roleName to my attrText(el, "role")
        if roleName is "AXTextField" or roleName is "AXTextArea" or roleName is "AXSearchField" or roleName is "AXComboBox" then
          try
            set value of el to newValue
            return
          end try
        end if
      end if
    end repeat
    repeat with el in nodes
      if my exactMatch(el, wanted) then
        try
          set value of el to newValue
          return
        end try
      end if
    end repeat
    repeat with el in nodes
      if my prefixMatch(el, wanted) then
        try
          set value of el to newValue
          return
        end try
      end if
    end repeat
  end tell
  error "AX element not found"
end setIn

on focusIn(proc, wanted)
  tell application "System Events"
    tell proc
      set nodes to entire contents
    end tell
    repeat with el in nodes
      if my exactMatch(el, wanted) or my prefixMatch(el, wanted) then
        try
          set focused of el to true
          return
        end try
      end if
    end repeat
  end tell
  error "AX element not found"
end focusIn
'''


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


def _run(args, timeout=45):
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


def frontmost_name():
    """Jméno aplikace, kterou má uživatel v popředí. None, když to nejde zjistit."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to name of first process whose frontmost is true'],
            text=True, capture_output=True, timeout=10,
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
        raw = _run([bundle_id, "pos", ""])
        x, y = raw.split(",")
        return int(x), int(y)
    except (AxError, OSError, subprocess.TimeoutExpired, ValueError):
        return None


def place(bundle_id, x, y, front_name=None, mouse=None):
    """Vrátí okno na uloženou pozici a fokus uživateli."""
    _run([bundle_id, "place", "", int(x), int(y), front_name or ""])
    restore_mouse(mouse)


def hide_app(bundle_id, front_name=None, mouse=None):
    """Schová okno (ekvivalent Cmd+H) a vrátí fokus uživateli. Myší nehýbe."""
    _run([bundle_id, "hide", "", "", "", front_name or ""])
    restore_mouse(mouse)


def press(bundle_id, wanted):
    log.info("AXPress %s", (wanted or "")[:80])
    _run([bundle_id, "press", wanted])


def set_value(bundle_id, wanted, value):
    log.info("AXValue %s", (wanted or "")[:80])
    _run([bundle_id, "set", wanted, value])


def focus_element(bundle_id, wanted, front_name=None):
    _run([bundle_id, "focus", wanted, front_name or ""])


def process_pid(bundle_id):
    return int(_run([bundle_id, "pid", ""]))


def mouse_position():
    if sys.platform != "darwin":
        return None
    try:
        cg, cf = _frameworks()
        cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        cg.CGEventCreate.restype = ctypes.c_void_p
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = _CGPoint
        event = cg.CGEventCreate(None)
        if not event:
            return None
        try:
            point = cg.CGEventGetLocation(event)
        finally:
            cf.CFRelease(event)
        return (point.x, point.y)
    except Exception:  # noqa: BLE001
        log.debug("Pozici myši nelze přečíst", exc_info=True)
        return None


def restore_mouse(origin):
    """Vrátí kurzor, jen když ho něco mezitím posunulo."""
    if not origin or sys.platform != "darwin":
        return
    now = mouse_position()
    if not now:
        return
    if abs(now[0] - origin[0]) < 3 and abs(now[1] - origin[1]) < 3:
        return
    try:
        cg, _cf = _frameworks()
        cg.CGWarpMouseCursorPosition.argtypes = [_CGPoint]
        cg.CGWarpMouseCursorPosition.restype = ctypes.c_int32
        cg.CGWarpMouseCursorPosition(_CGPoint(origin[0], origin[1]))
        log.info("Kurzor vrácen na původní místo")
    except Exception:  # noqa: BLE001
        log.warning("Kurzor se nepodařilo vrátit")


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


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _frameworks():
    cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    return cg, cf
