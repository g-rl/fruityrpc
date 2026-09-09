import ctypes
import json
import os
import struct
import sys
import tempfile
import time
import uuid

op_handshake = 0
op_frame = 1
op_close = 2
op_ping = 3
op_pong = 4

is_windows = sys.platform == "win32"

if is_windows:
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD)]
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL


class IPCError(Exception):
    pass


def _candidate_paths():
    if is_windows:
        for index in range(10):
            yield r"\\.\pipe\discord-ipc-%d" % index
        return

    base_dirs = []
    for name in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(name)
        if value:
            base_dirs.append(value)
    base_dirs.append(tempfile.gettempdir())
    extra = ("", "app/com.discordapp.Discord", "snap.discord",
             "app/com.discordapp.DiscordCanary")
    seen = set()
    for base in base_dirs:
        for sub in extra:
            directory = os.path.join(base, sub) if sub else base
            for index in range(10):
                path = os.path.join(directory, "discord-ipc-%d" % index)
                if path not in seen:
                    seen.add(path)
                    yield path


class DiscordIPC(object):

    def __init__(self, client_id, logger=None):
        self.client_id = str(client_id)
        self.log = logger
        self._handle = None
        self._socket = None
        self._alive = False
        self.user = None


    @property
    def connected(self):
        return self._alive

    def _debug(self, message):
        if self.log:
            self.log.debug(message)

    def _open(self, path):
        if is_windows:
            self._handle = open(path, "r+b", 0)
        else:
            import socket
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(path)
            self._socket = sock
            self._handle = sock.makefile("rwb", 0)

    def _available(self):
        handle = self._handle
        if handle is None:
            return 0
        if is_windows:
            try:
                raw = msvcrt.get_osfhandle(handle.fileno())
            except (OSError, ValueError):
                raise IPCError("connection closed")
            total = wintypes.DWORD(0)
            ok = _kernel32.PeekNamedPipe(raw, None, 0, None,
                                         ctypes.byref(total), None)
            if not ok:
                raise IPCError("connection closed by Discord")
            return total.value
        import select
        ready, _w, _e = select.select([self._socket], [], [], 0)
        return 1 if ready else 0

    def _write(self, opcode, payload):
        data = json.dumps(payload).encode("utf-8")
        frame = struct.pack("<II", opcode, len(data)) + data
        handle = self._handle
        if handle is None:
            raise IPCError("not connected")
        try:
            handle.write(frame)
            handle.flush()
        except Exception as exc:
            raise IPCError("write failed: %s" % exc)

    def _wait_for(self, count, timeout):
        deadline = time.time() + timeout
        while True:
            if self._available() >= count:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.02)

    def _read_exact(self, count, timeout=5.0):
        if not self._wait_for(1, timeout):
            raise IPCError("timed out waiting for Discord")
        chunks = []
        remaining = count
        deadline = time.time() + timeout
        while remaining > 0:
            try:
                chunk = self._handle.read(remaining)
            except Exception as exc:
                raise IPCError("read failed: %s" % exc)
            if not chunk:
                if time.time() >= deadline:
                    raise IPCError("connection closed by Discord")
                time.sleep(0.02)
                continue
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_frame(self, timeout=5.0):
        header = self._read_exact(8, timeout)
        opcode, length = struct.unpack("<II", header)
        body = self._read_exact(length, timeout) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            payload = {}
        return opcode, payload


    def connect(self):
        if not self.client_id:
            raise IPCError("no client_id configured")
        last_error = None
        for path in _candidate_paths():
            try:
                self._open(path)
            except Exception as exc:
                last_error = exc
                continue
            try:
                self._write(op_handshake,
                            {"v": 1, "client_id": self.client_id})
                opcode, payload = self._read_frame(timeout=8.0)
                if opcode == op_close:
                    raise IPCError("Discord refused the handshake: %s"
                                   % payload.get("message", payload))
                self.user = (payload.get("data") or {}).get("user")
                self._alive = True
                self._debug("connected on %s" % path)
                return True
            except Exception as exc:
                last_error = exc
                self.close(quiet=True)
        if last_error and self.log:
            self.log.debug("no usable Discord IPC endpoint (%s)" % last_error)
        return False

    def drain(self):
        if not self._alive:
            return
        try:
            while self._available() >= 8:
                opcode, payload = self._read_frame(timeout=2.0)
                if opcode == op_close:
                    self._debug("Discord closed the connection: %s"
                                % payload.get("message", ""))
                    self._alive = False
                    return
                if opcode == op_ping:
                    self._write(op_pong, payload)
        except IPCError as exc:
            self._debug("connection lost: %s" % exc)
            self._alive = False

    def close(self, quiet=False):
        self._alive = False
        handle, self._handle = self._handle, None
        sock, self._socket = self._socket, None
        for closable in (handle, sock):
            if closable is not None:
                try:
                    closable.close()
                except Exception:
                    pass
        if not quiet:
            self._debug("connection closed")


    def _command(self, cmd, args):
        self._write(op_frame, {
            "cmd": cmd,
            "args": args,
            "nonce": str(uuid.uuid4()),
        })

    def set_activity(self, activity):
        if not self._alive:
            raise IPCError("not connected")
        self._command("SET_ACTIVITY", {
            "pid": os.getpid(),
            "activity": activity,
        })
        self.drain()


class PresenceLink(object):

    def __init__(self, client_id, logger=None, retry_interval=15.0):
        self.client_id = client_id
        self.log = logger
        self.retry_interval = retry_interval
        self._ipc = None
        self._pending = None
        self._has_pending = False
        self._last_sent = None
        self._next_retry = 0.0

    @property
    def connected(self):
        return self._ipc is not None and self._ipc.connected

    @property
    def user_name(self):
        user = self._ipc.user if self._ipc else None
        if not user:
            return None
        name = user.get("global_name") or user.get("username") or ""
        discriminator = user.get("discriminator")
        if discriminator and discriminator not in ("0", 0):
            name = "%s#%s" % (name, discriminator)
        return name or None

    def ensure_connection(self):
        if self.connected:
            self._ipc.drain()
            if self.connected:
                return True
        now = time.time()
        if now < self._next_retry:
            return False
        self._next_retry = now + self.retry_interval
        if self._ipc:
            self._ipc.close(quiet=True)
        self._ipc = DiscordIPC(self.client_id, self.log)
        try:
            if self._ipc.connect():
                if self.log:
                    who = self.user_name
                    self.log.info("connected to Discord%s"
                                  % (" as " + who if who else ""))
                self._last_sent = None
                return True
        except IPCError as exc:
            if self.log:
                self.log.debug("connect failed: %s" % exc)
        return False

    def update(self, activity, force=False):
        payload = json.dumps(activity, sort_keys=True)
        if not force and payload == self._last_sent and self.connected:
            self._ipc.drain()
            return False
        self._pending = activity
        self._has_pending = True
        self._last_sent = payload
        return self.flush()

    def flush(self):
        if not self._has_pending:
            if self.connected:
                self._ipc.drain()
            return False
        if not self.ensure_connection():
            return False
        try:
            self._ipc.set_activity(self._pending)
            self._has_pending = False
            return True
        except IPCError as exc:
            if self.log:
                self.log.warning("presence update failed: %s" % exc)
            self._ipc.close(quiet=True)
            self._last_sent = None
            return False

    def close(self):
        if self._ipc and self._ipc.connected:
            try:
                self._ipc.set_activity(None)
                time.sleep(0.2)
            except IPCError:
                pass
        if self._ipc:
            self._ipc.close(quiet=True)
        self._ipc = None
