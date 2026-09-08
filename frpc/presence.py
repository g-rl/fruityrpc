"""Turns an FL Studio snapshot into a Discord activity payload."""

import hashlib
import re
import string
import time


optional_group = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)
_double_separator = re.compile(r"\s*([-|·•])\s*(?=[-|·•])")
_multi_space = re.compile(r"\s{2,}")


def tidy(text):
    """Clean up separators left behind by empty placeholders."""
    if not text:
        return ""
    text = _double_separator.sub("", text)
    text = _multi_space.sub(" ", text)
    return text.strip().strip(" -|·•,").strip()


class SafeFormatter(string.Formatter):
    """``str.format`` that never raises: unknown fields become empty text."""

    def __init__(self, empty=""):
        super(SafeFormatter, self).__init__()
        self.empty = empty

    def get_value(self, key, args, kwargs):
        if isinstance(key, int):
            return self.empty
        value = kwargs.get(key, self.empty)
        return self.empty if value is None else value

    def get_field(self, field_name, args, kwargs):
        try:
            return super(SafeFormatter, self).get_field(field_name, args,
                                                        kwargs)
        except (KeyError, IndexError, AttributeError, TypeError):
            return self.empty, field_name

    def convert_field(self, value, conversion):
        try:
            return super(SafeFormatter, self).convert_field(value, conversion)
        except Exception:
            return value

    def format_field(self, value, format_spec):
        try:
            return super(SafeFormatter, self).format_field(value, format_spec)
        except Exception:
            return str(value)

    def has_data(self, template, variables):
        """True when every placeholder in ``template`` has a value."""
        try:
            fields = [name for _lit, name, _spec, _conv
                      in self.parse(template) if name]
        except Exception:
            return True
        for field in fields:
            key = re.split(r"[.\[]", field, 1)[0]
            value = variables.get(key, "")
            if value is None or str(value).strip() == "":
                return False
        return True

    def render(self, template, variables):
        """Format ``template``, resolving ``[[optional groups]]`` first.

        Anything wrapped in double square brackets disappears when one of the
        placeholders inside it has no value, so a line like::

            [[{bpm} BPM]][[ - {channel_count} channels]]

        degrades cleanly to "24 channels" when the tempo is unknown instead of
        leaving a stray " BPM - ".
        """
        if not template:
            return ""
        try:
            def replace(match):
                inner = match.group(1)
                if not self.has_data(inner, variables):
                    return ""
                return self.vformat(inner, (), variables)

            resolved = optional_group.sub(replace, template)
            return tidy(self.vformat(resolved, (), variables))
        except Exception:
            return ""


def format_duration(seconds):
    seconds = int(max(0, seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def _clean_number(value, decimals=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if decimals <= 0:
        return str(int(round(number)))
    return ("%%.%df" % decimals) % number


def _count_label(value, words):
    """``(40, ["Sound", "Sounds"])`` -> ``"40 Sounds"``; ``""`` if unknown."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number < 0:
        return ""
    singular = words[0] if words else ""
    plural = words[1] if len(words) > 1 else singular + "s"
    return "%d %s" % (number, singular if number == 1 else plural)


def _window_tail(caption):
    """The part after the first separator: "Settings - Theme" -> "Theme"."""
    if not caption:
        return ""
    parts = re.split(r"\s+[-–›>]\s+", caption, 1)
    if len(parts) < 2:
        return ""
    return parts[1].strip(" -:›")


def _truncate(text, limit):
    if not text or limit <= 0 or len(text) <= limit:
        return text
    return text[:max(1, limit - 1)].rstrip() + "…"


plugin_classes = {"TPluginForm", "TGeneratorForm", "TEffectForm",
                  "TWrapperForm", "TVSTForm"}

fl_window_names = re.compile(
    r"^(piano roll|playlist|mixer|channel rack|step sequencer|browser|"
    r"event editor|project|settings|plugin |add one|tool|script|"
    r"file settings|midi settings|audio settings|general settings)",
    re.IGNORECASE)


class PresenceBuilder(object):
    """Builds the activity dict, and decides which status applies."""

    def __init__(self, config, logger=None, catalog=None):
        self.log = logger
        self.catalog = catalog
        self.session_start = time.time()
        self.project_start = time.time()
        self._last_project = None
        self.reconfigure(config)

    def pick_icon(self, project):
        """The large image to use, honouring the icon setting at the top of
        the config.

        Accepts either a plain asset name or a block::

            "icon": {"mode": "random", "choices": ["eevee", "pichu"]}

        ``fixed`` uses ``name``; ``cycle`` steps through ``choices`` in order;
        ``random`` picks one of them. Both rotating modes pick per time slot
        rather than per tick, so the choice is stable between updates instead
        of flickering, and with ``rotate_seconds`` at 0 a random icon is drawn
        once per project. Anything the Discord application does not actually
        have as an art asset falls back to the FL logo.
        """
        setting = self.config.get("icon")
        fallback = "fl_logo"

        if isinstance(setting, str):
            return self._known(setting or fallback, fallback)
        if not isinstance(setting, dict):
            return self._known(fallback, fallback)

        mode = str(setting.get("mode") or "fixed").strip().lower()
        choices = [str(name).strip() for name in setting.get("choices") or []
                   if str(name).strip()]
        name = str(setting.get("name") or fallback).strip()
        try:
            rotate = float(setting.get("rotate_seconds") or 0)
        except (TypeError, ValueError):
            rotate = 0.0

        if mode == "fixed" or not choices:
            return self._known(name or fallback, fallback)

        if mode == "cycle":
            period = rotate if rotate > 0 else 300.0
            index = int(time.time() // period) % len(choices)
            return self._known(choices[index], fallback)

        if rotate > 0:
            slot = str(int(time.time() // rotate))
        else:
            slot = "%s|%s" % (project or "", int(self.project_start))
        digest = hashlib.sha1(slot.encode("utf-8", "replace")).hexdigest()
        index = int(digest[:8], 16) % len(choices)
        return self._known(choices[index], fallback)

    def _known(self, name, fallback):
        if self.catalog is None:
            return name
        return self.catalog.resolve(name, fallback)

    def reconfigure(self, config):
        self.config = config
        formatting = config.get("formatting", {})
        self.fmt = SafeFormatter(formatting.get("empty_value", ""))


    def pick_status(self, snapshot, midi):
        detection = self.config.get("detection", {})
        if not snapshot.get("running"):
            return "closed"
        if snapshot.get("exporting") or midi.get("rendering"):
            return "exporting"
        if midi.get("recording"):
            return "recording"
        if midi.get("playing"):
            return "playing"

        afk_after = detection.get("afk_seconds", 0) or 0
        if afk_after and snapshot.get("idle_seconds", 0) >= afk_after:
            return "afk"

        if not snapshot.get("project"):
            windows = snapshot.get("windows") or []
            on_start_screen = snapshot.get("focused_class") == "TWelcomeWizard"
            if on_start_screen or not windows:
                return "no_project"
        if detection.get("require_foreground") and not snapshot.get("foreground"):
            return "idle"
        return "editing"


    def build_variables(self, snapshot, midi, status):
        privacy = self.config.get("privacy", {})
        formatting = self.config.get("formatting", {})
        empty = formatting.get("empty_value", "")
        words = formatting.get("count_words", {})

        raw_project = snapshot.get("project") or midi.get("project") or ""
        if not raw_project and snapshot.get("running") and status not in (
                "no_project", "closed"):
            raw_project = formatting.get("untitled_label", "Untitled project")
        project = raw_project
        lowered = project.lower()
        hidden = privacy.get("hide_project_name") or any(
            keyword and keyword.lower() in lowered
            for keyword in privacy.get("hidden_project_keywords", []))
        if project and hidden:
            project = privacy.get("project_placeholder", "a project")
        project = _truncate(project, formatting.get("max_project_length", 60))
        if project and snapshot.get("unsaved"):
            project += formatting.get("unsaved_suffix", "")

        window = snapshot.get("focused_window") or ""
        window_class = snapshot.get("focused_class") or ""
        if privacy.get("hide_window_names"):
            window = ""

        plugin = ""
        is_plugin = (window_class in plugin_classes if window_class
                     else bool(window) and not fl_window_names.match(window))
        if window and is_plugin:
            plugin = re.split(r"\s+[\(\[]", window)[0].strip()
        if plugin and privacy.get("hide_plugin_names"):
            plugin = privacy.get("plugin_placeholder", "a plugin")
            window = plugin
        if not plugin:
            plugin = midi.get("plugin") or ""

        channel = midi.get("channel") or ""
        if not channel and window.lower().startswith("piano roll"):
            parts = re.split(r"\s+-\s+", window, 1)
            if len(parts) == 2:
                channel = parts[1].strip()

        decimals = formatting.get("bpm_decimals", 0)
        bpm = _clean_number(midi.get("tempo"), decimals)

        bar = midi.get("bar")
        beat = midi.get("beat")
        tick = midi.get("tick")
        position = ""
        if bar is not None and beat is not None:
            position = self.fmt.render(
                formatting.get("position_format", "{bar}:{beat}"),
                {"bar": bar, "beat": beat, "tick": tick if tick is not None
                 else ""})
        position_suffix = ""
        if position:
            position_suffix = self.fmt.render(
                formatting.get("position_suffix_format", " [{position}]"),
                {"position": position})
            if position_suffix and not position_suffix.startswith(" "):
                position_suffix = " " + position_suffix

        song_mode = midi.get("song_mode")
        playback_mode = ""
        if song_mode is not None:
            playback_mode = "Song" if song_mode else "Pattern"

        pattern = midi.get("pattern") or ""
        if pattern and playback_mode == "Song":
            pattern_or_song = "Song mode"
        elif pattern:
            pattern_or_song = "Pattern: " + pattern
        else:
            pattern_or_song = playback_mode and (playback_mode + " mode") or ""

        variables = {
            "project": project or empty,
            "project_raw": raw_project or empty,
            "version": snapshot.get("version") or empty,
            "version_short": (snapshot.get("version_short")
                              or snapshot.get("version") or empty),
            "fl_version_full": midi.get("fl_version")
                               or snapshot.get("version_full") or empty,
            "bitness": snapshot.get("bitness") or empty,
            "exe": snapshot.get("exe") or empty,
            "icon": self.pick_icon(raw_project),
            "window": window or empty,
            "window_clean": window.rstrip(" -:›") or empty,
            "window_tail": _window_tail(window) or empty,
            "window_class": window_class or empty,
            "plugin": plugin or empty,
            "status": status,
            "open_windows": len(snapshot.get("windows") or []),
            "title": snapshot.get("main_title") or empty,

            "bpm": bpm or empty,
            "tempo": bpm or empty,
            "position": position or empty,
            "position_suffix": position_suffix or empty,
            "bar": bar if bar is not None else empty,
            "beat": beat if beat is not None else empty,
            "tick": tick if tick is not None else empty,
            "song_time": (format_duration(midi["song_seconds"])
                          if isinstance(midi.get("song_seconds"), (int, float))
                          else empty),
            "song_length": (format_duration(midi["length_seconds"])
                            if isinstance(midi.get("length_seconds"),
                                          (int, float)) else empty),
            "pattern": pattern or empty,
            "pattern_number": midi.get("pattern_number", empty),
            "pattern_count": midi.get("pattern_count", empty),
            "pattern_or_song": pattern_or_song or empty,
            "playback_mode": playback_mode or empty,
            "channel": channel or empty,
            "channel_count": midi.get("channel_count", empty),
            "mixer_track": midi.get("mixer_track") or empty,
            "mixer_track_index": midi.get("mixer_track_index", empty),
            "time_signature": midi.get("time_signature") or empty,
            "deep_mode": "on" if midi else "off",

            "sounds": _count_label(midi.get("channel_count"),
                                   words.get("sound", ["Sound", "Sounds"])),
            "patterns": _count_label(midi.get("pattern_count"),
                                     words.get("pattern",
                                               ["Pattern", "Patterns"])),
            "mixer_tracks": _count_label(
                midi.get("mixer_used_count", midi.get("mixer_track_count")),
                words.get("mixer_track", ["Track", "Tracks"])),
            "effects": _count_label(midi.get("effect_count"),
                                    words.get("effect",
                                              ["Effect", "Effects"])),
            "dot": formatting.get("separator", "•"),
        }

        activity, activity_short = self._activity_text(window, window_class,
                                                       variables)
        variables["activity"] = activity
        variables["activity_short"] = activity_short
        return variables

    def _activity_text(self, window, window_class, variables):
        """Pick the activity line for the focused FL window.

        A rule can match on the window class (stable across FL versions and
        interface languages) and/or on the caption; when both are given both
        must match.
        """
        activities = self.config.get("activities", {})
        default = activities.get("default", "Working on a track")
        default_short = activities.get("default_short", "FL Studio")
        if not window and not window_class:
            return (self.fmt.render(default, variables),
                    self.fmt.render(default_short, variables))
        for rule in activities.get("rules", []):
            pattern = rule.get("match")
            class_pattern = rule.get("class")
            if not pattern and not class_pattern:
                continue
            try:
                if class_pattern and not re.search(class_pattern,
                                                   window_class or "",
                                                   re.IGNORECASE):
                    continue
                if pattern and not re.search(pattern, window, re.IGNORECASE):
                    continue
            except re.error:
                continue
            text = self.fmt.render(rule.get("text", default), variables)
            short = self.fmt.render(rule.get("short", default_short), variables)
            return (text or self.fmt.render(default, variables),
                    short or self.fmt.render(default_short, variables))
        return (self.fmt.render(default, variables),
                self.fmt.render(default_short, variables))


    def _timestamp_start(self, mode, snapshot, midi):
        now = time.time()
        if mode == "none":
            return None
        if mode == "song":
            seconds = midi.get("song_seconds")
            if isinstance(seconds, (int, float)) and seconds >= 0:
                return now - seconds
            mode = "project"
        if mode == "project":
            return self.project_start
        return self.session_start

    def _track_project(self, snapshot):
        project = snapshot.get("project") if snapshot.get("running") else None
        if project != self._last_project:
            self._last_project = project
            self.project_start = time.time()


    def _buttons(self, configured, variables):
        """Discord shows at most two buttons.

        The first stays pinned; anything past the second takes turns in the
        remaining slot, so three or more links all get seen. Set
        presence.button_rotation_seconds to 0 to keep the first two only.
        """
        valid = []
        for button in configured or []:
            if not isinstance(button, dict):
                continue
            label = _truncate(self.fmt.render(button.get("label"), variables),
                              32)
            url = self.fmt.render(button.get("url"), variables)
            if label and url.startswith(("http://", "https://")):
                valid.append({"label": label, "url": url})

        if len(valid) <= 2:
            return valid

        rotate = self.config.get(
            "button_rotation_seconds",
            self.config.get("presence", {}).get("button_rotation_seconds", 60))
        try:
            rotate = float(rotate)
        except (TypeError, ValueError):
            rotate = 60.0
        if rotate <= 0:
            return valid[:2]

        rotating = valid[1:]
        index = int(time.time() // rotate) % len(rotating)
        return [valid[0], rotating[index]]

    def build(self, snapshot, midi):
        """Return ``(activity_or_None, status, variables)``."""
        self._track_project(snapshot)
        status = self.pick_status(snapshot, midi)

        privacy = self.config.get("privacy", {})
        raw_project = (snapshot.get("project") or "").lower()
        for blocked in privacy.get("disable_when_project_matches", []):
            if blocked and blocked.lower() in raw_project:
                return None, status, {}

        if status == "closed" and self.config.get("when_fl_closed") == "clear":
            return None, status, {}

        variables = self.build_variables(snapshot, midi, status)

        base = dict(self.config.get("presence", {}))
        overrides = self.config.get("statuses", {}).get(status, {})
        merged = dict(base)
        merged.update({key: value for key, value in overrides.items()
                       if key != "timestamp"})

        formatting = self.config.get("formatting", {})
        limit = formatting.get("max_line_length", 128)
        variables["elapsed"] = ""

        timestamp_mode = overrides.get("timestamp", "session")
        start = self._timestamp_start(timestamp_mode, snapshot, midi)
        if start:
            variables["elapsed"] = format_duration(time.time() - start)

        details = _truncate(self.fmt.render(merged.get("details"), variables),
                            limit)
        state = _truncate(self.fmt.render(merged.get("state"), variables),
                          limit)
        large_image = self.fmt.render(merged.get("large_image"), variables)
        large_text = _truncate(self.fmt.render(merged.get("large_text"),
                                               variables), limit)
        small_image = self.fmt.render(merged.get("small_image"), variables)
        small_text = _truncate(self.fmt.render(merged.get("small_text"),
                                               variables), limit)

        activity = {}
        activity_type = self.config.get("activity_type", 0)
        if isinstance(activity_type, int) and activity_type != 0:
            activity["type"] = activity_type
        if details:
            activity["details"] = details
        if len(state) >= 2:
            activity["state"] = state

        assets = {}
        if large_image:
            assets["large_image"] = large_image
            if large_text:
                assets["large_text"] = large_text
        if small_image:
            assets["small_image"] = small_image
            if small_text:
                assets["small_text"] = small_text
        if assets:
            activity["assets"] = assets

        if start:
            activity["timestamps"] = {"start": int(start * 1000)}

        configured = merged.get("buttons")
        if configured is None:
            configured = self.config.get("buttons")
        buttons = self._buttons(configured, variables)
        if buttons:
            activity["buttons"] = buttons

        if not activity:
            return None, status, variables
        return activity, status, variables
