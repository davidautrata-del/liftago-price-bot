"""Ovládání Liftaga mimo popředí.

Okno se na dobu měření schová. Tlačítka se mačkají přes AXUIElementPerformAction
(AXPress) a text se zapisuje do hodnoty prvku. Hledá se jen ve oknech, ne v
menu baru, a hledání má strop 8 s. Kurzor se nečte ani neposouvá. Klávesy do
procesu jdou jen záložně (CGEventPostToPid), nikdy do aktivního okna uživatele.
"""
import ctypes
import logging
import re
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
      try
        set x to (item 4 of axArgs) as integer
        set y to (item 5 of axArgs) as integer
        set visible of proc to true
        set position of window 1 of proc to {x, y}
      end try
      try
        set frontmost of proc to false
      end try
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
# Názvy atributů. Odpovídající symboly z hlavičky tenhle Mac neexportuje.
AX_ATTRIBUTE_NAMES = {
    "windows": "AXWindows",
    "children": "AXChildren",
    "role": "AXRole",
    "description": "AXDescription",
    "title": "AXTitle",
    "value": "AXValue",
    "identifier": "AXIdentifier",
    "placeholder": "AXPlaceholderValue",
    "focused": "AXFocused",
    "press": "AXPress",
    "position": "AXPosition",
    "size": "AXSize",
}
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


def nth_after(roots, marker, role, nth, children_of, describe, deadline, limit=_NODE_LIMIT,
              release=None, value=""):
    """První (nebo n-té) tlačítko za popiskem, i když samo popisek nemá.

    Potomci značky se nepočítají. Když je value, bere se návrh s tou hodnotou,
    jinak n-tý prvek dané role za značkou.
    """
    def drop(node):
        if release is not None and node is not None:
            release(node)

    if not marker or nth < 1:
        raise AxError("AX element not found")
    stack = [(node, 0) for node in reversed(list(roots or ()))]
    seen = 0
    saw_marker = False
    marker_depth = None
    count = 0
    fallback = None
    try:
        while stack:
            if time.monotonic() >= deadline:
                raise AxError("AX hledání překročilo limit času")
            node, depth = stack.pop()
            seen += 1
            if seen > limit:
                drop(node)
                raise AxError("AX hledání překročilo limit prvků")
            keep = False
            try:
                if marker_depth is not None and depth <= marker_depth:
                    marker_depth = None
                info = describe(node) or {}
                role_name = info.get("role") or ""
                if role_name in SKIP_ROLES:
                    continue
                if not saw_marker and classify(info, marker) == "exact":
                    saw_marker = True
                    marker_depth = depth
                elif saw_marker and marker_depth is None and role_name == role:
                    texts = [info.get(key) or "" for key in ("desc", "name", "title", "value", "placeholder", "axid")]
                    for text in texts:
                        _refuse_order(text)
                    if value and value in texts:
                        keep = True
                        drop(fallback)
                        fallback = None
                        for leftover, _depth in stack:
                            drop(leftover)
                        stack.clear()
                        return node
                    count += 1
                    if not value and count == nth:
                        keep = True
                        for leftover, _depth in stack:
                            drop(leftover)
                        stack.clear()
                        return node
                    if value and count == nth and fallback is None:
                        fallback = node
                        keep = True
                stack.extend((child, depth + 1) for child in reversed(list(children_of(node) or ())))
            finally:
                if not keep:
                    drop(node)
        if fallback is not None:
            found = fallback
            fallback = None
            return found
        raise AxError("AX element not found")
    finally:
        drop(fallback)
        for leftover, _depth in stack:
            drop(leftover)


_SUGGESTION_GENERIC = frozenset({
    "praha", "cesko", "czechia", "czech", "ulice", "trida", "namesti", "okres",
})


def _fold_text(text):
    import unicodedata

    raw = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).lower()


def suggestion_tokens(text):
    words = re.findall(r"[a-z0-9]+", _fold_text(text))
    return [
        word for word in words
        if not word.isdigit() and len(word) >= 3 and word not in _SUGGESTION_GENERIC
    ]


def suggestion_score(query, candidate):
    """Jak moc návrh odpovídá zadané adrese. 0 znamená, že to není ten návrh."""
    wanted = suggestion_tokens(query)
    folded = _fold_text(candidate)
    if not wanted or not folded.strip():
        return 0
    if not re.search(rf"(?<![a-z0-9]){re.escape(wanted[0])}(?![a-z0-9])", folded):
        return 0
    hits = 0
    for token in wanted:
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", folded):
            hits += 1
    for number in re.findall(r"\d+", _fold_text(query)):
        if re.search(rf"(?<!\d){number}(?!\d)", folded):
            hits += 1
            break
    return hits


def pick_suggestion(query, candidates):
    """Popisek návrhu, který odpovídá adrese. Prázdné první tlačítko se nebere."""
    best = None
    best_score = 0
    for candidate in candidates or []:
        text = (candidate or "").strip()
        if not text or text == "Objednat" or text.startswith("Objednat "):
            continue
        score = suggestion_score(query, text)
        if score > best_score:
            best = text
            best_score = score
    return best


def best_after(roots, marker, query, children_of, describe, deadline, limit=_NODE_LIMIT,
               release=None, role="AXButton"):
    """Tlačítko za značkou, jehož text odpovídá adrese. První prázdné se nebere."""
    def drop(node):
        if release is not None and node is not None:
            release(node)

    if not marker or not (query or "").strip():
        raise AxError("AX element not found")
    stack = [(node, 0) for node in reversed(list(roots or ()))]
    seen = 0
    saw_marker = False
    marker_depth = None
    best = None
    best_score = 0
    try:
        while stack:
            if time.monotonic() >= deadline:
                raise AxError("AX hledání překročilo limit času")
            node, depth = stack.pop()
            seen += 1
            if seen > limit:
                drop(node)
                raise AxError("AX hledání překročilo limit prvků")
            keep = False
            try:
                if marker_depth is not None and depth <= marker_depth:
                    marker_depth = None
                info = describe(node) or {}
                role_name = info.get("role") or ""
                if role_name in SKIP_ROLES:
                    continue
                if not saw_marker and classify(info, marker) == "exact":
                    saw_marker = True
                    marker_depth = depth
                elif saw_marker and marker_depth is None and role_name == role:
                    texts = _field_texts(info)
                    if any(text == "Objednat" or text.startswith("Objednat ") for text in texts):
                        pass
                    else:
                        score = suggestion_score(query, " ".join(texts))
                        if score > best_score:
                            drop(best)
                            best = node
                            best_score = score
                            keep = True
                stack.extend((child, depth + 1) for child in reversed(list(children_of(node) or ())))
            finally:
                if not keep:
                    drop(node)
        if best is None:
            raise AxError("AX element not found")
        found = best
        best = None
        return found
    finally:
        drop(best)
        for leftover, _depth in stack:
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


def _field_texts(info):
    texts = []
    for key in ("desc", "name", "title", "value"):
        text = (info.get(key) or "").replace("\u00a0", " ").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _hit_rank(role):
    if role == "AXButton":
        return 0
    if role in ("AXRadioButton", "AXCheckBox"):
        return 1
    if role == "AXStaticText":
        return 2
    return 3


def category_hit(roots, title, context, children_of, describe, deadline, limit=_NODE_LIMIT,
                 release=None):
    """Prvek, který kategorii vybere. Ne sloučený popisek celého řádku.

    Sloučený popisek („Odvoz vozu Řidič… Odhad ceny…“) AXPress přijme a nic
    nevybere. Vybere prvek, jehož popisek je přesně `title` a ve stejném řádku
    je `context`. Tlačítko s tím jménem má přednost před textem.
    """
    title = (title or "").strip()
    context = (context or "").strip()
    if not title or not context:
        raise AxError("AX element not found")
    state = {"seen": 0, "best": None, "rank": 99}

    def drop(node):
        if release is not None and node is not None:
            release(node)

    def row_has_context(node, parent):
        extra = []
        if parent is not None:
            extra.extend(list(children_of(parent) or ()))
        extra.extend(list(children_of(node) or ()))
        try:
            parts = []
            for item in extra:
                parts.extend(_field_texts(describe(item) or {}))
            return context in " ".join(parts)
        finally:
            for item in extra:
                if item is not node and item is not parent and item is not state["best"]:
                    drop(item)

    def visit(node, parent):
        if time.monotonic() >= deadline:
            drop(node)
            raise AxError("AX hledání překročilo limit času")
        state["seen"] += 1
        if state["seen"] > limit:
            drop(node)
            raise AxError("AX hledání překročilo limit prvků")
        keep = False
        try:
            info = describe(node) or {}
            role = info.get("role") or ""
            if role in SKIP_ROLES:
                return
            texts = _field_texts(info)
            if title in texts and row_has_context(node, parent):
                rank = _hit_rank(role)
                if rank < state["rank"]:
                    previous = state["best"]
                    state["best"] = node
                    state["rank"] = rank
                    keep = True
                    if previous is not None:
                        drop(previous)
            kids = list(children_of(node) or ())
            seen_kids = 0
            try:
                for kid in kids:
                    seen_kids += 1
                    visit(kid, node)
            except Exception:
                for kid in kids[seen_kids:]:
                    if kid is not state["best"]:
                        drop(kid)
                raise
        finally:
            if not keep and node is not state["best"]:
                drop(node)

    try:
        for root in list(roots or ()):
            visit(root, None)
        found = state["best"]
        state["best"] = None
        if found is None:
            raise AxError("AX element not found")
        return found
    finally:
        if state["best"] is not None:
            drop(state["best"])


def row_containing(roots, title, context, children_of, describe, deadline, limit=_NODE_LIMIT,
                   release=None):
    """Řádek, jehož vlastní popisek obsahuje název i popis. Samotný název to není."""
    title = (title or "").strip()
    context = (context or "").strip()
    state = {"seen": 0, "best": None}

    def drop(node):
        if release is not None and node is not None:
            release(node)

    def visit(node):
        if time.monotonic() >= deadline:
            drop(node)
            raise AxError("AX hledání překročilo limit času")
        state["seen"] += 1
        if state["seen"] > limit:
            drop(node)
            raise AxError("AX hledání překročilo limit prvků")
        keep = False
        try:
            info = describe(node) or {}
            if (info.get("role") or "") in SKIP_ROLES:
                return
            blob = " ".join(_field_texts(info))
            if title in blob and context in blob and blob != title and state["best"] is None:
                state["best"] = node
                keep = True
            kids = list(children_of(node) or ())
            seen_kids = 0
            try:
                for kid in kids:
                    seen_kids += 1
                    if state["best"] is None:
                        visit(kid)
                    else:
                        drop(kid)
            except Exception:
                for kid in kids[seen_kids:]:
                    if kid is not state["best"]:
                        drop(kid)
                raise
        finally:
            if not keep and node is not state["best"]:
                drop(node)

    try:
        for root in list(roots or ()):
            if state["best"] is None:
                visit(root)
            else:
                drop(root)
        found = state["best"]
        state["best"] = None
        if found is None:
            raise AxError("AX element not found")
        return found
    finally:
        if state["best"] is not None:
            drop(state["best"])


def title_click_point(left, top, width, height, on_title):
    """Bod zásahu. U celého řádku je to název vlevo nahoře, ne střed řádku."""
    if width < 2 or height < 2:
        raise AxError("Prvek kategorie nemá plochu")
    if on_title:
        return left + width / 2.0, top + height / 2.0
    x = min(left + min(96.0, max(width * 0.08, 8.0)), left + width - 1)
    y = min(top + min(24.0, max(height * 0.35, 4.0)), top + height - 1)
    return x, y


def press(bundle_id, wanted):
    log.info("AXPress %s", (wanted or "")[:80])
    _refuse_order(wanted)
    _ax_action(bundle_id, wanted, "press")


def activate_in_row(bundle_id, title, context):
    """Jednou vybere kategorii. Tlačítko s přesným jménem dostane AXPress.

    Text uvnitř řádku (ten, který AXPress na dlouhém popisku nevybere) dostane
    jeden klik jen do procesu Liftaga. Kurzor se neposouvá a okno se neaktivuje.
    """
    _refuse_order(title)
    _refuse_order(context)
    if sys.platform != "darwin":
        raise AxError("Zpřístupnění funguje jen na macOS")
    pid = process_pid(bundle_id)
    ax = _Ax()
    app = None
    node = None
    try:
        app = ax.application(pid)
        deadline = time.monotonic() + _SEARCH_LIMIT

        def describe(el):
            return ax.describe(el, title)

        on_title = True
        try:
            node = category_hit(
                ax.roots(app), title, context, ax.children, describe,
                deadline, release=ax.release,
            )
        except AxError as err:
            if "not found" not in str(err):
                raise
            on_title = False
            node = row_containing(
                ax.roots(app), title, context, ax.children, describe,
                time.monotonic() + _SEARCH_LIMIT, release=ax.release,
            )
        info = ax.describe(node, title) or {}
        if on_title and (info.get("role") or "") == "AXButton":
            log.info("AXPress %s", (title or "")[:80])
            ax.perform_press(node)
            return
        log.info("AXClick %s", (title or "")[:80])
        left, top, width, height = ax.frame(node)
        click_x, click_y = title_click_point(left, top, width, height, on_title)
        _click_pid(pid, click_x, click_y)
    finally:
        ax.release(node)
        ax.release(app)
        ax.close()


def press_best_after(bundle_id, marker, query, role="AXButton"):
    """Stiskne návrh za značkou, který odpovídá zadané adrese. Ne první prázdné tlačítko."""
    _refuse_order(marker)
    _refuse_order(query)
    if sys.platform != "darwin":
        raise AxError("Zpřístupnění funguje jen na macOS")
    pid = process_pid(bundle_id)
    ax = _Ax()
    app = None
    node = None
    try:
        app = ax.application(pid)
        deadline = time.monotonic() + _SEARCH_LIMIT
        node = best_after(
            ax.roots(app), marker, query, ax.children,
            ax.describe_with_value, deadline, release=ax.release, role=role,
        )
        info = ax.describe(node, query) or {}
        label = " ".join(_field_texts(info)) or query
        log.info("AXPress %s", label[:80])
        ax.perform_press(node)
    finally:
        ax.release(node)
        ax.release(app)
        ax.close()


def press_following(bundle_id, marker, role="AXButton", nth=1, value=""):
    """Stiskne n-tý prvek dané role za značkou. Popisek toho prvku není potřeba."""
    log.info("AXPress po %s #%s", (marker or "")[:40], nth)
    _refuse_order(marker)
    _refuse_order(value)
    _ax_action(bundle_id, marker, "after", value=value, role=role, nth=nth)


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


def _ax_action(bundle_id, wanted, mode, value="", role="AXButton", nth=1):
    if sys.platform != "darwin":
        raise AxError("Zpřístupnění funguje jen na macOS")
    if not wanted:
        raise AxError("AX element not found")
    pid = process_pid(bundle_id)
    ax = _Ax()
    app = None
    try:
        app = ax.application(pid)
        accept = None
        if mode == "press":
            accept = lambda info: (info.get("role") or "") in PRESS_ROLES
        elif mode == "set":
            accept = lambda info: (info.get("role") or "") in VALUE_ROLES
        deadline = time.monotonic() + _SEARCH_LIMIT
        if mode == "after":
            node = nth_after(
                ax.roots(app),
                wanted,
                role,
                nth,
                ax.children,
                ax.describe_with_value,
                deadline,
                release=ax.release,
                value=value,
            )
        else:
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
            if mode == "press" or mode == "after":
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
        ax.close()


class _Ax:
    """Omezené čtení stromu oken. Na prvek je strop 0,25 s, ať System Events nezamrzne."""

    def __init__(self):
        self.cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/"
            "HIServices.framework/HIServices")
        self._owned = []
        self._bind()
        try:
            self.k_windows = self._name("windows")
            self.k_children = self._name("children")
            self.k_role = self._name("role")
            self.k_desc = self._name("description")
            self.k_title = self._name("title")
            self.k_value = self._name("value")
            self.k_ident = self._name("identifier")
            self.k_placeholder = self._name("placeholder")
            self.k_focused = self._name("focused")
            self.k_press = self._name("press")
            self.k_position = self._name("position")
            self.k_size = self._name("size")
            self.k_true = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
            self._string_tid = self.cf.CFStringGetTypeID()
            self._array_tid = self.cf.CFArrayGetTypeID()
        except Exception:
            self.close()
            raise

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
        ax.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
        ax.AXValueGetValue.restype = ctypes.c_bool
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

    def _name(self, key):
        text = AX_ATTRIBUTE_NAMES[key]
        ref = self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)
        if not ref:
            raise AxError(f"Nelze vytvořit atribut {text}")
        self._owned.append(ref)
        return ref

    def close(self):
        for ref in self._owned:
            self.cf.CFRelease(ref)
        self._owned.clear()

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

    def describe_with_value(self, el):
        info = self.describe(el, "")
        if (info.get("role") or "") == "AXButton":
            info["value"] = self._string(el, self.k_value)
        return info

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

    def frame(self, el):
        """Levý horní roh a rozměr v souřadnicích obrazovky. Typ bodu je 1, rozměr 2."""
        self._arm(el)
        origin = self._ax_value(el, self.k_position, 1)
        size = self._ax_value(el, self.k_size, 2)
        if origin is None or size is None:
            raise AxError("Prvek kategorie nemá polohu")
        return origin[0], origin[1], size[0], size[1]

    def _ax_value(self, el, attr, kind):
        ref = self._copy(el, attr)
        if not ref:
            return None
        try:
            buf = CGPoint() if kind == 1 else CGSize()
            if not self.ax.AXValueGetValue(ref, kind, ctypes.byref(buf)):
                return None
            if kind == 1:
                return (float(buf.x), float(buf.y))
            return (float(buf.width), float(buf.height))
        finally:
            self.cf.CFRelease(ref)

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


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


def _click_pid(pid, x, y):
    """Jeden levý klik jen do procesu. Soukromý zdroj kurzor neposune."""
    cg, cf = _frameworks()
    cg.CGEventSourceCreate.argtypes = [ctypes.c_int32]
    cg.CGEventSourceCreate.restype = ctypes.c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventPostToPid.argtypes = [ctypes.c_int32, ctypes.c_void_p]
    source = cg.CGEventSourceCreate(-1)
    down = up = None
    try:
        point = CGPoint(float(x), float(y))
        down = cg.CGEventCreateMouseEvent(source, 1, point, 0)
        up = cg.CGEventCreateMouseEvent(source, 2, point, 0)
        if not down or not up:
            raise AxError("Klik na kategorii nejde sestavit")
        cg.CGEventPostToPid(int(pid), down)
        time.sleep(0.05)
        cg.CGEventPostToPid(int(pid), up)
    finally:
        if down:
            cf.CFRelease(down)
        if up:
            cf.CFRelease(up)
        if source:
            cf.CFRelease(source)


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
