# name=FruityRPC
# url=https://github.com/g-rl/frpc

import json
import os
import sys
import time
import traceback


state_dir = r"__FRUITYRPC_STATE_DIR__"


_folder_cache = None


def _candidate_folders():
    folders = []
    if state_dir and not state_dir.startswith("__FRUITYRPC"):
        folders.append(state_dir)
    try:
        folders.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    for variable in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        base = os.environ.get(variable)
        if base:
            folders.append(os.path.join(base, "FruityRPC"))
    folders.append(os.path.join(os.path.expanduser("~"), "FruityRPC"))

    seen = set()
    unique = []
    for folder in folders:
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen:
            seen.add(key)
            unique.append(folder)
    return unique


def _usable(folder):
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        probe = os.path.join(folder, ".fruityrpc-probe")
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _folder():
    global _folder_cache
    if _folder_cache:
        return _folder_cache
    for folder in _candidate_folders():
        if _usable(folder):
            _folder_cache = folder
            return folder
    return _candidate_folders()[0]


def _log(message):
    try:
        path = os.path.join(_folder(), "script.log")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a") as handle:
            handle.write("%s  %s\n" % (stamp, message))
    except Exception:
        pass


print("FruityRPC bridge: loading")
_log("script loaded by fl studio, python %s" % sys.version.split()[0])
_log("writing to %s" % _folder())

try:
    import midi
    import transport
    import ui
    import channels
    import patterns
    import mixer
    import general
except Exception:
    _log("fl api import failed: %s" % traceback.format_exc())
    raise

try:
    import plugins
except Exception:
    plugins = None
    _log("plugins module unavailable, mixer counts disabled")

try:
    import device
except Exception:
    device = None


write_interval = 0.4
idle_interval = 1.5

_last_write = 0.0
_enabled = True
_state_path = None


def _safe(function, default=None):
    try:
        return function()
    except Exception:
        return default


def _state_file():
    return os.path.join(_folder(), "state.json")


def _tempo():
    value = _safe(lambda: mixer.getCurrentTempo(), None)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value > 1000.0:
        value /= 1000.0
    return round(value, 3)


def _position():
    hint = _safe(lambda: transport.getSongPosHint(), "") or ""
    parts = [part.strip() for part in str(hint).replace(".", ":").split(":")]
    numbers = []
    for part in parts[:3]:
        try:
            numbers.append(int(part))
        except (TypeError, ValueError):
            numbers.append(None)
    while len(numbers) < 3:
        numbers.append(None)
    return numbers[0], numbers[1], numbers[2]


def _seconds(getter):
    for mode in (getattr(midi, "SONGLENGTH_S", 1), 1):
        value = _safe(lambda: getter(mode), None)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return None


def _time_signature():
    ppb = _safe(lambda: general.getRecPPB(), None)
    ppq = _safe(lambda: general.getRecPPQ(), None)
    try:
        beats = int(round(float(ppb) / float(ppq)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if beats <= 0:
        return None
    return "%d/4" % beats


def _selected_channel():
    index = _safe(lambda: channels.selectedChannel(), -1)
    if index is None or index < 0:
        return "", index
    name = _safe(lambda: channels.getChannelName(index), "") or ""
    return name, index


def _selected_mixer_track():
    index = _safe(lambda: mixer.trackNumber(), -1)
    if index is None or index < 0:
        return "", index
    name = _safe(lambda: mixer.getTrackName(index), "") or ""
    return name, index


_mixer_scan_interval = 5.0
_mixer_cache = {"ts": 0.0, "used": None}


def _effect_count(track):
    if plugins is None or track is None or track < 0:
        return None
    count = 0
    for slot in range(10):
        if _safe(lambda: plugins.isValid(track, slot), False):
            count += 1
    return count


def _mixer_used():
    now = time.time()
    if now - _mixer_cache["ts"] < _mixer_scan_interval:
        return _mixer_cache["used"]

    if plugins is None:
        return None

    total = _safe(lambda: mixer.trackCount(), 0) or 0
    used = 0
    for track in range(total):
        name = _safe(lambda: mixer.getTrackName(track), "") or ""
        lowered = name.strip().lower()
        if lowered and not lowered.startswith("insert"):
            used += 1
            continue
        for slot in range(10):
            if _safe(lambda: plugins.isValid(track, slot), False):
                used += 1
                break

    _mixer_cache["ts"] = now
    _mixer_cache["used"] = used
    return used


def _current_pattern():
    number = _safe(lambda: patterns.patternNumber(), -1)
    name = ""
    if number is not None and number >= 0:
        name = _safe(lambda: patterns.getPatternName(number), "") or ""
    return name, number


def collect():
    bar, beat, tick = _position()
    channel_name, channel_index = _selected_channel()
    mixer_name, mixer_index = _selected_mixer_track()
    pattern_name, pattern_number = _current_pattern()

    loop_mode = _safe(lambda: transport.getLoopMode(), None)

    state = {
        "ts": time.time(),
        "playing": bool(_safe(lambda: transport.isPlaying(), False)),
        "recording": bool(_safe(lambda: transport.isRecording(), False)),
        "song_mode": (bool(loop_mode) if loop_mode is not None else None),
        "tempo": _tempo(),
        "bar": bar,
        "beat": beat,
        "tick": tick,
        "song_seconds": _seconds(transport.getSongPos),
        "length_seconds": _seconds(transport.getSongLength),
        "pattern": pattern_name,
        "pattern_number": pattern_number,
        "pattern_count": _safe(lambda: patterns.patternCount(), None),
        "channel": channel_name,
        "channel_index": channel_index,
        "channel_count": _safe(lambda: channels.channelCount(), None),
        "mixer_track": mixer_name,
        "mixer_track_index": mixer_index,
        "mixer_track_count": _safe(lambda: mixer.trackCount(), None),
        "mixer_used_count": _mixer_used(),
        "effect_count": _effect_count(mixer_index),
        "time_signature": _time_signature(),
        "plugin": _safe(lambda: ui.getFocusedFormCaption(), "") or "",
        "fl_version": str(_safe(lambda: ui.getVersion(), "") or ""),
        "metronome": bool(_safe(lambda: general.getUseMetronome(), False)),
    }
    return state


def write_state(state):
    global _enabled
    if not _enabled or not _state_path:
        return
    try:
        temporary = _state_path + ".tmp"
        with open(temporary, "w") as handle:
            json.dump(state, handle)
        os.replace(temporary, _state_path)
    except Exception:
        try:
            with open(_state_path, "w") as handle:
                json.dump(state, handle)
        except Exception:
            _enabled = False
            _log("cannot write the state file: %s"
                 % traceback.format_exc())


def _tick(force=False):
    global _last_write
    now = time.time()
    playing = _safe(lambda: transport.isPlaying(), False)
    interval = write_interval if playing else idle_interval
    if not force and now - _last_write < interval:
        return
    _last_write = now
    write_state(collect())


def OnInit():
    global _state_path
    _state_path = _state_file()
    _tick(force=True)
    _log("oninit, writing %s" % _state_path)
    print("FruityRPC bridge active -> %s" % _state_path)


def OnDeInit():
    try:
        if _state_path and os.path.isfile(_state_path):
            os.remove(_state_path)
    except Exception:
        pass


def OnIdle():
    _tick()


def OnRefresh(flags):
    _tick(force=True)


def OnUpdateBeatIndicator(value):
    _tick()


def OnProjectLoad(status):
    _tick(force=True)


def OnMidiIn(event):
    event.handled = False
