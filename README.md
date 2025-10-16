DJI RTK QA (QGIS Plugin)

QA DJI RTK photo sets in QGIS — visualize Terra-style route quality, per-photo accuracy, and RTK status before you leave the site.

Reads DJI .RPT (survey report), .MRK (timestamp/STD), and image metadata (EXIF/XMP).

Creates three layers:

DJI Route (RPT) — the dense flight route from the RPT file, colored by Excellent / Good / Poor using the RPT “summary” windows (e.g., RTK loss).

DJI Photos (RTK) — image capture points, styled by RMSE (3D) from STDs (larger + warmer colors = higher error).

DJI Flight Path (RTK) — lines connecting photos per flight, categorized by RTK Fix / Float / Single / No Position.

Works across multiple flights stored in the same folder (recursive scan).

Why this is useful

Quick field QA: make sure RTK quality and standard deviations look right across the mission.

Visual check that matches DJI Terra route coloring, without exporting from Terra.

Spot abnormal intervals (loss of RTK, too few systems, low satellites) directly on the map.

Requirements

QGIS: 3.22 or newer.

ExifTool (required): path must be configured in the plugin Settings….

Windows: download from https://exiftool.org/
 and select exiftool.exe.

macOS/Linux: brew install exiftool / apt install libimage-exiftool-perl, then select exiftool.

The official QGIS plugin repository does not allow bundling binaries, so exiftool is not shipped inside the plugin. Use the Settings dialog to point to it.

Installation

Download the plugin ZIP or install from the QGIS Plugin Manager (when published).

Ensure the zip contains a single top-level folder (e.g., dji_rtk_status/) with __init__.py, metadata.txt, etc.

In QGIS: Plugins → Manage and Install Plugins… → Install from ZIP.

Quickstart

Configure ExifTool
Plugins → DJI RTK QA → Settings… → Browse to exiftool and Save.

Load a mission folder
Plugins → DJI RTK QA → Add Layer from Folder… → choose the parent folder that contains:

Images (.JPG, .DNG, etc.)

Optional mission files (.RPT, .MRK) — the plugin will search recursively and can handle multiple flights in the same directory.

Review the layers

DJI Route (RPT) — styled by Excellent / Good / Poor from the RPT summary windows.

DJI Photos (RTK) — points sized & colored by RMSE (3D).

DJI Flight Path (RTK) — categorized by RTK Fix / Float / Single / No Position.

How the plugin interprets your data
Sources it reads

RPT (*.RPT): JSON that includes

RTK_PATH_INFO_UNIT.RTK_DETAIL_INFO → dense route points.

VISIBLE_CAM_INFO_UNIT.RTK_DETAIL_INFO → per-capture points.

RTB_INFO_UNIT → summary abnormal windows (e.g., RTK loss), used to color “Good/Excellent/Poor”.

MRK (*.MRK): Per-capture records with lat/lon/ellipsoid height and standard deviations (N/E/U) + an RTK flag.

EXIF/XMP (images): GPS, altitude, attitude, timestamps; used to place photos and fill any missing items. EXIF reads are accelerated via ExifTool.

Symbology logic

DJI Route (RPT)

Color is driven only by RPT summary windows (Terra-style).

Mapping (defaults):

LOSS, FEW_SYS, LESS_SAT → Good

No abnormal window → Excellent

Attributes include rpt_reason (the active window) and rtk_status (from the downstream route point) for reference.

DJI Photos (RTK)

Computes RMSE (3D) from standard deviations:

RMSE
3
𝐷
=
𝜎
𝑁
2
+
𝜎
𝐸
2
+
𝜎
𝑈
2
 
(
m
)
,
displayed in cm
RMSE
3D
	​

=
σ
N
2
	​

+σ
E
2
	​

+σ
U
2
	​

	​

 (m),displayed in cm

Default bins: ≤ 3 cm, 3–6 cm, ≥ 6 cm (larger + warmer symbol for larger RMSE).

Fields also include raw STDs (std_n_m, std_e_m, std_u_m), RTK flag, and status.

DJI Flight Path (RTK)

Connects photo points per flight.

Categorized by RTK Fix / RTK Float / Single / No Position / Unknown using MRK or EXIF/XMP flags.

Fields (selected)

DJI Photos (RTK)

file, time, flight_id

rtk_flag, rtk_status, rtk_quality

std_n_m, std_e_m, std_u_m, rmse_3d_cm

abs_alt_m, rel_alt_m, yaw_deg

DJI Route (RPT)

flight_id

rpt_quality (Excellent/Good/Poor/Unknown)

rpt_reason (LOSS, FEW_SYS, LESS_SAT or empty)

rtk_status (informational)

Tips & notes

The plugin recursively reads all .RPT/.MRK files and images under the chosen folder; multiple flights in a single directory are supported.

Matching between images and MRK rows uses nearest neighbor within 5 m (configurable in code via NEAR_MATCH_M).

EXIF reads are chunked (EXIF_CHUNK=100) to stay snappy on large sets.

No data leaves your machine; everything is read locally.

Troubleshooting

Nothing happens after selecting a folder
Make sure ExifTool is set in Settings… and is reachable (we call exiftool -ver to validate).

No “DJI Route (RPT)” layer
The folder didn’t contain any .RPT files (or they lack route info). Photos and flight path still load.

Photos show “Unknown” / missing STDs
Some drones/firmware omit STDs in EXIF; ensure .MRK files are present. We prefer MRK values when available.

Colors/thresholds differ from your standards
Adjust these constants in dji_rtk_status.py:

RPT_SUMMARY_MAP (how abnormal windows map to Good/Poor)

FIX_* / FLT_* thresholds (STD gates)

RMSE_BINS_CM, POINT_SIZES (point symbology)

NEAR_MATCH_M (match radius)

Supported DJI platforms

Tested with Mavic 3 Enterprise and Phantom 4 RTK data. Other DJI aircraft that produce the same .RPT/.MRK formats should work, but please open an issue if you hit edge cases.

Contributing

PRs welcome! Helpful additions include:

Extra abnormal keys from RTB_INFO_UNIT found in the wild

More robust MRK parsers for variant formats

UI polish, legends, report export

Please file bugs / feature requests with a small sample (a redacted .RPT/.MRK plus ~10 photos helps a ton).

License

GPL-3.0-or-later (see LICENSE).
This plugin calls ExifTool externally (Perl Artistic License); users install/configure ExifTool separately and accept its license.

Changelog (excerpt)

0.9.0

RPT route layer with summary-window coloring (Terra-style).

Photo points styled by RMSE (3D); flight path categorized by RTK status.

Recursive multi-flight loading; optional ExifTool path via Settings….

Screenshots

Add a couple of PNGs here (Layers panel + map view).
