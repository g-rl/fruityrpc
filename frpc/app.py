import argparse
import ctypes
import json
import logging
import logging.handlers
import os
import signal
import sys
import time

from . import config as config_module
from . import flwatch
from .assets import AssetCatalog
from .flp import ProjectFacts
from .ipc import PresenceLink
from .midi_state import MidiState
from .presence import PresenceBuilder

app_version = "1.0.2"


def setup_logging(level_name, path, to_console):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logger = logging.getLogger("fruityrpc")
    logger.setLevel(level)
    logger.handlers[:] = []

    formatter = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                  "%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass

    if to_console:
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger


_mutex = None


def claim_single_instance():
    global _mutex
    if sys.platform != "win32":
        return True
    error_already_exists = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                      ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\FruityRPC.singleton")
    return ctypes.get_last_error() != error_already_exists


class FruityRPC(object):
    def __init__(self, config_file=None, force_debug=False, console=True):
        self.config_file = config_file or config_module.config_path()
        self.force_debug = force_debug
        self.console = console
        self._config_mtime = 0.0
        self._running = True
        self._fl_gone_since = None
        self._last_status = None
        self._last_push = 0.0
        self._bridge_checked = 0.0
        self._bridge_warned = False

        self.config, self.config_file, notes = config_module.load(
            self.config_file)
        self.log = setup_logging(
            "debug" if force_debug else self.config.get("log_level", "info"),
            config_module.log_path(), console)
        for note in notes:
            self.log.info(note)
        self._config_mtime = self._mtime(self.config_file)

        self.watcher = flwatch.FLWatcher(self.config, self.log)
        self.catalog = AssetCatalog(
            self.config.get("client_id", ""),
            os.path.join(config_module.config_dir(), "assets.json"), self.log)
        self.catalog.refresh(force=True)
        self.builder = PresenceBuilder(self.config, self.log, self.catalog)
        self.facts = ProjectFacts(
            self.log,
            self.config.get("detection", {}).get(
                "project_search_paths", []))
        self.midi = MidiState(
            config_module.state_path(),
            self.config.get("detection", {}).get("midi_state_max_age", 6.0),
            self.log)
        self.link = PresenceLink(
            self.config.get("client_id", ""), self.log,
            self.config.get("reconnect_interval", 15.0))


    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def maybe_reload_config(self):
        mtime = self._mtime(self.config_file)
        if not mtime or mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            new_config, _path, _notes = config_module.load(self.config_file,
                                                           create=False)
        except Exception as exc:
            self.log.warning("config reload failed: %s" % exc)
            return
        old_client = self.config.get("client_id")
        self.config = new_config
        self.watcher.reconfigure(new_config)
        self.facts.search_paths = list(
            new_config.get("detection", {}).get(
                "project_search_paths", []) or [])
        self.builder.reconfigure(new_config)
        self.midi.reconfigure(
            config_module.state_path(),
            new_config.get("detection", {}).get("midi_state_max_age", 6.0))
        if not self.force_debug:
            self.log = setup_logging(new_config.get("log_level", "info"),
                                     config_module.log_path(), self.console)
        if new_config.get("client_id") != old_client:
            self.log.info("client_id changed, reconnecting")
            self.catalog = AssetCatalog(
                new_config.get("client_id", ""),
                os.path.join(config_module.config_dir(), "assets.json"),
                self.log)
            self.catalog.refresh(force=True)
            self.builder.catalog = self.catalog
            self.link.close()
            self.link = PresenceLink(
                new_config.get("client_id", ""), self.log,
                new_config.get("reconnect_interval", 15.0))
        self.log.info("config reloaded")


    def maintain_bridge(self, snapshot, deep_mode):
        detection = self.config.get("detection", {})
        now = time.time()
        if now - self._bridge_checked < 20.0:
            return
        self._bridge_checked = now

        from . import install as installer

        if detection.get("auto_install_midi_script", True):
            for path in installer.ensure_script_installed():
                self.log.info("installed the FL bridge script: %s" % path)

        if deep_mode or snapshot.get("running"):
            return
        if not detection.get("auto_bind_midi_script", True):
            return
        if not installer.installed_scripts():
            return

        rows = installer.primary_binding_rows()
        if not rows:
            if not self._bridge_warned:
                self._bridge_warned = True
                if not installer.midi_input_ports():
                    self.log.info("deep mode idle: no MIDI input connected, "
                                  "so the bridge has nothing to attach to")
                else:
                    self.log.info("deep mode idle: every connected MIDI input "
                                  "is disabled in FL or already scripted")
            return
        self._bridge_warned = False
        for row in installer.apply_binding(rows):
            self.log.info("attached the bridge to %s in %s - it starts with "
                          "FL Studio from now on"
                          % (row["device"], row["product"]))

    def tick(self):
        snapshot = self.watcher.poll()
        midi = {}
        if self.config.get("detection", {}).get("use_midi_state", True):
            midi = self.midi.read() or {}
            if snapshot.get("running") is False and midi:
                midi = {}

        deep_mode = bool(midi)
        if not midi and self.config.get("detection", {}).get(
                "use_flp_file", True):
            midi = self.facts.read(snapshot.get("project"))

        self.maintain_bridge(snapshot, deep_mode)

        activity, status, _variables = self.builder.build(snapshot, midi)

        if status != self._last_status:
            self.log.info("status: %s%s" % (
                status,
                (" - " + (snapshot.get("project") or "no project"))
                if snapshot.get("running") else ""))
            self._last_status = status

        min_interval = max(5.0, float(self.config.get("min_update_interval",
                                                      5.0)))
        now = time.time()
        if now - self._last_push >= min_interval or activity is None:
            if self.link.update(activity):
                self._last_push = now
        else:
            self.link.flush()

        self._handle_fl_exit(snapshot)
        return snapshot, status

    def _handle_fl_exit(self, snapshot):
        if snapshot.get("running"):
            self._fl_gone_since = None
            return
        if self._fl_gone_since is None:
            self._fl_gone_since = time.time()
        if self.config.get("when_fl_closed") == "exit":
            self.log.info("FL Studio closed, exiting")
            self._running = False
            return
        timeout = self.config.get("exit_after_fl_closed_seconds", 0) or 0
        if timeout and time.time() - self._fl_gone_since >= timeout:
            self.log.info("FL Studio closed for %ds, exiting" % timeout)
            self._running = False


    def run(self):
        if not self.config.get("client_id"):
            self.log.error(
                "no client_id in %s - create a Discord application at "
                "https://discord.com/developers/applications and paste its "
                "Application ID into the config." % self.config_file)
            return 2

        self.log.info("FruityRPC %s starting (pid %d)" % (app_version,
                                                          os.getpid()))
        self._install_signal_handlers()
        interval = max(0.25, float(self.config.get("poll_interval", 1.0)))
        try:
            while self._running:
                try:
                    self.maybe_reload_config()
                    self.tick()
                except Exception as exc:
                    self.log.exception("unexpected error: %s" % exc)
                    time.sleep(2.0)
                interval = max(0.25, float(self.config.get("poll_interval",
                                                           1.0)))
                time.sleep(interval)
        finally:
            self.shutdown()
        return 0

    def _install_signal_handlers(self):
        def stop(_signum, _frame):
            self._running = False

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            handler = getattr(signal, name, None)
            if handler is not None:
                try:
                    signal.signal(handler, stop)
                except (ValueError, OSError):
                    pass

    def shutdown(self):
        self.log.info("shutting down")
        try:
            self.link.close()
        except Exception:
            pass


def diagnose(app):
    snapshot = app.watcher.poll()
    midi = app.midi.read() or {}
    if not midi:
        midi = app.facts.read(snapshot.get("project"))
        if not midi:
            app.facts.wait()
            midi = app.facts.read(snapshot.get("project"))
    activity, status, variables = app.builder.build(snapshot, midi)

    print("FruityRPC %s - diagnostics" % app_version)
    print("config file : %s" % app.config_file)
    print("client_id   : %s" % (app.config.get("client_id") or "(not set!)"))
    print("state file  : %s%s" % (config_module.state_path(),
                                  "" if midi else "  (deep mode off)"))
    print("")
    if not snapshot.get("running"):
        print("FL Studio   : not detected")
    else:
        print("FL Studio   : %s (pid %s) %s"
              % (snapshot.get("exe"), snapshot.get("pid"),
                 snapshot.get("bitness")))
        print("window title: %r" % snapshot.get("main_title"))
        print("project     : %r%s" % (snapshot.get("project"),
                                      " (unsaved)" if snapshot.get("unsaved")
                                      else ""))
        print("version     : %s  (exe %s)" % (snapshot.get("version"),
                                              snapshot.get("version_full")))
        print("focused     : %r  [class %s]"
              % (snapshot.get("focused_window"),
                 snapshot.get("focused_class") or "-"))
        print("foreground  : %s" % snapshot.get("foreground"))
        print("idle        : %.0fs" % snapshot.get("idle_seconds", 0))
        windows = snapshot.get("windows") or []
        print("open windows: %d" % len(windows))
        for entry in (snapshot.get("window_details") or [])[:25]:
            print("   - %-40s [%s]" % (entry.get("title"), entry.get("class")))
    print("")
    facts = app.facts.read(snapshot.get("project")) if snapshot.get(
        "running") else {}
    if facts:
        print("project file: %s" % (app.facts._path or "-"))
        print("   sounds       %s" % facts.get("channel_count"))
        print("   patterns     %s" % facts.get("pattern_count"))
        print("   tempo        %s" % facts.get("tempo"))
        print("   arrangement  %s" % facts.get("arrangement"))
        print("   saved by     build %s" % facts.get("fl_build"))
    elif snapshot.get("running"):
        print("project file: not found on disk (unsaved project?)")
    print("")
    print("deep mode   : %s" % ("yes" if midi else "no"))
    if midi:
        for key in sorted(midi):
            print("   %-16s %s" % (key, midi[key]))
    else:
        from .install import (installed_scripts, midi_input_ports,
                              midi_bindings)
        scripts = installed_scripts()
        ports = midi_input_ports()
        bindings = [row for row in midi_bindings()
                    if row["enabled"] or row["script"]]
        if scripts:
            for path in scripts:
                print("   script       %s" % path)
        else:
            print("   script       not installed - run "
                  "FruityRPC.bat --install-midi-script")
        print("   state file   missing, so FL is not running the script yet")
        if ports:
            print("   midi inputs  %s" % ", ".join(ports))
            if bindings:
                print("   fl bindings")
                for row in bindings:
                    print("      %-14s %-22s controller type: %s"
                          % (row["product"], row["device"],
                             row["script"] or "(none)"))
            if not any(row["script"] for row in bindings):
                print("")
                print("   FL has no controller script bound to any port, so")
                print("   the bridge is never started.")
                print("")
                print("   FL Studio > Options > MIDI settings:")
                print("     1. click the input row so it is highlighted")
                print("     2. tick Enable for it")
                print("     3. open the Controller type dropdown at the")
                print("        bottom of that panel and choose FruityRPC")
                print("     4. close FL Studio - it writes the setting on")
                print("        exit - then start it again")
                print("")
                print("   Notes still pass through; the script never")
                print("   consumes MIDI.")
        else:
            print("   midi inputs  none on this machine")
            print("")
            print("   FL can only run a controller script that is bound to a")
            print("   port. Install loopMIDI, add one virtual port, then bind")
            print("   it in FL Studio > Options > MIDI settings.")
        print("")
        print("   without it, sound/pattern counts and tempo still come")
        print("   from the saved .flp file; only live transport (play,")
        print("   record, bar:beat) and mixer counts need the bridge.")
    print("")
    known = app.catalog.names
    print("art assets  : %s"
          % (", ".join(sorted(known)) if known else
             "could not be read from Discord"))
    print("icon        : %s" % (activity or {}).get(
        "assets", {}).get("large_image", "-"))
    print("")
    print("status      : %s" % status)
    print("activity    :")
    print(json.dumps(activity, indent=2, ensure_ascii=False))

    if app.config.get("client_id"):
        print("")
        print("discord     : connecting...")
        if app.link.ensure_connection():
            print("              connected as %s"
                  % (app.link.user_name or "unknown"))
            if activity:
                app.link.update(activity, force=True)
                print("              test presence pushed - check your "
                      "Discord profile")
                time.sleep(8)
            app.link.close()
        else:
            print("              could not reach Discord (is it running?)")
    return 0


def _ask(question, default="n"):
    try:
        answer = input("%s [%s] " % (question, "Y/n" if default == "y"
                                     else "y/N")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default == "y"
    if not answer:
        return default == "y"
    return answer.startswith("y")


def wizard(app):
    root = config_module.install_root()
    launcher = os.path.join(root, "FruityRPC.exe")
    if not os.path.isfile(launcher):
        launcher = os.path.join(root, "FruityRPC-Silent.vbs")

    line = "-" * 64

    print("")
    print(line)
    print("  fruityrpc %s  -  setup" % app_version)
    print(line)
    print("")
    print("      config   %s" % app.config_file.lower())
    print("      log      %s" % config_module.log_path().lower())
    print("")

    if not app.config.get("client_id"):
        print("  step 1  -  discord application")
        print("")
        print("      1.  open  https://discord.com/developers/applications")
        print("      2.  new application, named exactly what discord should")
        print("          show, e.g.  fl studio")
        print("      3.  rich presence > art assets > add image, upload the")
        print("          files from the assets folder, keeping their names")
        print("          (fl_logo, play, record, edit, export, afk)")
        print("      4.  general information > copy the application id")
        print("")
        try:
            client_id = input("      paste the application id here: ").strip()
        except (EOFError, KeyboardInterrupt):
            client_id = ""
        print("")
        if client_id.isdigit() and len(client_id) >= 17:
            app.config["client_id"] = client_id
            stored, _path, _notes = config_module.load(app.config_file,
                                                       create=False)
            stored["client_id"] = client_id
            config_module.save(stored, app.config_file)
            print("      saved")
        else:
            print("      skipped, set client_id in the config before starting")
        print("")

    print("  step 2  -  deep mode  (optional, recommended)")
    print("")
    print("      installs a small midi script inside fl studio so the")
    print("      presence can show tempo, transport, bar:beat, pattern,")
    print("      channel and sound counts. it sends no midi and changes")
    print("      nothing in your projects.")
    print("")
    if _ask("      install the fl studio bridge script now?", "y"):
        from .install import install_midi_script
        install_midi_script()
    else:
        print("")

    print("  step 3  -  add it to fl studio")
    print("")
    print("      1.  options > general settings > external tools")
    print("      2.  click the folder icon on an empty row and pick")
    print("")
    print("              %s" % launcher.lower())
    print("")
    print("      3.  name it  discord rpc")
    print("      4.  tick  launch at startup  on the right")
    print("")

    print("  step 4  -  customise")
    print("")
    print("      every line, image, button and count lives in")
    print("")
    print("          %s" % app.config_file.lower())
    print("")
    print("      it is re-read while fruityrpc runs, so edits apply within")
    print("      a few seconds. no restart needed.")
    print("")
    print(line)
    print("")

    if _ask("  open the config file now?", "n"):
        try:
            os.startfile(app.config_file)
        except OSError as exc:
            print("      could not open it: %s" % exc)
    if _ask("  run a live test against discord now?", "y"):
        print("")
        diagnose(app)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="FruityRPC",
        description="Detailed Discord Rich Presence for FL Studio.")
    parser.add_argument("--config", help="path to an alternate config.json")
    parser.add_argument("--setup", action="store_true",
                        help="interactive first-run setup")
    parser.add_argument("--diagnose", action="store_true",
                        help="print what FruityRPC can see and exit")
    parser.add_argument("--once", action="store_true",
                        help="push a single presence update and exit")
    parser.add_argument("--debug", action="store_true",
                        help="verbose logging to the console")
    parser.add_argument("--no-single-instance", action="store_true",
                        help="allow more than one copy to run")
    parser.add_argument("--bind-midi-script", nargs="?", const=True,
                        metavar="DEVICE",
                        help="attach the bridge script to a midi input "
                             "without using fl's menus (fl must be closed); "
                             "name a device to pick one")
    parser.add_argument("--install-midi-script", action="store_true",
                        help="copy the FL MIDI script into FL Studio's "
                             "Hardware folder (enables deep mode)")
    parser.add_argument("--open-config", action="store_true",
                        help="open config.json in the default editor")
    parser.add_argument("--version", action="version",
                        version="FruityRPC " + app_version)
    args, extra = parser.parse_known_args(argv)

    console = bool(args.debug or args.diagnose or args.setup)
    if not console:
        try:
            console = sys.stdout is not None and sys.stdout.isatty()
        except Exception:
            console = False

    if args.open_config:
        path = args.config or config_module.config_path()
        config_module.load(path)
        os.startfile(path)
        return 0

    if args.install_midi_script:
        from .install import install_midi_script
        return install_midi_script()

    if args.bind_midi_script:
        from .install import bind_midi_script
        device = None if args.bind_midi_script is True             else args.bind_midi_script
        return bind_midi_script(device)

    app = FruityRPC(args.config, args.debug, console)
    if extra:
        app.log.debug("ignoring extra arguments from the launcher: %r" % extra)

    if args.setup:
        return wizard(app)

    if args.diagnose:
        return diagnose(app)

    if args.once:
        app.tick()
        time.sleep(1.0)
        app.shutdown()
        return 0

    if app.config.get("single_instance", True) and not args.no_single_instance:
        if not claim_single_instance():
            app.log.info("another FruityRPC is already running, exiting")
            return 0

    return app.run()
