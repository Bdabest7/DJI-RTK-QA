# DJI RTK QA (QGIS Plugin)

QA DJI RTK photo sets in QGIS — visualize Terra‑style route quality, per‑photo accuracy, and RTK status **before you leave the site**.

> Reads DJI **.RPT** (survey report), **.MRK** (timestamp/STD), and **image metadata (EXIF/XMP)** to create three analysis layers in QGIS.

---

## Table of Contents
- [Features](#features)
- [Why It’s Useful](#why-its-useful)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [How the Plugin Interprets Your Data](#how-the-plugin-interprets-your-data)
  - [Sources It Reads](#sources-it-reads)
  - [Symbology Logic](#symbology-logic)
  - [Fields](#fields)
- [Tips & Notes](#tips--notes)
- [Troubleshooting](#troubleshooting)
- [Supported DJI Platforms](#supported-dji-platforms)
- [Contributing](#contributing)
- [License](#license)
- [Changelog (excerpt)](#changelog-excerpt)
- [Screenshots](#screenshots)

---

## Features

Creates **three layers** automatically:

| Layer | Description | Styling |
|---|---|---|
| **DJI Route (RPT)** | Dense route from the **.RPT** file (DJI Lidar) | Colored by **Excellent / Good / Poor** based on RPT *summary* windows (e.g., RTK loss) |
| **DJI Photos (RTK)** | Image capture points | **Graduated by RMSE (3D)** from STDs — larger/warmer symbols indicate higher error |
| **DJI Flight Path (RTK)** | Lines connecting photos per flight | Categorized by **RTK Fix / Float / Single / No Position** |

Works across **multiple flights** stored under the same parent folder (recursive scan).

---

## Why It’s Useful

- **Quick field QA:** sanity‑check RTK quality and standard deviations across a mission.
- **Terra‑style visualization:** mirrors DJI Terra route coloring—no Terra export needed.
- **Find abnormal intervals:** see losses (e.g., RTK loss, too few systems, low satellites) directly on the map.

---

## Requirements

- **QGIS:** 3.22 or newer  
- **ExifTool (required):** path must be configured in the plugin **Settings…**  
  - **Windows:** download from <https://exiftool.org/> and select **exiftool.exe**  
  - **macOS/Linux:** `brew install exiftool` or `apt install libimage-exiftool-perl`, then select `exiftool`

> Note: The QGIS plugin repository does not allow bundling binaries, so ExifTool is not shipped. Use the **Settings** dialog to point the plugin to your local ExifTool.

---

## Installation

1. Download the plugin ZIP or install from **QGIS Plugin Manager** (when published).
2. Ensure the ZIP contains a single top‑level folder (e.g., `dji_rtk_status/`) with `__init__.py`, `metadata.txt`, etc.
3. In QGIS: **Plugins → Manage and Install Plugins… → Install from ZIP**.

---

## Quickstart

### 1) Configure ExifTool
**Plugins → DJI RTK QA → Settings… →** Browse to your ExifTool binary and **Save**.

### 2) Load a mission folder
**Plugins → DJI RTK QA → Add Layer from Folder… →** choose the parent folder that contains:

- **Images** (`.JPG`, `.DNG`, etc.)  
- **Optional mission files** (`.RPT`, `.MRK`) — the plugin searches **recursively** and supports **multiple flights** in the same directory

### 3) Review the layers
- **DJI Route (RPT):** styled by **Excellent / Good / Poor** (from RPT summary windows)  
- **DJI Photos (RTK):** points sized & colored by **RMSE (3D)**  
- **DJI Flight Path (RTK):** categorized by **RTK Fix / Float / Single / No Position**

---

## How the Plugin Interprets Your Data

### Sources It Reads

- **RPT (`*.RPT`)** — JSON containing:
  - `RTK_PATH_INFO_UNIT.RTK_DETAIL_INFO` → **dense route points**
  - `VISIBLE_CAM_INFO_UNIT.RTK_DETAIL_INFO` → **per‑capture points**
  - `RTB_INFO_UNIT` → **summary abnormal windows** (e.g., RTK loss), used to color **Good/Excellent/Poor**
- **MRK (`*.MRK`)** — Per‑capture records with **lat/lon/ellipsoid height** and **standard deviations (N/E/U)** + an **RTK flag**
- **EXIF/XMP (images)** — GPS, altitude, attitude, timestamps; used to place photos and fill any missing items  
  EXIF reads are accelerated via **ExifTool**.

### Symbology Logic

#### DJI Route (RPT)
- Color is driven **only** by **RPT summary windows** (Terra‑style).
- Default mapping:
  - **LOSS**, **FEW_SYS**, **LESS_SAT** → **Good**
  - **No abnormal window** → **Excellent**
- Attributes include:
  - `rpt_reason` (active window label)
  - `rtk_status` (from the downstream route point, for reference)

#### DJI Photos (RTK)
- Computes **RMSE (3D)** from standard deviations:  
  \[ RMSE\_{3D} = \sqrt{\sigma_N^2 + \sigma_E^2 + \sigma_U^2} \] (meters), displayed in **centimeters**
- Default bins: **≤ 3 cm**, **3–6 cm**, **≥ 6 cm** (larger + warmer symbol for larger RMSE)
- Fields also include raw STDs (`std_n_m`, `std_e_m`, `std_u_m`), RTK flag, and status.

#### DJI Flight Path (RTK)
- Connects photo points **per flight**
- Categorized by **RTK Fix / RTK Float / Single / No Position / Unknown** using MRK or EXIF/XMP flags

### Fields

**DJI Photos (RTK)**
- `file`, `time`, `flight_id`
- `rtk_flag`, `rtk_status`, `rtk_quality`
- `std_n_m`, `std_e_m`, `std_u_m`, `rmse_3d_cm`
- `abs_alt_m`, `rel_alt_m`, `yaw_deg`

**DJI Route (RPT)**
- `flight_id`
- `rpt_quality` (**Excellent / Good / Poor / Unknown**)
- `rpt_reason` (**LOSS**, **FEW_SYS**, **LESS_SAT**, or empty)
- `rtk_status` (informational)

---

## Tips & Notes

- The plugin **recursively** reads all `.RPT` / `.MRK` files and images under the selected folder; **multiple flights** are supported.
- Matching between **images** and **MRK** uses **nearest neighbor within 5 m** (configurable via `NEAR_MATCH_M`).
- EXIF reads are **chunked** (`EXIF_CHUNK=100`) to perform well on large sets.
- **Local‑only:** No data leaves your machine; everything is read locally.

---

## Troubleshooting

- **Nothing happens after selecting a folder**  
  Ensure ExifTool is set in **Settings…** and reachable (the plugin validates with `exiftool -ver`).

- **No “DJI Route (RPT)” layer**  
  The folder didn’t contain any `.RPT` files. To date, only DJI L1/L2 have produced .RTP files.

- **Photos show “Unknown” / missing STDs**  
  Some drones/firmware omit STDs in EXIF; ensure `.MRK` files are present. The plugin prefers MRK values when available.

- **Colors/thresholds differ from your standards**  
  Tweak these constants in `dji_rtk_status.py`:
  - `RPT_SUMMARY_MAP` (how abnormal windows map to Good/Poor)
  - `FIX_*` / `FLT_*` thresholds (STD gates)
  - `RMSE_BINS_CM`, `POINT_SIZES` (point symbology)
  - `NEAR_MATCH_M` (match radius)

---

## Supported DJI Platforms

Tested with **Mavic 3 Enterprise** , **Phantom 4 RTK** , **Matrice 300 P1/L2** data. Other DJI aircraft that produce the same `.RPT` / `.MRK` formats should work—please open an issue if you hit edge cases.

---

## Contributing

PRs welcome! Helpful additions include:
- Extra abnormal keys from `RTB_INFO_UNIT` found in the wild
- More robust MRK parsers for variant formats
- UI polish, legends, and report export

Please file bugs/feature requests with a **small sample** (a redacted `.RPT` / `.MRK` plus ~10 photos helps a ton).

---

## License

**GPL‑3.0‑or‑later** (see `LICENSE`).  
This plugin calls **ExifTool** externally (Perl Artistic License); users install/configure ExifTool separately and accept its license.

---

## Changelog (excerpt)

### 1.0
- RPT route layer with **summary‑window coloring** (Terra‑style)
- Photo points styled by **RMSE (3D)**; flight path categorized by **RTK status**
- Recursive multi‑flight loading; optional ExifTool path via **Settings…**

---

## Screenshots
DJI RTK QA: <img width="1885" height="1206" alt="DJI RTK QA" src="https://github.com/user-attachments/assets/d30d2139-c2e3-4f86-a1a0-90206dd836d8" />
**vs**
DJI Terra: <img width="2057" height="1175" alt="Terra" src="https://github.com/user-attachments/assets/c3ff8f2e-cc23-43a5-85f1-eef3b4b87938" />


