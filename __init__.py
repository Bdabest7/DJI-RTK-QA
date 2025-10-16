# -*- coding: utf-8 -*-
def classFactory(iface):
    from .dji_rtk_status import DJIRTKStatusPlugin
    return DJIRTKStatusPlugin(iface)
