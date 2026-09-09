import os
import struct
import threading
import time

event_new_channel = 64
event_new_pattern = 65
event_fine_tempo = 156
event_build_number = 159
event_pattern_name = 193
event_project_title = 194
event_channel_name = 203
event_arrangement_name = 241

max_file_size = 128 * 1024 * 1024


def recent_files_lists():
    paths = []
    userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    roots = [os.path.join(userprofile, "Documents", "Image-Line"),
             os.path.join(userprofile, "OneDrive", "Documents", "Image-Line")]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "Image-Line"))

    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            candidate = os.path.join(root, entry, "Settings", "Browser",
                                     "Recent files.scr")
            if os.path.isfile(candidate):
                paths.append(candidate)
    return paths


def recent_projects():
    found = []
    for listing in recent_files_lists():
        try:
            with open(listing, encoding="utf-8-sig", errors="replace") as file:
                lines = file.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.lower().endswith(".flp") and line not in found:
                found.append(line)
    return found


search_depth = 4
search_dir_budget = 4000
search_time_budget = 3.0
search_retry_seconds = 20.0
skip_folders = {"backup", "__pycache__", "node_modules", "$recycle.bin",
                "system volume information", "windows", "program files",
                "program files (x86)", "appdata", "packages", "temp"}


def _is_drive_root(path):
    trimmed = path.rstrip(os.sep + "/")
    return os.path.dirname(trimmed) == trimmed


def search_roots(extra=()):
    """Folders worth walking for a project file, nearest first.

    Drive roots are deliberately left out: walking one costs seconds, and a
    project always sits somewhere below a folder FL has opened before.
    """
    roots = []

    def add(folder):
        if not folder:
            return
        folder = os.path.abspath(folder)
        if folder in roots or _is_drive_root(folder):
            return
        if os.path.isdir(folder):
            roots.append(folder)

    for path in extra or ():
        add(path)

    for path in recent_projects():
        folder = os.path.dirname(path)
        add(folder)
        add(os.path.dirname(folder))

    userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for documents in (os.path.join(userprofile, "Documents"),
                      os.path.join(userprofile, "OneDrive", "Documents")):
        add(os.path.join(documents, "Image-Line", "FL Studio", "Projects"))
    return roots


def search_disk(name, extra=()):
    """Look for a project FL has saved but not yet listed as recent.

    FL only rewrites 'Recent files.scr' when it exits, so a project saved
    during this session cannot be resolved from it. The walk is bounded by
    both a folder count and a deadline so it can never hold anything up.
    """
    wanted = os.path.basename(name).strip().lower()
    if not wanted:
        return ""
    if not wanted.endswith(".flp"):
        wanted += ".flp"

    deadline = time.time() + search_time_budget
    budget = search_dir_budget
    best = ""
    best_stamp = -1.0

    for root in search_roots(extra):
        base_depth = root.count(os.sep)
        for folder, folders, files in os.walk(root):
            budget -= 1
            if budget <= 0 or time.time() > deadline:
                return best
            if folder.count(os.sep) - base_depth >= search_depth:
                folders[:] = []
            else:
                folders[:] = [item for item in folders
                              if item.lower() not in skip_folders
                              and not item.startswith((".", "$"))]
            for item in files:
                if item.lower() != wanted:
                    continue
                candidate = os.path.join(folder, item)
                try:
                    stamp = os.path.getmtime(candidate)
                except OSError:
                    continue
                if stamp > best_stamp:
                    best_stamp = stamp
                    best = candidate
    return best


def find_recent(name):
    wanted = os.path.basename(name).strip().lower()
    if not wanted:
        return ""
    stem = wanted[:-4] if wanted.endswith(".flp") else wanted
    for path in recent_projects():
        base = os.path.basename(path).lower()
        if base == wanted or base == stem + ".flp":
            if os.path.isfile(path):
                return path
    return ""


def find_project(name, extra=()):
    if not name:
        return ""
    return find_recent(name) or search_disk(name, extra)


def _read_events(data):
    position = 0
    length = len(data)
    while position < length:
        event_id = data[position]
        position += 1
        try:
            if event_id < 64:
                value = data[position]
                position += 1
            elif event_id < 128:
                value = struct.unpack_from("<H", data, position)[0]
                position += 2
            elif event_id < 192:
                value = struct.unpack_from("<I", data, position)[0]
                position += 4
            else:
                size = 0
                shift = 0
                while True:
                    byte = data[position]
                    position += 1
                    size |= (byte & 0x7F) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                value = data[position:position + size]
                position += size
        except (IndexError, struct.error):
            return
        yield event_id, value


def _text(value):
    try:
        return value.decode("utf-16-le").rstrip("\x00").strip()
    except (UnicodeDecodeError, AttributeError):
        try:
            return value.decode("latin-1").rstrip("\x00").strip()
        except Exception:
            return ""


def parse(path):
    try:
        if os.path.getsize(path) > max_file_size:
            return {}
        with open(path, "rb") as file:
            data = file.read()
    except OSError:
        return {}

    if data[:4] != b"FLhd" or len(data) < 16:
        return {}

    try:
        header_size = struct.unpack_from("<I", data, 4)[0]
        _format, channels, ppq = struct.unpack_from("<hHH", data, 8)
    except struct.error:
        return {}

    position = 8 + header_size
    if data[position:position + 4] != b"FLdt":
        return {}
    body_size = struct.unpack_from("<I", data, position + 4)[0]
    position += 8
    body = data[position:position + body_size] if body_size else data[position:]

    channel_count = 0
    highest_pattern = 0
    tempo = None
    build = None
    title = ""
    arrangement = ""
    channel_names = []
    pattern_names = []

    for event_id, value in _read_events(body):
        if event_id == event_new_channel:
            channel_count += 1
        elif event_id == event_new_pattern:
            highest_pattern = max(highest_pattern, int(value))
        elif event_id == event_fine_tempo:
            tempo = value / 1000.0
        elif event_id == event_build_number:
            build = int(value)
        elif event_id == event_project_title:
            title = _text(value)
        elif event_id == event_arrangement_name:
            arrangement = arrangement or _text(value)
        elif event_id == event_channel_name:
            channel_names.append(_text(value))
        elif event_id == event_pattern_name:
            pattern_names.append(_text(value))

    return {
        "source": "flp",
        "ts": time.time(),
        "channel_count": channel_count or channels,
        "pattern_count": max(1, highest_pattern),
        "tempo": tempo,
        "ppq": ppq,
        "fl_build": build,
        "project_title": title,
        "arrangement": arrangement,
        "channel_names": [name for name in channel_names if name],
        "pattern_names": [name for name in pattern_names if name],
    }


class ProjectFacts(object):

    def __init__(self, logger=None, search_paths=()):
        self.log = logger
        self.search_paths = list(search_paths or [])
        self._path = ""
        self._stamp = None
        self._facts = {}
        self._missing = set()
        self._lock = threading.Lock()
        self._thread = None
        self._resolved = {}
        self._attempts = {}

    def _resolve(self, project_name):
        with self._lock:
            known = self._resolved.get(project_name)
            if known and os.path.isfile(known):
                return known
            if self._thread and self._thread.is_alive():
                return ""
            last = self._attempts.get(project_name, 0.0)
            if last and time.time() - last < search_retry_seconds:
                return ""

        quick = find_recent(project_name)
        if quick:
            with self._lock:
                self._resolved[project_name] = quick
            return quick

        def worker():
            found = search_disk(project_name, self.search_paths)
            with self._lock:
                self._attempts[project_name] = time.time()
                if found:
                    self._resolved[project_name] = found
            if not self.log:
                return
            if found:
                self.log.info("found the project on disk: %s" % found)
            elif project_name not in self._missing:
                self._missing.add(project_name)
                self.log.debug("no saved file found for %r" % project_name)

        with self._lock:
            self._thread = threading.Thread(target=worker, name="flp-search",
                                            daemon=True)
            self._thread.start()
        return ""

    def wait(self, timeout=5.0):
        with self._lock:
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)

    def read(self, project_name):
        if not project_name:
            return {}

        path = self._path
        if not path or os.path.basename(path).lower() != \
                os.path.basename(project_name).strip().lower():
            path = self._resolve(project_name)
            if not path:
                self._path = ""
                self._facts = {}
                return {}
            self._path = path
            self._stamp = None
            if self.log:
                self.log.info("reading project facts from %s" % path)

        try:
            stamp = os.path.getmtime(path)
        except OSError:
            self._path = ""
            self._facts = {}
            return {}

        if stamp != self._stamp:
            self._stamp = stamp
            self._facts = parse(path)
            if self.log and self._facts:
                self.log.debug("project facts: %d channels, %d patterns"
                               % (self._facts.get("channel_count", 0),
                                  self._facts.get("pattern_count", 0)))
        facts = dict(self._facts)
        if facts:
            facts["ts"] = time.time()
        return facts
