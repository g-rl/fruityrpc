"""FruityRPC - detailed Discord Rich Presence for FL Studio.

Pure standard library: window detection through ctypes, Discord IPC through
the local named pipe. Nothing to pip install.
"""

from .app import app_version

__all__ = ["app_version"]
