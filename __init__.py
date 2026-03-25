# -*- coding: utf-8 -*-
import os
import sys

# Ensure bundled dependencies (e.g. defusedxml) are importable without
# requiring the user to manually install anything.
_lib_path = os.path.join(os.path.dirname(__file__), "lib")
if _lib_path not in sys.path:
    sys.path.insert(0, _lib_path)


def classFactory(iface):
    from .dji_rtk_status import DJIRTKStatusPlugin
    return DJIRTKStatusPlugin(iface)
