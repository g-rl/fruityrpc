"""FruityRPC entry point.

Run directly (``py -3 fruityrpc.py``) or through the launchers next to it:
``FruityRPC.bat`` (visible console) and ``FruityRPC-Silent.vbs`` (no window,
the one to point FL Studio's External tools at).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frpc.app import main

if __name__ == "__main__":
    sys.exit(main())
