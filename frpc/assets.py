import json
import os
import threading
import time
import urllib.request

refresh_seconds = 900.0
request_timeout = 8.0


class AssetCatalog(object):
    def __init__(self, client_id, cache_path, logger=None):
        self.client_id = str(client_id or "")
        self.cache_path = cache_path
        self.log = logger
        self._names = None
        self._fetched = 0.0
        self._thread = None
        self._lock = threading.Lock()
        self._complained = set()
        self._load_cache()

    def _load_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                cached = json.load(handle)
        except (OSError, ValueError):
            return
        if cached.get("client_id") != self.client_id:
            return
        names = cached.get("names")
        if isinstance(names, list):
            self._names = {str(name) for name in names}
            self._fetched = float(cached.get("ts") or 0.0)

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                json.dump({"client_id": self.client_id,
                           "ts": self._fetched,
                           "names": sorted(self._names or [])}, handle,
                          indent=2)
        except OSError:
            pass

    def _fetch(self):
        url = ("https://discord.com/api/v9/oauth2/applications/%s/assets"
               % self.client_id)
        request = urllib.request.Request(
            url, headers={"User-Agent": "FruityRPC (https://github.com/g-rl)"})
        try:
            with urllib.request.urlopen(request,
                                        timeout=request_timeout) as response:
                payload = json.load(response)
        except Exception as error:
            if self.log:
                self.log.debug("could not read the Discord asset list: %s"
                               % error)
            return

        names = {str(item.get("name")) for item in payload
                 if isinstance(item, dict) and item.get("name")}
        with self._lock:
            self._names = names
            self._fetched = time.time()
            self._complained.clear()
        self._save_cache()
        if self.log:
            self.log.info("discord art assets: %s"
                          % (", ".join(sorted(names)) or "none uploaded"))

    def refresh(self, force=False):
        if not self.client_id:
            return
        if not force and time.time() - self._fetched < refresh_seconds:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._fetch,
                                        name="discord-assets", daemon=True)
        self._thread.start()

    @property
    def names(self):
        with self._lock:
            return set(self._names) if self._names is not None else None

    def resolve(self, name, fallback="fl_logo"):
        self.refresh()
        name = (name or "").strip()
        if not name:
            return fallback
        if name.startswith(("http://", "https://")):
            return name

        known = self.names
        if known is None or name in known:
            return name
        if fallback and fallback in known:
            if name not in self._complained:
                self._complained.add(name)
                if self.log:
                    self.log.warning(
                        "icon %r is not an art asset on your Discord "
                        "application, falling back to %s" % (name, fallback))
            return fallback
        return name
