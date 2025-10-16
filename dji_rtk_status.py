# -*- coding: utf-8 -*-
"""
DJI RTK QA — Refactored
-----------------------
This is a refactor of the original single-file QGIS plugin script with the same
behavior, organized into small, testable components with type hints, dataclasses,
and clearer separation of concerns.

Key improvements:
- Added missing Settings dialog and `_ensure_exiftool()` logic
- Extracted ExifTool handling into a helper class
- Introduced dataclasses for MRK / RPT / Photo records
- Centralized quality logic, constants, and utilities
- Safer error handling and user feedback
- Clearer layer builders with self-documenting code
"""

from __future__ import annotations

import os
import re
import json
import math
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime

# --- QGIS / PyQt ---
from qgis.PyQt.QtWidgets import (
    QAction,
    QFileDialog,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
)
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtCore import QVariant, QSettings
from qgis.core import (
    QgsProject,
    QgsPointXY,
    QgsGeometry,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsVectorLayer,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    Qgis,
)

# For graduated rendering
from qgis.core import QgsGraduatedSymbolRenderer, QgsRendererRange, QgsSymbol


# ==============================================================================
# Settings / Constants
# ==============================================================================

TIME_GAP_MIN = 6         # fallback grouping for photos without MRK (kept for parity)
EXIF_CHUNK = 100         # exiftool batch size
NEAR_MATCH_M = 5.0       # photo/RPT point <-> MRK row nearest match (meters)

# QSettings key for storing the ExifTool path (per user/profile)
EXIFTOOL_SETTINGS_KEY = "dji_rtk_status/exiftool_path"

# --- Quality thresholds (meters) when STDs exist ---
FIX_EXCELLENT_U = 0.05
FIX_EXCELLENT_NE = 0.03
FIX_GOOD_U = 0.15
FIX_GOOD_NE = 0.08
FLT_GOOD_U = 0.30
FLT_GOOD_NE = 0.20

# --- RPT summary → quality mapping (Terra-like) ---
RPT_SUMMARY_MAP = {
    "LOSS": "Good",   # configurable; change to "Poor" if you prefer
    "FEW_SYS": "Good",
    "LESS_SAT": "Good",
}
RPT_SUMMARY_DEFAULT = "Excellent"

# --- RMSE bins for photo points (in centimeters) ---
RMSE_BINS_CM: List[Tuple[float, float]] = [(0, 3), (3, 6), (6, 9999)]
POINT_SIZES = [2.6, 3.4, 4.2]

# --- XMP / DJI namespaces & regex ---
XMP_RE = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.DOTALL)
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "drone-dji": "http://www.dji.com/drone-dji/1.0/",
}

# --- RPT rows ---
MRK_ROW_RE = re.compile(
    r"([-\d\.]+),La.*?\t([-\d\.]+),Lon\t([-\d\.]+),Ellh\t([-\d\.]+),\s*([-\d\.]+),\s*([-\d\.]+)\t(\d+),Q"
)


# ==============================================================================
# Dataclasses
# ==============================================================================

@dataclass
class MRKEntry:
    lat: float
    lon: float
    ellh: float
    std_n: float
    std_e: float
    std_u: float
    flag: Optional[int]
    flight_id: str
    dir: str


@dataclass
class RPTPoint:
    lat: float
    lon: float
    height: Optional[float]
    ts: Optional[int]
    flag: Optional[int]
    flight_id: str


@dataclass
class RPTEvent:
    start: int
    end: int
    kind: str  # e.g., "LOSS", "FEW_SYS", "LESS_SAT"


@dataclass
class PhotoRecord:
    file: str
    lat: float
    lon: float
    capture_time: Optional[str] = None
    abs_alt: Optional[float] = None
    rel_alt: Optional[float] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None
    rtk_flag: Optional[int] = None
    std_n_m: Optional[float] = None
    std_e_m: Optional[float] = None
    std_u_m: Optional[float] = None
    flight_id: Optional[str] = None


# ==============================================================================
# Tiny utils
# ==============================================================================

def npath(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def relid(root: str, path: str) -> str:
    r = os.path.relpath(path, root).replace("\\", "/")
    return "." if r in (".", "") else r


def parse_exif_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip().replace("Z", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def rmse3d_cm(n: float, e: float, u: float) -> float:
    return (n * n + e * e + u * u) ** 0.5 * 100.0


def safe_get(d: Dict, *keys: str):
    """
    Robust getter for exiftool JSON keys (case-insensitive, skips NaN/None/empty).
    """
    for k in keys:
        if k in d and d[k] not in (None, "", "NaN"):
            return d[k]
    lower = {kk.lower(): vv for kk, vv in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] not in (None, "", "NaN"):
            return lower[k.lower()]
    return None


# ==============================================================================
# XMP helpers
# ==============================================================================

def extract_xmp_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            data = f.read()
        m = XMP_RE.search(data)
        return m.group(0) if m else None
    except Exception:
        return None


def _xmp_get_text(node: ET.Element, tag: str) -> Optional[str]:
    el = node.find(f".//{{{NS['drone-dji']}}}{tag}")
    return el.text.strip() if el is not None and el.text is not None else None


def parse_dji_xmp(path: str) -> Dict:
    """
    Fallback parser for DJI XMP when exiftool keys are missing.
    Returns a small dict of fields similar to the exiftool output naming.
    """
    out = {"file": os.path.basename(path)}
    xmp = extract_xmp_bytes(path)
    if not xmp:
        return out
    try:
        root = ET.fromstring(xmp)
        descs = root.findall(".//rdf:Description", NS)
        if not descs:
            return out
        d = descs[0]

        def fnum(v):
            try:
                return float(v)
            except Exception:
                return float("nan")

        def fint(v):
            try:
                return int(float(v))
            except Exception:
                return None

        out["lat"] = fnum(_xmp_get_text(d, "GpsLatitude"))
        out["lon"] = fnum(_xmp_get_text(d, "GpsLongitude"))
        out["abs_alt"] = fnum(_xmp_get_text(d, "AbsoluteAltitude"))
        out["rel_alt"] = fnum(_xmp_get_text(d, "RelativeAltitude"))
        out["yaw"] = fnum(_xmp_get_text(d, "FlightYawDegree") or _xmp_get_text(d, "GimbalYawDegree"))
        out["pitch"] = fnum(_xmp_get_text(d, "FlightPitchDegree") or _xmp_get_text(d, "GimbalPitchDegree"))
        out["roll"] = fnum(_xmp_get_text(d, "FlightRollDegree") or _xmp_get_text(d, "GimbalRollDegree"))
        out["rtk_flag"] = fint(_xmp_get_text(d, "RtkFlag"))
        out["rtk_std_lat"] = fnum(_xmp_get_text(d, "RtkStdLat"))
        out["rtk_std_lon"] = fnum(_xmp_get_text(d, "RtkStdLon"))
        out["rtk_std_hgt"] = fnum(_xmp_get_text(d, "RtkStdHgt"))
        out["capture_time"] = _xmp_get_text(d, "CreateDate") or None
        return out
    except Exception:
        return out


# ==============================================================================
# ExifTool handling
# ==============================================================================

def _hide_proc_kwargs() -> Dict:
    kw = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw["startupinfo"] = si
        # CREATE_NO_WINDOW
        kw["creationflags"] = 0x08000000
    return kw


def _validate_exiftool(path: Optional[str]) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        proc = subprocess.run([path, "-ver"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, **_hide_proc_kwargs())
        return proc.returncode == 0
    except Exception:
        return False


def _settings_exiftool_path() -> Optional[str]:
    p = QSettings().value(EXIFTOOL_SETTINGS_KEY, "", type=str)
    return p if p else None


def _set_settings_exiftool_path(p: Optional[str]) -> None:
    QSettings().setValue(EXIFTOOL_SETTINGS_KEY, p or "")


def _find_exiftool() -> Optional[str]:
    """Resolve exiftool path in this order: user setting → PATH → common Windows locations."""
    # 1) User setting
    user_path = _settings_exiftool_path()
    if user_path and _validate_exiftool(user_path):
        return user_path

    # 2) PATH
    p = shutil.which("exiftool")
    if p and _validate_exiftool(p):
        return p

    # 3) Common Windows installs
    if os.name == "nt":
        for c in [
            r"C:\Program Files\exiftool\exiftool.exe",
            r"C:\Program Files (x86)\exiftool\exiftool.exe",
            r"C:\Windows\exiftool.exe",
        ]:
            if os.path.isfile(c) and _validate_exiftool(c):
                return c

    return None


class ExifTool:
    """Small helper to read JSON in batches via exiftool."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _find_exiftool()

    def ensure(self) -> bool:
        """Try settings/path/auto-find; update settings on success."""
        if self.path and _validate_exiftool(self.path):
            return True
        candidate = _find_exiftool()
        if candidate:
            self.path = candidate
            _set_settings_exiftool_path(candidate)
            return True
        return False

    def batch_read(self, files: List[str], chunk: int = EXIF_CHUNK) -> Dict[str, Dict]:
        results: Dict[str, Dict] = {}
        if not self.path or not files:
            return results
        kwargs = _hide_proc_kwargs()
        for i in range(0, len(files), chunk):
            ch = files[i : i + chunk]
            try:
                proc = subprocess.run(
                    [self.path, "-j", "-n", "-fast2"] + ch,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    **kwargs,
                )
                arr = json.loads(proc.stdout.decode("utf-8", errors="ignore"))
                for j in arr:
                    src = j.get("SourceFile") or j.get("FileName") or ""
                    results[npath(src)] = j
            except Exception:
                # ignore this chunk; continue
                continue
        return results


# ==============================================================================
# Parsing: MRK / RPT
# ==============================================================================

def normalize_flag(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        v = int(v)
        if v in (50, 5, 4):
            return 50
        if v in (34, 3, 2):
            return 34
        if v in (16, 1):
            return 16
        if v == 0:
            return 0
        return None
    s = str(v).lower()
    if "fix" in s:
        return 50
    if "float" in s:
        return 34
    if "single" in s or "standalone" in s:
        return 16
    if "none" in s or "invalid" in s:
        return 0
    return None


def parse_mrk_recursive(root: str) -> List[MRKEntry]:
    out: List[MRKEntry] = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if not fn.lower().endswith(".mrk"):
                continue
            fid = f"MRK:{relid(root, dp)}/{os.path.splitext(fn)[0]}".replace("//", "/")
            try:
                with open(os.path.join(dp, fn), "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = MRK_ROW_RE.search(line)
                        if not m:
                            continue
                        lat = float(m.group(1))
                        lon = float(m.group(2))
                        ellh = float(m.group(3))
                        std_n = float(m.group(4))
                        std_e = float(m.group(5))
                        std_u = float(m.group(6))
                        flag = normalize_flag(int(m.group(7)))
                        out.append(
                            MRKEntry(
                                lat=lat,
                                lon=lon,
                                ellh=ellh,
                                std_n=std_n,
                                std_e=std_e,
                                std_u=std_u,
                                flag=flag,
                                flight_id=fid,
                                dir=dp,
                            )
                        )
            except Exception:
                continue
    return out


def parse_rpt_recursive(root: str) -> Tuple[List[RPTPoint], List[RPTPoint], Dict[str, List[RPTEvent]]]:
    """
    Returns:
      - route_pts: list of dense route points
      - shot_pts: list of per-capture points
      - events_by_fid: mapping flight_id → list of summary abnormal windows
    """
    route_pts: List[RPTPoint] = []
    shot_pts: List[RPTPoint] = []
    events_by_fid: Dict[str, List[RPTEvent]] = defaultdict(list)

    for dp, _, fns in os.walk(root):
        for fn in fns:
            if not fn.lower().endswith(".rpt"):
                continue
            rpt_path = os.path.join(dp, fn)
            fid = f"RPT:{relid(root, dp)}/{os.path.splitext(fn)[0]}".replace("//", "/")
            try:
                with open(rpt_path, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                sroot = data.get("SURVEYING_REPORT_ROOT", {})

                # Route (dense)
                rtk_path = (sroot.get("RTK_PATH_INFO_UNIT") or {}).get("RTK_DETAIL_INFO") or []
                for rec in rtk_path:
                    route_pts.append(
                        RPTPoint(
                            lat=rec.get("LATITUDE"),
                            lon=rec.get("LONGITUDE"),
                            height=rec.get("HEIGHT"),
                            ts=rec.get("TIME_STAMP"),
                            flag=normalize_flag(rec.get("RTK_STATUS")),
                            flight_id=fid,
                        )
                    )

                # Per-capture
                vis = (sroot.get("VISIBLE_CAM_INFO_UNIT") or {}).get("RTK_DETAIL_INFO") or []
                for rec in vis:
                    shot_pts.append(
                        RPTPoint(
                            lat=rec.get("LATITUDE"),
                            lon=rec.get("LONGITUDE"),
                            height=rec.get("HEIGHT"),
                            ts=rec.get("TIME_STAMP"),
                            flag=normalize_flag(rec.get("RTK_STATUS")),
                            flight_id=fid,
                        )
                    )

                # Summary windows — RTB_INFO_UNIT
                rtb = (sroot.get("RTB_INFO_UNIT") or {})

                def add_intervals(key: str, kind: str) -> None:
                    arr = rtb.get(key) or []
                    for it in arr:
                        st = it.get("START_TIME")
                        en = it.get("END_TIME")
                        if isinstance(st, (int, float)) and isinstance(en, (int, float)):
                            events_by_fid[fid].append(RPTEvent(start=int(st), end=int(en), kind=kind))

                add_intervals("RTB_LOSS_ABNORMAL_DURATION", "LOSS")
                add_intervals("RTB_TOO_FEW_SYSTEMS_ABNORMAL_DURATION", "FEW_SYS")
                add_intervals("RTB_SATELLITE_ABNORMAL_DURATION", "LESS_SAT")

            except Exception:
                continue

    return route_pts, shot_pts, events_by_fid


# ==============================================================================
# Quality mapping
# ==============================================================================

def rtk_flag_to_status(flag: Optional[int], std_n: Optional[float] = None, std_e: Optional[float] = None, std_u: Optional[float] = None) -> Tuple[str, str]:
    """Return (status, quality). If stds are present, apply thresholds for finer grading."""
    base = {50: ("RTK Fix", "Excellent"), 34: ("RTK Float", "Good"), 16: ("Single", "Poor"), 0: ("No Position", "Poor")}.get(
        flag, ("Unknown", "Unknown")
    )
    if std_u is None and std_n is None and std_e is None:
        return base

    ne = max(std_n or 0.0, std_e or 0.0)
    u = std_u if std_u is not None else 9e9

    if flag == 50:
        if u <= FIX_EXCELLENT_U and ne <= FIX_EXCELLENT_NE:
            return ("RTK Fix", "Excellent")
        if u <= FIX_GOOD_U and ne <= FIX_GOOD_NE:
            return ("RTK Fix", "Good")
        return ("RTK Fix", "Poor")

    if flag == 34:
        if u <= FLT_GOOD_U and ne <= FLT_GOOD_NE:
            return ("RTK Float", "Good")
        return ("RTK Float", "Poor")

    return base


# ==============================================================================
# Vector layer builders
# ==============================================================================

def _mk_fields(spec: List[Tuple[str, QVariant]]) -> QgsFields:
    fields = QgsFields()
    for name, typ in spec:
        fields.append(QgsField(name, typ))
    return fields


def build_photo_layers(records: List[PhotoRecord]) -> Tuple[QgsVectorLayer, QgsVectorLayer]:
    """Build photo point layer and a polyline 'flight path' layer from records."""
    # --- Points layer ---
    vl = QgsVectorLayer("Point?crs=EPSG:4326", "DJI Photos (RTK)", "memory")
    pr = vl.dataProvider()

    fields = _mk_fields(
        [
            ("file", QVariant.String),
            ("time", QVariant.String),
            ("flight_id", QVariant.String),
            ("rtk_flag", QVariant.Int),
            ("rtk_status", QVariant.String),
            ("rtk_quality", QVariant.String),
            ("std_n_m", QVariant.Double),
            ("std_e_m", QVariant.Double),
            ("std_u_m", QVariant.Double),
            ("rmse_3d_cm", QVariant.Double),
            ("abs_alt_m", QVariant.Double),
            ("rel_alt_m", QVariant.Double),
            ("yaw_deg", QVariant.Double),
        ]
    )
    vl.startEditing()
    pr.addAttributes(fields)
    vl.updateFields()

    feats: List[QgsFeature] = []
    for r in records:
        if r.lat is None or r.lon is None:
            continue

        status, qual = rtk_flag_to_status(r.rtk_flag, r.std_n_m, r.std_e_m, r.std_u_m)
        n = r.std_n_m or 0.0
        e = r.std_e_m or 0.0
        u = r.std_u_m or 0.0
        rmse_cm = rmse3d_cm(n, e, u)

        f = QgsFeature()
        f.setFields(fields)
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon, r.lat)))
        f["file"] = r.file
        f["time"] = r.capture_time or ""
        f["flight_id"] = r.flight_id or "."
        f["rtk_flag"] = r.rtk_flag
        f["rtk_status"] = status
        f["rtk_quality"] = qual
        f["std_n_m"] = r.std_n_m
        f["std_e_m"] = r.std_e_m
        f["std_u_m"] = r.std_u_m
        f["rmse_3d_cm"] = rmse_cm
        f["abs_alt_m"] = r.abs_alt
        f["rel_alt_m"] = r.rel_alt
        f["yaw_deg"] = r.yaw
        feats.append(f)

    pr.addFeatures(feats)
    vl.commitChanges()

    # --- graduated renderer by RMSE (color + size change) ---
    ranges: List[QgsRendererRange] = []
    colors = [QColor(0, 153, 0), QColor(255, 165, 0), QColor(204, 0, 0)]
    for i, (lo, hi) in enumerate(RMSE_BINS_CM):
        sym = QgsMarkerSymbol.createSimple({"name": "circle", "size": str(POINT_SIZES[i])})
        sym.setColor(colors[i])
        label = f"≤ {int(hi)} cm" if i == 0 else (f"{int(lo)}–{int(hi)} cm" if i == 1 else f"≥ {int(lo)} cm")
        ranges.append(QgsRendererRange(lo, hi, sym, label))

    renderer = QgsGraduatedSymbolRenderer("rmse_3d_cm", ranges)
    renderer.setMode(QgsGraduatedSymbolRenderer.Custom)
    vl.setRenderer(renderer)

    # --- Flight path line layer ---
    ll = QgsVectorLayer("LineString?crs=EPSG:4326", "DJI Flight Path (RTK)", "memory")
    lpr = ll.dataProvider()
    lfields = _mk_fields(
        [
            ("flight_id", QVariant.String),
            ("from", QVariant.String),
            ("to", QVariant.String),
            ("rtk_status", QVariant.String),
            ("rtk_quality", QVariant.String),
        ]
    )
    ll.startEditing()
    lpr.addAttributes(lfields)
    ll.updateFields()

    def ptime(t: Optional[str]) -> Optional[datetime]:
        return parse_exif_dt(t)

    groups: Dict[str, List[PhotoRecord]] = defaultdict(list)
    for r in records:
        groups[r.flight_id or "."].append(r)

    for fid, recs in groups.items():
        recs = sorted(
            recs,
            key=lambda r: (ptime(r.capture_time) is None, ptime(r.capture_time) or datetime.min, r.file or ""),
        )
        for i in range(1, len(recs)):
            a, b = recs[i - 1], recs[i]
            if None in (a.lat, a.lon, b.lat, b.lon):
                continue
            status, qual = rtk_flag_to_status(b.rtk_flag, b.std_n_m, b.std_e_m, b.std_u_m)
            feat = QgsFeature()
            feat.setFields(lfields)
            feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(a.lon, a.lat), QgsPointXY(b.lon, b.lat)]))
            feat["flight_id"] = fid
            feat["from"] = a.file
            feat["to"] = b.file
            feat["rtk_status"] = status
            feat["rtk_quality"] = qual
            lpr.addFeatures([feat])

    ll.commitChanges()

    lcats: List[QgsRendererCategory] = []
    base = QgsLineSymbol.createSimple({"width": "0.9"})
    for label, color in [
        ("RTK Fix", QColor(0, 153, 0)),
        ("RTK Float", QColor(255, 165, 0)),
        ("Single", QColor(204, 0, 0)),
        ("No Position", QColor(128, 0, 0)),
        ("RTK Abnormal", QColor(128, 0, 128)),
        ("Unknown", QColor(150, 150, 150)),
    ]:
        s = base.clone()
        s.setColor(color)
        lcats.append(QgsRendererCategory(label, s, label))
    ll.setRenderer(QgsCategorizedSymbolRenderer("rtk_status", lcats))

    return vl, ll


def build_rpt_route_layer(route_pts: List[RPTPoint], events_by_fid: Dict[str, List[RPTEvent]]) -> QgsVectorLayer:
    """Route layer from .RPT; quality comes only from RPT summary windows."""

    def reason_for_ts(fid: str, ts: Optional[int]) -> Optional[str]:
        evs = events_by_fid.get(fid) or []
        if ts is None:
            return None
        try:
            t = int(ts)
        except Exception:
            return None
        for ev in evs:
            if ev.start <= t <= ev.end:
                return ev.kind
        return None

    # line layer
    ll = QgsVectorLayer("LineString?crs=EPSG:4326", "DJI Route (RPT)", "memory")
    lpr = ll.dataProvider()
    fields = _mk_fields(
        [
            ("flight_id", QVariant.String),
            ("rpt_quality", QVariant.String),
            ("rpt_reason", QVariant.String),
            ("rtk_status", QVariant.String),
        ]
    )
    ll.startEditing()
    lpr.addAttributes(fields)
    ll.updateFields()

    groups: Dict[str, List[RPTPoint]] = defaultdict(list)
    for p in route_pts:
        groups[p.flight_id].append(p)

    for fid, pts in groups.items():
        pts = sorted(pts, key=lambda x: (x.ts is None, x.ts or 0))
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            if None in (a.lat, a.lon, b.lat, b.lon):
                continue

            reason = reason_for_ts(fid, b.ts)
            qual = RPT_SUMMARY_MAP.get(reason, RPT_SUMMARY_DEFAULT)
            status, _ = rtk_flag_to_status(b.flag)

            feat = QgsFeature()
            feat.setFields(fields)
            feat.setGeometry(
                QgsGeometry.fromPolylineXY([QgsPointXY(a.lon, a.lat), QgsPointXY(b.lon, b.lat)])
            )
            feat["flight_id"] = fid
            feat["rpt_quality"] = qual
            feat["rpt_reason"] = reason or ""
            feat["rtk_status"] = status
            lpr.addFeatures([feat])

    ll.commitChanges()

    cats: List[QgsRendererCategory] = []
    base = QgsLineSymbol.createSimple({"width": "1.1"})
    for label, color in [
        ("Excellent", QColor(0, 153, 0)),
        ("Good", QColor(255, 165, 0)),
        ("Poor", QColor(204, 0, 0)),
        ("Unknown", QColor(150, 150, 150)),
    ]:
        s = base.clone()
        s.setColor(color)
        cats.append(QgsRendererCategory(label, s, label))
    ll.setRenderer(QgsCategorizedSymbolRenderer("rpt_quality", cats))
    return ll


# ==============================================================================
# Plugin UI: Settings dialog
# ==============================================================================

class ExifToolSettingsDialog(QDialog):
    def __init__(self, parent=None, current_path: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DJI RTK QA — Settings")
        self.path_edit = QLineEdit(self)
        if current_path:
            self.path_edit.setText(current_path)

        browse_btn = QPushButton("Browse…", self)
        save_btn = QPushButton("Save", self)
        cancel_btn = QPushButton("Cancel", self)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("ExifTool path:", self))
        row.addWidget(self.path_edit)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        browse_btn.clicked.connect(self._on_browse)
        save_btn.clicked.connect(self._on_save)
        cancel_btn.clicked.connect(self.reject)

    def _on_browse(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Select exiftool executable", "", "Executables (*)")
        if fn:
            self.path_edit.setText(fn)

    def _on_save(self) -> None:
        path = self.path_edit.text().strip()
        if not _validate_exiftool(path):
            QMessageBox.critical(self, "Invalid Path", "The selected ExifTool path is invalid.")
            return
        _set_settings_exiftool_path(path)
        self.accept()


# ==============================================================================
# QGIS Plugin
# ==============================================================================

class DJIRTKStatusPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action: Optional[QAction] = None
        self.settingsAction: Optional[QAction] = None
        self.exiftool = ExifTool()  # auto-resolve

    # --- QGIS hooks ---
    def initGui(self):
        self.action = QAction(QIcon(), "Add Layer from Folder…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("DJI RTK QA", self.action)

        self.settingsAction = QAction(QIcon(), "Settings…", self.iface.mainWindow())
        self.settingsAction.triggered.connect(self.show_settings)
        self.iface.addPluginToMenu("DJI RTK QA", self.settingsAction)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("DJI RTK QA", self.action)
        if self.settingsAction:
            self.iface.removePluginMenu("DJI RTK QA", self.settingsAction)

    # --- UI actions ---
    def show_settings(self):
        dlg = ExifToolSettingsDialog(self.iface.mainWindow(), current_path=self.exiftool.path)
        if dlg.exec_() == QDialog.Accepted:
            # Reload from settings and re-ensure
            self.exiftool.path = _settings_exiftool_path()
            self.exiftool.ensure()
            self.iface.messageBar().pushMessage(
                "DJI RTK QA", "Settings saved.", level=Qgis.Success, duration=4
            )

    def _ensure_exiftool(self) -> bool:
        """Ensure exiftool is configured and valid; prompt user otherwise."""
        if self.exiftool.ensure():
            return True

        # Prompt user to set it
        self.iface.messageBar().pushMessage(
            "DJI RTK QA",
            "ExifTool is not configured. Please set the path via 'Settings…'.",
            level=Qgis.Critical,
            duration=8,
        )
        self.show_settings()
        return bool(self.exiftool.path and _validate_exiftool(self.exiftool.path))

    # --- Main workflow ---
    def run(self):
        root = QFileDialog.getExistingDirectory(self.iface.mainWindow(), "Select DJI Folder", "")
        if not root:
            return

        # Require exiftool for full functionality
        if not self._ensure_exiftool():
            self.iface.messageBar().pushMessage(
                "DJI RTK QA",
                "ExifTool is not configured. Set the path via 'Settings…' to enable full functionality.",
                level=Qgis.Critical,
                duration=8,
            )
            return

        # Collect images recursively
        img_exts = {".jpg", ".jpeg", ".tif", ".tiff", ".dng"}
        images: List[str] = []
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in img_exts:
                    images.append(os.path.join(dp, fn))

        if not images:
            self.iface.messageBar().pushMessage("DJI RTK Status", "No images found.", level=Qgis.Warning, duration=5)
            return

        # Parse MRK & RPT
        mrk_entries = parse_mrk_recursive(root)
        rpt_route_pts, rpt_shot_pts, rpt_events = parse_rpt_recursive(root)

        # Read EXIF via exiftool
        exif_json = self.exiftool.batch_read(images, chunk=EXIF_CHUNK) if self.exiftool.path else {}

        # Build per-photo records
        records: List[PhotoRecord] = []
        for p in images:
            key = npath(p)
            j = exif_json.get(key, {})

            r = PhotoRecord(
                file=os.path.basename(p),
                lat=safe_get(j, "GPSLatitude"),
                lon=safe_get(j, "GPSLongitude"),
                abs_alt=safe_get(j, "AbsoluteAltitude"),
                rel_alt=safe_get(j, "RelativeAltitude"),
                yaw=safe_get(j, "FlightYawDegree", "GimbalYawDegree"),
                pitch=safe_get(j, "FlightPitchDegree", "GimbalPitchDegree"),
                roll=safe_get(j, "FlightRollDegree", "GimbalRollDegree"),
                capture_time=safe_get(j, "CreateDate", "DateTimeOriginal"),
            )

            # XMP fallback if lat/lon missing
            if r.lat is None or r.lon is None:
                rx = parse_dji_xmp(p)
                r.lat = r.lat if r.lat is not None else rx.get("lat")
                r.lon = r.lon if r.lon is not None else rx.get("lon")
                for k in ("abs_alt", "rel_alt", "yaw", "pitch", "roll", "capture_time"):
                    if getattr(r, k) is None and rx.get(k) is not None:
                        setattr(r, k, rx.get(k))

            if r.lat is None or r.lon is None:
                continue

            # Attach nearest MRK to fill STDs/flag + flight_id
            best: Optional[MRKEntry] = None
            bestd = 1e9
            for e in mrk_entries:
                d = haversine_m(r.lat, r.lon, e.lat, e.lon)
                if d < bestd:
                    bestd = d
                    best = e
                    if bestd <= NEAR_MATCH_M and bestd < 0.5:  # early exit if essentially on top
                        break

            if best and bestd <= NEAR_MATCH_M:
                r.rtk_flag = best.flag
                r.std_n_m = best.std_n
                r.std_e_m = best.std_e
                r.std_u_m = best.std_u
                r.flight_id = best.flight_id
            else:
                r.rtk_flag = normalize_flag(safe_get(j, "RTKFlag", "RtkFlag", "RTKStatus", "RtkStatus"))
                r.std_n_m = safe_get(j, "RTKStdLat", "RtkStdLat")
                r.std_e_m = safe_get(j, "RTKStdLon", "RtkStdLon")
                r.std_u_m = safe_get(j, "RTKStdHgt", "RtkStdHgt")
                r.flight_id = None

            records.append(r)

        if not records and not rpt_route_pts:
            self.iface.messageBar().pushMessage(
                "DJI RTK Status", "No usable metadata found.", level=Qgis.Warning, duration=6
            )
            return

        # Add layers
        pts, photo_line = build_photo_layers(records)
        QgsProject.instance().addMapLayer(photo_line)
        QgsProject.instance().addMapLayer(pts)

        if rpt_route_pts:
            rpt_line = build_rpt_route_layer(rpt_route_pts, rpt_events)
            QgsProject.instance().addMapLayer(rpt_line)

        flights = len(set([(r.flight_id or ".") for r in records]))
        msg = f"Loaded {len(records)} photos across {flights} flight(s)."
        if rpt_route_pts:
            msg += " Added Terra-style route with summary overrides."
        self.iface.messageBar().pushMessage("DJI RTK Status", msg, level=Qgis.Success, duration=6)
