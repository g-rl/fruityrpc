import copy
import json
import os
import sys

from . import yamlish

app_name = "FruityRPC"


_config_dir = None


def install_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable(directory):
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def config_dir():
    global _config_dir
    override = os.environ.get("FRUITYRPC_HOME")
    if override:
        return os.path.abspath(override)
    if _config_dir:
        return _config_dir

    local = os.path.join(install_root(), "data")
    if _writable(local):
        _config_dir = local
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        _config_dir = os.path.join(base, app_name)
    return _config_dir


def config_path():
    folder = config_dir()
    modern = os.path.join(folder, "config.yml")
    if os.path.isfile(modern):
        return modern
    for legacy in ("config.yaml", "config.json"):
        candidate = os.path.join(folder, legacy)
        if os.path.isfile(candidate):
            return candidate
    return modern


def state_path():
    return os.path.join(config_dir(), "state.json")


def log_path():
    return os.path.join(config_dir(), "fruityrpc.log")


defaults = {
    "//": [
        "FruityRPC configuration.",
        "Every text field supports {placeholders}; the full list is at the",
        "bottom of this file under placeholders. Unknown placeholders",
        "resolve to an empty string, and a line that ends up empty is",
        "omitted from the presence instead of showing blank.",
        "Wrap a part of a line in [[double brackets]] to drop it whenever a",
        "placeholder inside it has no value, e.g.",
        "  \"{project}[[ - {bpm} BPM]]\"",
        "shows ' - 174 BPM' only when the tempo is actually known.",
    ],

    "client_id": "",
    "//client_id": [
        "Discord application ID. Create one at",
        "https://discord.com/developers/applications, name it 'FL Studio',",
        "then under Rich Presence > Art Assets upload the FL logo with the",
        "asset name 'fl_logo'. Paste the Application ID here."
    ],

    "icon": {
        "//": [
            "The big picture on the presence.",
            "",
            "  mode 'fixed'   - always 'name'",
            "  mode 'random'  - one of 'choices', drawn per project (or every",
            "                   rotate_seconds when that is above 0)",
            "  mode 'cycle'   - steps through 'choices' in order, changing",
            "                   every rotate_seconds (default 300)",
            "",
            "Any name here must be the key of an art asset uploaded to your",
            "Discord application (Rich Presence > Art Assets). Names that are",
            "not there fall back to fl_logo, so a typo never shows a blank",
            "square. A plain string works too: \"icon\": \"eevee\".",
            "",
            "The assets folder next to FruityRPC ships extra icons you can",
            "upload; anything you upload can be listed in choices."
        ],
        "mode": "fixed",
        "name": "fl_logo",
        "choices": [],
        "rotate_seconds": 0
    },

    "//buttons": [
        "The buttons under the presence. Label and url are both yours to",
        "change. A label is at most 32 characters and a url must start with",
        "http:// or https://. Use [] for no buttons at all.",
        "",
        "Discord shows two buttons at a time. The first stays put and any",
        "button past the second takes turns in the remaining slot, swapping",
        "every button_rotation_seconds. Set that to 0 to show only the first",
        "two and ignore the rest."
    ],
    "buttons": [
        {"label": "by nyli", "url": "https://github.com/g-rl"},
        {"label": "view & download", "url": "https://github.com/g-rl/fruityrpc"},
        {"label": "buy fl studio", "url": "https://www.image-line.com/"}
    ],
    "button_rotation_seconds": 60,

    "poll_interval": 1.0,
    "//poll_interval": "Seconds between FL Studio window scans.",

    "min_update_interval": 5.0,
    "//min_update_interval": [
        "Discord rate-limits presence updates to about one every 5 seconds.",
        "Values below 5 are clamped."
    ],

    "reconnect_interval": 15.0,
    "//reconnect_interval": "Seconds between Discord reconnection attempts.",

    "log_level": "info",
    "//log_level": "debug | info | warning | error",

    "single_instance": True,
    "//single_instance": [
        "Refuse to start when another FruityRPC is already running.",
        "Keep this on when using 'Launch at startup' in FL's External tools."
    ],

    "when_fl_closed": "clear",
    "//when_fl_closed": [
        "What to do while FL Studio is not running:",
        "'clear' hides the presence, 'idle' shows the 'closed' status,",
        "'exit' quits FruityRPC."
    ],

    "exit_after_fl_closed_seconds": 0,
    "//exit_after_fl_closed_seconds": [
        "0 = never exit. Otherwise quit this many seconds after the last FL",
        "Studio window disappears. Handy with 'Launch at startup'."
    ],

    "activity_type": 0,
    "//activity_type": "0 Playing, 2 Listening to, 3 Watching, 5 Competing in.",

    "detection": {
        "//": [
            "How FruityRPC finds FL Studio. Deliberately version-agnostic:",
            "a window counts as FL Studio if its process name OR its title",
            "matches, so FL Studio 9 through 2024+, 32-bit, 64-bit, portable",
            "and renamed builds are all picked up."
        ],

        "process_regex": "^fl(?:64|32)?(?:[ _.-].*)?\\.exe$",
        "//process_regex": "Case-insensitive regex tested on the exe file name.",

        "extra_process_names": [],
        "//extra_process_names": [
            "Extra exact exe names for unusual builds,",
            "e.g. [\"FL64_stable.exe\", \"FLStudio.exe\"]."
        ],

        "main_window_class_regex": "^TFruityLoopsMainForm$",
        "//main_window_class_regex": [
            "The Win32 class of FL Studio's main window. It has been",
            "TFruityLoopsMainForm since the Fruity Loops days and does not",
            "change with the interface language, which makes it the most",
            "reliable signal of the three."
        ],

        "ignore_window_class_regex":
            "Panel$|Sheet$|Splitter|Toolbar|Caption$|Hint|^Static$|^Button$",
        "//ignore_window_class_regex": [
            "Focused controls of these classes are skipped when working out",
            "which FL window you are in; FruityRPC walks up to the real form",
            "instead."
        ],

        "window_title_regex": "FL\\s*Studio|Fruity\\s*?Loops",
        "//window_title_regex": [
            "A visible window whose title matches this is treated as an FL",
            "Studio main window even when the process name does not match."
        ],

        "project_title_regex": "",
        "//project_title_regex": [
            "Optional override. When set, the first capture group of this",
            "regex applied to the main window title becomes the project name.",
            "Leave empty to use the built-in parser, which understands every",
            "known FL Studio title layout."
        ],

        "untitled_names": ["untitled", "new project", "empty", ""],
        "//untitled_names": "Names treated as 'no project open'.",

        "afk_seconds": 300,
        "//afk_seconds": [
            "No keyboard or mouse input for this long switches to the 'afk'",
            "status. 0 disables AFK detection."
        ],

        "sticky_focus": True,
        "//sticky_focus": [
            "Windows only reports which sub-window has focus while FL Studio",
            "is the active application. With this on, FruityRPC keeps showing",
            "the last window you were in after you alt-tab away instead of",
            "falling back to a generic line."
        ],

        "require_foreground": False,
        "//require_foreground": [
            "When true, the 'editing' status is only used while FL Studio is",
            "the foreground application; otherwise 'idle' is used."
        ],

        "auto_install_midi_script": True,
        "//auto_install_midi_script": [
            "Copy the bridge script into FL Studio's Hardware folder by",
            "itself, and refresh it whenever FruityRPC is updated."
        ],

        "auto_bind_midi_script": True,
        "//auto_bind_midi_script": [
            "Attach the bridge to a free MIDI input automatically, so deep",
            "mode needs no trip through Options > MIDI settings. It only",
            "happens while FL Studio is closed, because FL rewrites those",
            "settings from memory when it exits, and it never touches a port",
            "that already has a controller script of its own."
        ],

        "use_flp_file": True,
        "//use_flp_file": [
            "Read the sound, pattern and tempo counts out of the saved .flp",
            "file when deep mode is not running. It costs nothing and needs",
            "no setup at all, but it only reflects the last time the project",
            "was saved. Deep mode, when available, always wins."
        ],

        "project_search_paths": [],
        "//project_search_paths": [
            "Extra folders to look in for the project file. FL only writes",
            "its recent-files list when it exits, so a project saved this",
            "session is found by searching the folders projects normally",
            "live in. Add yours here if it sits somewhere unusual."
        ],

        "use_midi_state": True,
        "//use_midi_state": [
            "Read live transport data (tempo, playing, recording, bar/beat,",
            "pattern, channel, mixer track) from the optional FL MIDI script.",
            "See the README section 'Deep mode'."
        ],

        "midi_state_max_age": 6.0,
        "//midi_state_max_age": [
            "Ignore the MIDI script state file when it has not been refreshed",
            "within this many seconds."
        ],

        "export_title_regex": "render(ing)?|export|bounce",
        "//export_title_regex": [
            "Window captions matching this mean FL is rendering audio."
        ],
    },

    "privacy": {
        "//": "Hide anything you would rather not broadcast.",
        "hide_project_name": False,
        "project_placeholder": "a secret project",
        "hide_plugin_names": False,
        "plugin_placeholder": "a plugin",
        "hide_window_names": False,
        "hidden_project_keywords": [],
        "//hidden_project_keywords": [
            "When the project name contains any of these (case-insensitive)",
            "it is replaced by project_placeholder."
        ],
        "disable_when_project_matches": [],
        "//disable_when_project_matches": [
            "Presence is cleared entirely while one of these projects is open."
        ],
    },

    "presence": {
        "//": "Base presence. Every status below can override any of these.",
        "large_image": "{icon}",
        "//large_image": [
            "Normally left as {icon} so the icon block at the top of this",
            "file decides. Can also be an art-asset name directly, or an",
            "https:// image URL."
        ],
        "large_text": "FL Studio {version_short}",
        "//large_text": [
            "Tooltip on the big image. {version_short} is the real build",
            "number, e.g. 25.1.3, rather than the marketing year FL puts in",
            "its title bar."
        ],
        "small_image": "",
        "small_text": "",
        "//buttons": [
            "Leave as null to use the buttons at the top of this file.",
            "Set a list here to give this one status its own buttons."
        ],
        "buttons": None,
    },

    "statuses": {
        "//": [
            "One entry per situation. 'details' is the first presence line,",
            "'state' the second. 'timestamp' picks what the elapsed timer",
            "counts: session | project | song | none."
        ],

        "recording": {
            "details": "{project}[[ (v{version_short})]]",
            "state": "Recording{position_suffix}",
            "small_image": "record",
            "small_text": "Recording[[ - {bpm} BPM]]",
            "timestamp": "project",
        },
        "playing": {
            "details": "{project}[[ (v{version_short})]]",
            "state": "Playing{position_suffix}[[ - {bpm} BPM]]",
            "small_image": "play",
            "small_text": "{pattern_or_song}",
            "timestamp": "project",
        },
        "exporting": {
            "details": "{project}[[ (v{version_short})]]",
            "state": "Rendering audio",
            "small_image": "export",
            "small_text": "Exporting",
            "timestamp": "project",
        },
        "editing": {
            "details": "{project}[[ (v{version_short})]]",
            "state": "{activity}",
            "small_image": "edit",
            "small_text": "[[{bpm} BPM]][[ - {channel_count} channels]]",
            "timestamp": "project",
        },
        "idle": {
            "details": "{project}[[ (v{version_short})]]",
            "state": "Idle[[ - {activity_short}]]",
            "small_image": "edit",
            "small_text": "[[{bpm} BPM]]",
            "timestamp": "project",
        },
        "afk": {
            "details": "{project}[[ (v{version_short})]]",
            "state": "AFK",
            "small_image": "afk",
            "small_text": "AFK",
            "timestamp": "project",
        },
        "no_project": {
            "details": "FL Studio {version_short}",
            "state": "No project open",
            "small_image": "",
            "small_text": "",
            "timestamp": "session",
        },
        "closed": {
            "details": "FL Studio",
            "state": "Not running",
            "small_image": "",
            "small_text": "",
            "timestamp": "session",
        },
    },

    "activities": {
        "//": [
            "Maps the focused FL window to the text behind {activity}.",
            "Rules are matched top to bottom. 'class' is a regex tested",
            "against the window class and 'match' a regex tested against the",
            "caption; when a rule has both, both must match. Class names are",
            "the reliable ones - they are identical in every FL Studio",
            "version and in every interface language - so the class rules",
            "come first and the caption rules act as a fallback.",
            "'short' feeds {activity_short}. The final '.' rule is the",
            "catch-all used for plugin windows."
        ],
        "rules": [
            {"class": "^TPianoRollForm$",
             "text": "Piano roll[[ {dot} {channel}]]",
             "short": "Piano roll"},

            {"//": "FL uses TEventEditForm for the Piano roll, the Playlist "
                   "and the Event editor alike, so these are told apart by "
                   "caption, with the caption itself as the last resort.",
             "class": "^TEventEditForm$", "match": "^piano roll",
             "text": "Piano roll[[ {dot} {channel}]]",
             "short": "Piano roll"},
            {"class": "^TEventEditForm$", "match": "^playlist",
             "text": "Playlist[[ {dot} {sounds}]][[ {dot} {patterns}]]",
             "short": "Playlist"},
            {"class": "^TEventEditForm$", "match": "^event editor|automation",
             "text": "Automation",
             "short": "Automation"},
            {"class": "^TEventEditForm$",
             "text": "{window_clean}",
             "short": "{window_clean}"},
            {"class": "^TFXForm$",
             "text": "Mixer[[ {dot} {mixer_track}]][[ {dot} {effects}]][[ {dot} {mixer_tracks}]]",
             "short": "Mixer"},
            {"class": "^TStepSeqForm$",
             "text": "Channel rack[[ {dot} {sounds}]][[ {dot} {patterns}]]",
             "short": "Channel rack"},
            {"class": "^TSampleListForm$",
             "text": "Browsing...",
             "short": "Browser"},
            {"class": "^TGraphEditorForm$",
             "text": "Automation",
             "short": "Automation"},
            {"class": "^TMIDIForm$",
             "text": "Settings[[ {dot} {window_tail}]]",
             "short": "Settings"},
            {"class": "^TWelcomeWizard$",
             "text": "Start screen",
             "short": "Start screen"},
            {"class": "^TPythonForm$",
             "text": "Scripting",
             "short": "Scripting"},

            {"match": "^piano roll",
             "text": "Piano roll[[ {dot} {channel}]]",
             "short": "Piano roll"},
            {"match": "^playlist",
             "text": "Playlist[[ {dot} {sounds}]][[ {dot} {patterns}]]",
             "short": "Playlist"},
            {"match": "^mixer",
             "text": "Mixer[[ {dot} {mixer_track}]][[ {dot} {effects}]][[ {dot} {mixer_tracks}]]",
             "short": "Mixer"},
            {"match": "^channel rack|^step sequencer|^channel settings",
             "text": "Channel rack[[ {dot} {sounds}]][[ {dot} {patterns}]]",
             "short": "Channel rack"},
            {"match": "^browser",
             "text": "Browsing...",
             "short": "Browser"},
            {"match": "^event editor|automation clip",
             "text": "Automation",
             "short": "Automation"},
            {"match": "^playlist track|^track properties",
             "text": "Playlist",
             "short": "Playlist"},
            {"match": "^(midi|audio|general|file|theme|project)?\\s*settings",
             "text": "Settings[[ {dot} {window_tail}]]",
             "short": "Settings"},
            {"match": "^project (info|general settings)",
             "text": "Project settings",
             "short": "Project settings"},
            {"match": "^manage plugins|^plugin manager",
             "text": "Plugin manager",
             "short": "Plugin manager"},
            {"match": "^debugging log|^script output",
             "text": "Debug log",
             "short": "Debug log"},
            {"match": "^manage accounts|^account",
             "text": "Account settings",
             "short": "Account settings"},
            {"match": "^plugin (picker|database)|^add one",
             "text": "Browsing plugins",
             "short": "Plugin picker"},
            {"class": "^TPluginForm$|^TGeneratorForm$|^TEffectForm$",
             "text": "{plugin}",
             "short": "{plugin}"},
            {"//": "anything else - a plugin, a dialog, a window added in a "
                   "later fl version - is named by its own caption.",
             "match": ".",
             "text": "{window_clean}",
             "short": "{window_clean}"},
        ],
        "default": "In the studio",
        "default_short": "FL Studio",
    },

    "formatting": {
        "//": "Small cosmetic knobs.",
        "unsaved_suffix": " *",
        "//unsaved_suffix": [
            "Appended to {project} when the window title says the project has",
            "unsaved changes."
        ],
        "strip_flp_extension": False,
        "//strip_flp_extension": [
            "By default the file name is shown exactly as fl studio",
            "has it, extension included, so a project opened from a",
            "zip reads 'file.zip' rather than pretending to be an flp.",
            "Set true to hide a trailing .flp."
        ],
        "untitled_label": "Untitled project",
        "//untitled_label": [
            "FL Studio only shows a project name in its title bar once the",
            "project has been saved. While you work on an unsaved one this",
            "text is used for {project}."
        ],
        "position_format": "{bar}:{beat}",
        "position_suffix_format": " [{position}]",
        "//position_suffix_format": [
            "Used by {position_suffix}; it collapses to nothing when the",
            "MIDI script is not running."
        ],
        "separator": "•",
        "//separator": "The character behind {dot}, used between the parts "
                       "of a busier line.",
        "count_words": {
            "//": "Singular and plural words behind {sounds}, {patterns} and "
                  "{mixer_tracks}. These need deep mode to have any value.",
            "sound": ["Sound", "Sounds"],
            "pattern": ["Pattern", "Patterns"],
            "mixer_track": ["Track", "Tracks"],
            "effect": ["Effect", "Effects"]
        },
        "max_project_length": 60,
        "max_line_length": 128,
        "bpm_decimals": 0,
        "empty_value": "",
        "//empty_value": "Text substituted for placeholders with no data.",
    },

    "//placeholders": [
        "{project}            project name, privacy rules applied",
        "{project_raw}        project name with no privacy filtering",
        "{icon}               the resolved art asset for the big image",
        "{version_short}      full FL Studio build, e.g. 25.1.3",
        "{version}            version FL shows in its title bar, e.g. 2025",
        "{bitness}            '64-bit' or '32-bit'",
        "{window}             caption of the focused FL window",
        "{window_clean}       the same without FL's trailing separator",
        "{window_tail}        what follows the separator, e.g. the settings tab",
        "{activity}           mapped activity text, see 'activities'",
        "{activity_short}     short form of the same",
        "{plugin}             plugin name when a plugin window is focused",
        "{status}             current status key",
        "{elapsed}            H:MM:SS since the current timer started",
        "{open_windows}       number of open FL sub-windows",
        "--- the following need the optional FL MIDI script (Deep mode) ---",
        "{bpm} {tempo}        project tempo",
        "{position}           song position formatted with position_format",
        "{position_suffix}    the same wrapped in position_suffix_format",
        "{bar} {beat} {tick}  raw song position parts",
        "{song_time}          song position as M:SS",
        "{song_length}        song length as M:SS",
        "{pattern}            selected pattern name",
        "{pattern_number}     selected pattern index",
        "{pattern_count}      number of patterns",
        "{pattern_or_song}    'Pattern <name>' or 'Song mode'",
        "{playback_mode}      'Pattern' or 'Song'",
        "{channel}            selected channel name",
        "{channel_count}      number of channels",
        "{mixer_track}        selected mixer track name",
        "{mixer_track_index}  selected mixer track number",
        "{sounds}             channels in the rack, e.g. '40 Sounds'",
        "{patterns}           patterns in the project, e.g. '1 Pattern'",
        "{mixer_tracks}       mixer inserts in use, e.g. '12 Tracks'",
        "{effects}            effects on the selected insert",
        "{dot}                the separator from formatting.separator",
        "{time_signature}     e.g. 4/4",
        "{fl_version_full}    full version string reported by FL itself",
    ],
}


def _is_comment(key):
    return isinstance(key, str) and key.startswith("//")


def strip_comments(value):
    if isinstance(value, dict):
        return {k: strip_comments(v) for k, v in value.items()
                if not _is_comment(k)}
    if isinstance(value, list):
        return [strip_comments(v) for v in value]
    return value


def _merge(defaults, user):
    added = []
    out = copy.deepcopy(defaults)
    for key, value in user.items():
        if _is_comment(key):
            continue
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            sub, sub_added = _merge(out[key], value)
            out[key] = sub
            added.extend(key + "." + name for name in sub_added)
        else:
            out[key] = value
    for key in defaults:
        if _is_comment(key) or key in user:
            continue
        added.append(key)
    return out, added


def load(path=None, create=True):
    path = path or config_path()
    notes = []
    raw = {}
    existed = os.path.isfile(path)

    if existed:
        try:
            raw = _read(path)
            if not isinstance(raw, dict):
                raise ValueError("config root must be a mapping")
        except Exception as exc:
            notes.append("config unreadable (%s), falling back to defaults"
                         % exc)
            backup = path + ".broken"
            try:
                os.replace(path, backup)
                notes.append("broken config moved to " + backup)
            except OSError:
                pass
            raw = {}
            existed = False

    merged, added = _merge(defaults, raw)

    if create and existed and path.lower().endswith(".json"):
        converted = os.path.join(os.path.dirname(path), "config.yml")
        try:
            save(merged, converted)
            os.replace(path, path + ".converted")
            notes.append("config converted to " + converted)
            path = converted
            added = []
        except OSError as exc:
            notes.append("could not convert the config to YAML: %s" % exc)

    if create and (not existed or added):
        if existed:
            notes.append("added %d new option(s) to the config" % len(added))
        else:
            notes.append("created default config at " + path)
        try:
            save(merged, path)
        except OSError as exc:
            notes.append("could not write config: %s" % exc)
    return strip_comments(merged), path, notes


def _read(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()
    if path.lower().endswith(".json"):
        return json.loads(text)
    try:
        return yamlish.loads(text)
    except yamlish.YamlError:
        return json.loads(text)


def save(config, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        if path.lower().endswith(".json"):
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        else:
            handle.write(yamlish.dumps(config))
    os.replace(tmp, path)
