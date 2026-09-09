import json
import os
import time


def _hardware_script_dirs():
    try:
        from .install import hardware_dirs
    except Exception:
        return []
    return [os.path.join(hardware, "FruityRPC")
            for hardware in hardware_dirs()]


class MidiState(object):
    def __init__(self, path, max_age=6.0, logger=None):
        self.path = path
        self.max_age = max_age
        self.log = logger
        self._cache = {}
        self._mtime = 0.0
        self._last_warned = 0.0
        self._was_live = None

    def reconfigure(self, path, max_age):
        if path != self.path:
            self._cache = {}
            self._mtime = 0.0
        self.path = path
        self.max_age = max_age

    def candidates(self):
        folders = [os.path.dirname(self.path)]
        for hardware in _hardware_script_dirs():
            folders.append(hardware)
        for variable in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
            base = os.environ.get(variable)
            if base:
                folders.append(os.path.join(base, "FruityRPC"))
        folders.append(os.path.join(os.path.expanduser("~"), "FruityRPC"))

        seen = set()
        paths = []
        for folder in folders:
            candidate = os.path.join(folder, os.path.basename(self.path))
            key = os.path.normcase(os.path.abspath(candidate))
            if key not in seen:
                seen.add(key)
                paths.append(candidate)
        return paths

    def _freshest(self):
        newest, newest_time = None, 0.0
        for candidate in self.candidates():
            try:
                stamp = os.path.getmtime(candidate)
            except OSError:
                continue
            if stamp > newest_time:
                newest, newest_time = candidate, stamp
        return newest, newest_time

    def read(self):
        path, mtime = self._freshest()
        if not path:
            self._note_live(False)
            return {}
        if path != self.path:
            if self.log:
                self.log.info("reading deep mode state from %s" % path)
            self.path = path

        if mtime != self._mtime:
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    self._cache = data
                    self._mtime = mtime
            except (OSError, ValueError):
                pass

        stamp = self._cache.get("ts")
        age = None
        if isinstance(stamp, (int, float)):
            age = time.time() - stamp
        else:
            age = time.time() - self._mtime

        if self.max_age and age is not None and age > self.max_age:
            self._note_live(False)
            return {}

        self._note_live(bool(self._cache))
        return self._cache

    def _note_live(self, live):
        if live == self._was_live or self.log is None:
            return
        self._was_live = live
        if live:
            self.log.info("deep mode active (FL MIDI script is reporting)")
        else:
            self.log.debug("deep mode inactive, using window titles only")
