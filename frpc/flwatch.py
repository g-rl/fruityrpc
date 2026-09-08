"""FL Studio detection and introspection.

Everything here is done with ctypes against the Win32 API, so there are no
third-party dependencies and nothing has to be injected into FL Studio.

The watcher is deliberately version-agnostic. A running FL Studio is found by
matching *either* the executable name (``FL64.exe``, ``FL.exe``, ``FL32.exe``,
renamed or portable builds via config) *or* the window title, which has
contained the words "FL Studio" (and "Fruity Loops" before that) in every
release. Project name, version and the focused sub-window are then parsed out
of window titles, which works identically from FL Studio 9 to 2024+.
"""

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes

is_windows = sys.platform == "win32"

if is_windows:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        version_dll = ctypes.WinDLL("version", use_last_error=True)
    except OSError:
        version_dll = None
else:
    user32 = kernel32 = version_dll = None


ga_parent = 1
ga_root = 2
gw_owner = 4
process_query_limited_information = 0x1000
process_query_information = 0x0400


class gui_thread_info(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class last_input_info(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


if is_windows:
    wnd_enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)
    user32.EnumWindows.argtypes = [wnd_enum_proc, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, wnd_enum_proc,
                                        wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD,
                                        ctypes.POINTER(gui_thread_info)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _window_class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def clean_caption(text):
    """Strip FL's private-use marker glyphs out of a window caption.

    FL Studio embeds characters from the Unicode Private Use Area in its
    captions and hints - they select icons in its own font, and everywhere
    else (Discord included) they render as an empty box. "Fruity Parametric
    EQ 2" should reach the presence as "Fruity Parametric EQ 2".
    """
    if not text:
        return ""
    cleaned = []
    for character in text:
        code = ord(character)
        if 0xE000 <= code <= 0xF8FF:
            continue
        if 0xF0000 <= code <= 0x10FFFF:
            continue
        if code < 0x20 and character not in "\t":
            continue
        cleaned.append(character)
    return " ".join("".join(cleaned).split())


def _window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return clean_caption(buffer.value)


def _process_path(pid):
    for access in (process_query_limited_information,
                   process_query_information):
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer,
                                                   ctypes.byref(size)):
                return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    return ""


def _is_wow64(pid):
    """True when a 32-bit process runs on 64-bit Windows."""
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        flag = wintypes.BOOL()
        if kernel32.IsWow64Process(handle, ctypes.byref(flag)):
            return bool(flag.value)
    except AttributeError:
        return None
    finally:
        kernel32.CloseHandle(handle)
    return None


def _file_version(path):
    """Return the ``a.b.c.d`` version string of an exe, or ''."""
    if not path or version_dll is None:
        return ""
    try:
        size = version_dll.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size,
                                               buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version_dll.VerQueryValueW(buffer, ctypes.c_wchar_p("\\"),
                                          ctypes.byref(pointer),
                                          ctypes.byref(length)):
            return ""
        data = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
        most, least = data[2], data[3]
        return "%d.%d.%d.%d" % (most >> 16, most & 0xFFFF,
                                least >> 16, least & 0xFFFF)
    except Exception:
        return ""


def idle_seconds():
    """Seconds since the last keyboard or mouse input, system wide."""
    if not is_windows:
        return 0.0
    info = last_input_info()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ticks = kernel32.GetTickCount()
    delta = (ticks - info.dwTime) & 0xFFFFFFFF
    return delta / 1000.0


version_pattern = re.compile(
    r"(?:FL\s*Studio|Fruity\s*?Loops)\s*"
    r"(?:Producer\s+Edition\s*|Signature\s*|Fruity\s*|Express\s*|"
    r"All\s+Plugins\s+Edition\s*|Mobile\s*)?"
    r"(\d{1,4}(?:\.\d+)*)?",
    re.IGNORECASE)

app_token_pattern = re.compile(r"(?:FL\s*Studio|Fruity\s*?Loops)",
                               re.IGNORECASE)
bitness_pattern = re.compile(r"\(?\b(32|64)[\s-]*bits?\b\)?", re.IGNORECASE)
bracket_pattern = re.compile(r"^\[(.+)\]$")


def parse_title(title, config):
    """Split an FL Studio main-window title into its parts.

    Returns ``(project, unsaved, version)``. ``project`` is ``''`` when no
    project is open. Handles the layouts FL has shipped over the years::

        FL Studio 21 - my song
        my song - FL Studio 20
        FL Studio 12 (64 bit) - my song.flp
        FL Studio 21.2 [my song *]
        Fruity Loops 3 - my song
    """
    formatting = config.get("formatting", {})
    detection = config.get("detection", {})

    version = ""
    match = version_pattern.search(title or "")
    if match and match.group(1):
        version = match.group(1)

    override = detection.get("project_title_regex") or ""
    if override:
        try:
            found = re.search(override, title or "", re.IGNORECASE)
            project = (found.group(1) if found and found.groups() else "")
        except re.error:
            project = ""
    else:
        project = _extract_project(title or "")

    project = project.strip()
    unsaved = False
    if project.endswith("*") or project.startswith("*"):
        unsaved = True
        project = project.strip("*").strip()

    bracketed = bracket_pattern.match(project)
    if bracketed:
        project = bracketed.group(1).strip()
        if project.endswith("*"):
            unsaved = True
            project = project[:-1].strip()

    if formatting.get("strip_flp_extension", True):
        if project.lower().endswith(".flp"):
            project = project[:-4].strip()

    untitled = [name.lower() for name in
                detection.get("untitled_names", ["untitled", ""])]
    if project.lower() in untitled:
        project = ""

    return project, unsaved, version


def _extract_project(title):
    """Remove the application part of a title, leaving the project."""
    cleaned = bitness_pattern.sub("", title).strip()

    segments = [part.strip() for part in re.split(r"\s+[-–]\s+", cleaned)]
    if len(segments) > 1:
        remainder = [part for part in segments
                     if part and not app_token_pattern.search(part)]
        if remainder:
            return " - ".join(remainder)
        return ""

    stripped = app_token_pattern.sub("", cleaned, count=1).strip()
    stripped = re.sub(r"^(?:Producer\s+Edition|Signature|Fruity|Express|"
                      r"All\s+Plugins\s+Edition|Mobile)\b", "", stripped,
                      flags=re.IGNORECASE).strip()
    stripped = re.sub(r"^\d{1,4}(?:\.\d+)*", "", stripped).strip()
    stripped = stripped.lstrip(":-–").strip()
    return stripped


class Snapshot(dict):
    """Plain dict with attribute access, for readability at call sites."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


empty_snapshot = Snapshot(
    running=False, pid=0, exe="", exe_path="", main_title="", project="",
    unsaved=False, version="", version_short="", version_full="", bitness="",
    windows=[],
    window_details=[], focused_window="", focused_class="", exporting=False,
    foreground=False, idle_seconds=0.0,
)


class FLWatcher(object):
    """Scans the desktop for FL Studio and reports what it is doing."""

    def __init__(self, config, logger=None):
        self.log = logger
        self.reconfigure(config)
        self._path_cache = {}
        self._last_pid = 0
        self._last_focus = None

    def reconfigure(self, config):
        self.config = config
        detection = config.get("detection", {})
        try:
            self._process_re = re.compile(
                detection.get("process_regex", r"^fl(?:64|32)?.*\.exe$"),
                re.IGNORECASE)
        except re.error:
            self._process_re = re.compile(r"^fl(?:64|32)?.*\.exe$",
                                          re.IGNORECASE)
        try:
            self._title_re = re.compile(
                detection.get("window_title_regex", r"FL\s*Studio"),
                re.IGNORECASE)
        except re.error:
            self._title_re = re.compile(r"FL\s*Studio", re.IGNORECASE)
        try:
            self._class_re = re.compile(
                detection.get("main_window_class_regex",
                              r"^TFruityLoopsMainForm$"), re.IGNORECASE)
        except re.error:
            self._class_re = re.compile(r"^TFruityLoopsMainForm$",
                                        re.IGNORECASE)
        try:
            self._ignore_class_re = re.compile(
                detection.get("ignore_window_class_regex",
                              r"Panel$|Splitter|Toolbar|Caption$"),
                re.IGNORECASE)
        except re.error:
            self._ignore_class_re = re.compile(r"Panel$|Splitter|Toolbar",
                                               re.IGNORECASE)
        try:
            self._export_re = re.compile(
                detection.get("export_title_regex", r"render|export"),
                re.IGNORECASE)
        except re.error:
            self._export_re = re.compile(r"render|export", re.IGNORECASE)
        self._extra_names = {name.lower() for name in
                             detection.get("extra_process_names", [])}


    def _exe_for_pid(self, pid):
        cached = self._path_cache.get(pid)
        if cached is not None:
            return cached
        path = _process_path(pid)
        if len(self._path_cache) > 256:
            self._path_cache.clear()
        self._path_cache[pid] = path
        return path

    def _looks_like_fl(self, hwnd, title, window_class):
        """Decide whether ``hwnd`` is an FL Studio main window.

        Three independent signals, any of which is enough on its own:

        * the window class - ``TFruityLoopsMainForm`` has been FL's main form
          class since the Fruity Loops days, and it does not change with the
          interface language;
        * the executable name;
        * the window title.
        """
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        by_class = bool(self._class_re.match(window_class or ""))
        by_title = bool(self._title_re.search(title))
        if not (by_class or by_title):
            return None
        path = self._exe_for_pid(pid.value)
        name = os.path.basename(path).lower()
        by_process = bool(name) and (self._process_re.match(name)
                                     or name in self._extra_names)
        if by_class or by_title or by_process:
            return pid.value, path, by_class
        return None

    def _collect_windows(self, pid):
        """Every visible captioned top-level window belonging to ``pid``."""
        results = []

        @wnd_enum_proc
        def top_level(hwnd, _lparam):
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid and user32.IsWindowVisible(hwnd):
                title = _window_text(hwnd)
                if title:
                    results.append((hwnd, title, _window_class(hwnd)))
            return True

        user32.EnumWindows(top_level, 0)
        return results

    def _child_windows(self, hwnd):
        """Visible captioned children: FL's Playlist, Mixer, Piano roll, ..."""
        results = []

        @wnd_enum_proc
        def child(handle, _lparam):
            if user32.IsWindowVisible(handle):
                title = _window_text(handle)
                if title:
                    results.append((handle, title, _window_class(handle)))
            return True

        user32.EnumChildWindows(hwnd, child, 0)
        return results

    def _focused_window(self, hwnd_main):
        """``(caption, class)`` of the FL sub-window that has focus."""
        thread_id = user32.GetWindowThreadProcessId(hwnd_main, None)
        info = gui_thread_info()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return "", ""
        for candidate in (info.hwndFocus, info.hwndCapture, info.hwndActive):
            if not candidate:
                continue
            hwnd = candidate
            depth = 0
            while hwnd and depth < 24:
                title = _window_text(hwnd)
                window_class = _window_class(hwnd)
                if (hwnd != hwnd_main and title and len(title) > 1
                        and not self._ignore_class_re.search(window_class)):
                    return title, window_class
                hwnd = user32.GetAncestor(hwnd, ga_parent)
                depth += 1
        return "", ""


    def poll(self):
        """Return a :class:`Snapshot` of FL Studio right now."""
        if not is_windows:
            return Snapshot(empty_snapshot, idle_seconds=0.0)

        found = []

        @wnd_enum_proc
        def scan(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, gw_owner):
                return True
            title = _window_text(hwnd)
            if not title:
                return True
            window_class = _window_class(hwnd)
            match = self._looks_like_fl(hwnd, title, window_class)
            if match:
                found.append((hwnd, title, match[0], match[1], match[2]))
            return True

        user32.EnumWindows(scan, 0)

        if not found:
            snapshot = Snapshot(empty_snapshot)
            snapshot["idle_seconds"] = idle_seconds()
            snapshot["windows"] = []
            snapshot["window_details"] = []
            return snapshot

        found.sort(key=lambda item: (item[4],
                                     bool(self._title_re.search(item[1])),
                                     len(item[1])), reverse=True)
        hwnd_main, main_title, pid, exe_path, _by_class = found[0]

        sub_windows = []
        details = []
        seen = set()
        for _handle, title, window_class in self._child_windows(hwnd_main):
            if title == main_title or title in seen:
                continue
            if self._ignore_class_re.search(window_class):
                continue
            seen.add(title)
            sub_windows.append(title)
            details.append({"title": title, "class": window_class})
        for handle, title, window_class in self._collect_windows(pid):
            if handle == hwnd_main or title == main_title or title in seen:
                continue
            seen.add(title)
            sub_windows.append(title)
            details.append({"title": title, "class": window_class})

        focused, focused_class = self._focused_window(hwnd_main)
        if focused:
            self._last_focus = (focused, focused_class)
        elif (self.config.get("detection", {}).get("sticky_focus", True)
              and self._last_focus):
            focused, focused_class = self._last_focus
        project, unsaved, version = parse_title(main_title, self.config)

        version_full = _file_version(exe_path)
        if not version and version_full:
            version = version_full.split(".")[0]

        version_short = ".".join(version_full.split(".")[:3]) if version_full \
            else version

        bitness = ""
        name = os.path.basename(exe_path).lower()
        if "64" in name:
            bitness = "64-bit"
        elif "32" in name:
            bitness = "32-bit"
        else:
            wow64 = _is_wow64(pid)
            if wow64 is True:
                bitness = "32-bit"
            elif wow64 is False:
                bitness = "64-bit"

        exporting = bool(self._export_re.search(main_title)) or any(
            self._export_re.search(title) for title in sub_windows)
        if not project and focused_class == "TWelcomeWizard":
            focused = ""

        foreground_hwnd = user32.GetForegroundWindow()
        foreground_pid = wintypes.DWORD()
        if foreground_hwnd:
            user32.GetWindowThreadProcessId(foreground_hwnd,
                                            ctypes.byref(foreground_pid))

        if pid != self._last_pid and self.log:
            self.log.info("found FL Studio: %s (pid %d) - %r"
                          % (os.path.basename(exe_path) or "unknown", pid,
                             main_title))
            self._last_pid = pid

        return Snapshot(
            running=True,
            pid=pid,
            exe=os.path.basename(exe_path),
            exe_path=exe_path,
            main_title=main_title,
            project=project,
            unsaved=unsaved,
            version=version,
            version_short=version_short,
            version_full=version_full,
            bitness=bitness,
            windows=sub_windows,
            window_details=details,
            focused_window=focused,
            focused_class=focused_class,
            exporting=exporting,
            foreground=foreground_pid.value == pid,
            idle_seconds=idle_seconds(),
        )
