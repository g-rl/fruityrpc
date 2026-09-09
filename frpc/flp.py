import os
import struct
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


def find_project(name):
    if not name:
        return ""
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

    def __init__(self, logger=None):
        self.log = logger
        self._path = ""
        self._stamp = None
        self._facts = {}
        self._missing = set()

    def read(self, project_name):
        if not project_name:
            return {}

        path = self._path
        if not path or os.path.basename(path).lower() != \
                os.path.basename(project_name).strip().lower():
            path = find_project(project_name)
            if not path:
                if project_name not in self._missing:
                    self._missing.add(project_name)
                    if self.log:
                        self.log.debug("no saved file found for %r"
                                       % project_name)
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
