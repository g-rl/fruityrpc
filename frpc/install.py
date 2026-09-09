import os
import re
import sys

from . import config as config_module

script_name = "device_FruityRPC.py"


def _package_root():
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return bundle
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_script():
    bundled = os.path.join(_package_root(), "fl-script", script_name)
    if os.path.isfile(bundled):
        return bundled
    return os.path.join(config_module.install_root(), "fl-script", script_name)


def hardware_dirs():
    candidates = []
    appdata = os.environ.get("APPDATA")
    userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")

    roots = []
    if appdata:
        roots.append(os.path.join(appdata, "Image-Line"))
    roots.append(os.path.join(userprofile, "Documents", "Image-Line"))
    roots.append(os.path.join(userprofile, "OneDrive", "Documents",
                              "Image-Line"))

    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            if not entry.lower().startswith(("fl studio", "fruity")):
                continue
            hardware = os.path.join(root, entry, "Settings", "Hardware")
            if os.path.isdir(hardware):
                candidates.append(hardware)
    return candidates


def installed_scripts():
    found = []
    for hardware in hardware_dirs():
        candidate = os.path.join(hardware, "FruityRPC", script_name)
        if os.path.isfile(candidate):
            found.append(candidate)
    return found


def midi_input_ports():
    try:
        import ctypes

        class MidiInCaps(ctypes.Structure):
            _fields_ = [("wMid", ctypes.c_ushort),
                        ("wPid", ctypes.c_ushort),
                        ("vDriverVersion", ctypes.c_uint),
                        ("szPname", ctypes.c_wchar * 32),
                        ("dwSupport", ctypes.c_uint)]

        winmm = ctypes.WinDLL("winmm")
        ports = []
        for index in range(winmm.midiInGetNumDevs()):
            caps = MidiInCaps()
            winmm.midiInGetDevCapsW(index, ctypes.byref(caps),
                                    ctypes.sizeof(caps))
            ports.append(caps.szPname)
        return ports
    except Exception:
        return []


def midi_bindings():
    try:
        import winreg
    except ImportError:
        return []

    found = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Image-Line")
    except OSError:
        return []

    with root:
        index = 0
        while True:
            try:
                product = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not product.lower().startswith("fl studio"):
                continue
            path = r"Software\Image-Line\%s\Devices\MIDI input" % product
            try:
                devices = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                continue
            with devices:
                position = 0
                while True:
                    try:
                        device = winreg.EnumKey(devices, position)
                    except OSError:
                        break
                    position += 1
                    try:
                        with winreg.OpenKey(devices, device) as key:
                            def value(name):
                                try:
                                    return winreg.QueryValueEx(key, name)[0]
                                except OSError:
                                    return ""
                            found.append({
                                "product": product,
                                "device": device,
                                "script": str(value("ScriptFolder")).strip(),
                                "enabled": str(value("Enabled")).strip() == "1",
                                "port": str(value("Port")).strip(),
                            })
                    except OSError:
                        continue
    return found


def script_source(state_dir=None):
    with open(source_script(), "r", encoding="utf-8") as handle:
        script = handle.read()
    state_dir = state_dir or config_module.config_dir()
    replacement = 'state_dir = r"%s"' % state_dir.replace('"', '')
    return re.sub(r'^state_dir = r".*"$', lambda _match: replacement,
                  script, count=1, flags=re.MULTILINE)


def ensure_script_installed():
    if not os.path.isfile(source_script()):
        return []
    try:
        script = script_source()
    except OSError:
        return []

    written = []
    for hardware in hardware_dirs():
        folder = os.path.join(hardware, "FruityRPC")
        destination = os.path.join(folder, script_name)
        try:
            current = ""
            if os.path.isfile(destination):
                with open(destination, "r", encoding="utf-8") as handle:
                    current = handle.read()
            if current == script:
                continue
            os.makedirs(folder, exist_ok=True)
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write(script)
            written.append(destination)
        except OSError:
            continue
    return written


def bindable_rows():
    available = {name.strip().lower() for name in midi_input_ports()}
    if not available:
        return []
    return [row for row in midi_bindings()
            if row["enabled"] and not row["script"]
            and row["device"].strip().lower() in available]


def primary_binding_rows():
    rows = bindable_rows()
    if not rows:
        return []

    order = [name.strip().lower() for name in midi_input_ports()]
    def rank(row):
        name = row["device"].strip().lower()
        return order.index(name) if name in order else len(order)

    primary = min(rows, key=rank)["device"]
    return [row for row in rows if row["device"] == primary]


def apply_binding(rows, folder="FruityRPC"):
    try:
        import winreg
    except ImportError:
        return []

    done = []
    for row in rows:
        path = (r"Software\Image-Line\%s\Devices\MIDI input\%s"
                % (row["product"], row["device"]))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "ScriptFolder", 0, winreg.REG_SZ,
                                  folder)
            done.append(row)
        except OSError:
            continue
    return done


def bind_midi_script(device_name=None, folder="FruityRPC"):
    if os.name != "nt":
        print("  registry access is windows only")
        return 1

    if _fl_is_running():
        print("")
        print("  fl studio is running. close it first - it overwrites these")
        print("  settings from memory when it exits.")
        print("")
        return 1

    rows = primary_binding_rows()
    if not rows:
        print("")
        if not midi_input_ports():
            print("  no midi input is connected, so there is nothing to")
            print("  attach the bridge to. plug a controller in, or install")
            print("  loopmidi and add one virtual port, then run this again.")
        else:
            print("  no free midi input found. every connected port is")
            print("  either disabled in fl studio or already running another")
            print("  controller script.")
        print("")
        return 1

    if device_name:
        wanted = [row for row in bindable_rows()
                  if row["device"].lower() == device_name.lower()]
        if not wanted:
            print("")
            print("  no free connected input called %r. available:"
                  % device_name)
            for row in bindable_rows():
                print("      %s  (%s)" % (row["device"], row["product"]))
            print("")
            return 1
        rows = wanted

    print("")
    print("  bound the bridge script")
    print("")
    changed = apply_binding(rows, folder)
    for row in changed:
        print("      %-14s %-22s was: %s"
              % (row["product"], row["device"], row["script"] or "(none)"))

    if not changed:
        print("      nothing was changed")
        print("")
        return 1

    print("")
    print("  start fl studio and check options > midi settings; the port")
    print("  should show fruityrpc as its controller type. to undo, set")
    print("  controller type back to (generic controller) there.")
    print("")
    return 0


def _fl_is_running():
    from .flwatch import FLWatcher
    from . import config as config_for_watch
    settings, _path, _notes = config_for_watch.load(create=False)
    return bool(FLWatcher(settings).poll().get("running"))


def install_midi_script():
    source = source_script()
    if not os.path.isfile(source):
        print("cannot find %s" % source)
        return 1

    script = script_source()

    targets = hardware_dirs()
    if not targets:
        print("")
        print("  no fl studio settings folder found. expected one of:")
        print("")
        print(r"      %appdata%\image-line\fl studio\settings\hardware")
        print(r"      %userprofile%\documents\image-line\fl studio\settings"
              r"\hardware")
        print("")
        print("  run fl studio once, then try again, or copy")
        print("  %s there by hand." % script_name)
        print("")
        return 1

    print("")
    print("  installed")
    print("")
    for hardware in targets:
        folder = os.path.join(hardware, "FruityRPC")
        os.makedirs(folder, exist_ok=True)
        destination = os.path.join(folder, script_name)
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write(script)
        print("      script  %s" % destination.lower())
    print("      state   %s" % config_module.state_path().lower())
    print("")
    print("  finish in fl studio")
    print("")
    print("      1.  options > midi settings")
    print("      2.  pick any input port and enable it")
    print("          (a loopmidi virtual port works, no hardware needed)")
    print("      3.  set controller type to  fruityrpc")
    print("      4.  restart fl studio")
    print("")
    print("  until that is done the presence still works, it just cannot")
    print("  show tempo, transport, sound and pattern counts.")
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(install_midi_script())
