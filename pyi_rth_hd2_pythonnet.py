"""Configure pythonnet before pywebview imports clr in a frozen bundle."""
import os
import sys

if sys.platform == "win32":
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    exe_dir = os.path.dirname(sys.executable)
    dll_name = f"python{sys.version_info[0]}{sys.version_info[1]}.dll"
    runtime_dir = os.path.join(base, "pythonnet", "runtime")
    os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")

    candidates = [os.path.join(base, dll_name), os.path.join(exe_dir, dll_name)]
    for root, _dirs, files in os.walk(base):
        if dll_name in files:
            candidates.append(os.path.join(root, dll_name))
            break

    def unblock(path):
        try:
            os.remove(path + ":Zone.Identifier")
        except OSError:
            pass

    for candidate in candidates:
        if os.path.isfile(candidate):
            os.environ["PYTHONNET_PYDLL"] = candidate
            unblock(candidate)
            break

    runtime_dll = os.path.join(runtime_dir, "Python.Runtime.dll")
    if os.path.isfile(runtime_dll):
        unblock(runtime_dll)

    search_dirs = [base, exe_dir, runtime_dir]
    for path in search_dirs:
        if os.path.isdir(path):
            os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(path)
                except OSError:
                    pass
