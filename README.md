# EpochLoom Testing Dashboard

**NASA Space Apps Challenge 2026 · Challenge: _Planet X and SPHEREx_ · Team EpochLoom · KFUPM, Dhahran, Saudi Arabia (14–15 November 2026)**

> [!IMPORTANT]
> **TESTING REPOSITORY.** This repository contains a testing and experimentation dashboard built to support the testing and evaluation of Team EpochLoom's project during NASA Space Apps Challenge 2026.
>
> **This is NOT the final submitted project.**
>
> The final project developed and presented by Team EpochLoom is available at: **[https://epochloom.space](https://epochloom.space)**

---

## 1. Purpose of this repository

During the hackathon I built this dashboard as a separate test bench. It was used to:

- check which NASA data services (IRSA TAP, IRSA Gator, the AWS Open Data copy of SPHEREx, IRSA HiPS mirrors) were actually reachable from our network, and how they behaved under load and outages;
- try out retrieval, photometry, astrometry and visualisation ideas on real SPHEREx data alongside the team's main implementation;
- test the challenge's target objects (Barnard's Star, ε Indi B, WISE 0855−0714, RW Aurigae, R Draconis) against authoritative catalogues.

It is a working tool in its own right, but it is a **supporting testbed**, not the team's submission.

## 2. The problem

NASA's SPHEREx telescope maps the whole sky in 102 infrared spectral channels (0.75–5.0 µm) and revisits each position over time. The challenge asks for an accessible public tool that shows SPHEREx images over time and helps people spot **moving or variable objects**.

## 3. What the dashboard does

"Implemented" means the feature is present in the code. Verification status and the tests performed are documented separately in §8. "Limited" means the feature has known caveats described in §9.

| Feature | Status |
|---|---|
| Archive search: every SPHEREx image covering a sky position (IRSA TAP), with a hard timeout and a disk-cache fallback when IRSA is down | Implemented |
| Cutouts from the AWS Open Data copy of SPHEREx, falling back to IRSA | Implemented |
| Blink / timelapse viewer per band: play, speed, timeline slider, observation date per frame, shared per-band brightness stretch so real brightness changes stay visible | Implemented |
| Proper-motion correction: each frame is cut and measured at the target's position propagated to that frame's date (`astropy` `apply_space_motion`) | Implemented |
| Catalogue-backed targets: positions, proper motions and parallaxes from Gaia DR3 or CatWISE2020 via IRSA, with the source shown | Implemented |
| Motion track on the sky map: observed catalogue epochs (2MASS, AllWISE, CatWISE2020, Gaia DR3) as a solid line, propagated positions as a dashed line, plus a table | Implemented |
| SPHEREx centroids: measured position of the target in each SPHEREx frame, added to the motion track | Implemented |
| Difference imaging: each frame minus the median of other frames **within ±3 % in wavelength** (SPHEREx is a linear variable filter); refuses to subtract with fewer than 3 references | Implemented |
| Aperture photometry and spectrum (per-pixel wavelength from each image's `WCS-WAVE` table), CSV export | Implemented |
| Interactive sky map (Aladin Lite 3.8.2, served locally) with DSS2, 2MASS, AllWISE, SDSS and a SPHEREx DR1 preview layer; Gaia DR3 catalogue overlay | Implemented / Limited |
| Contamination-flag badges per frame, WiseView link, WebM export of the timelapse, object info card | Implemented |

## 4. Architecture

```
Browser (web/index.html, vanilla JS, Aladin Lite)
        │  HTTP (same origin)
        ▼
FastAPI backend (server.py)
   ├── photometry.py   aperture photometry + centroids on SPHEREx cutouts
   ├── astrometry.py   proper-motion propagation (astropy)
   ├── catalogs.py     IRSA Gator cone searches (Gaia DR3, CatWISE2020, AllWISE, 2MASS), cached
   └── diff.py         resampling onto a sky-fixed grid + wavelength-matched differences
        │
        ├── IRSA TAP  (SPHEREx image metadata)
        ├── AWS S3    nasa-irsa-spherex (byte-range reads of only the needed pixels)
        ├── IRSA image service (cutouts, fallback)
        ├── IRSA Gator (catalogues)
        └── IRSA HiPS mirrors (sky-map tiles)
```

No account, API key or credential is needed. All data is public.

## 5. Data sources

| Source | Used for |
|---|---|
| [IRSA SPHEREx archive](https://irsa.ipac.caltech.edu/Missions/spherex.html) (TAP: `spherex.artifact`, `spherex.plane`) | finding the images that cover a position |
| [AWS Open Data: SPHEREx](https://registry.opendata.aws/spherex-qr/) (`nasa-irsa-spherex`) | cutouts (first choice) |
| IRSA image cutout service | cutouts (fallback) |
| Gaia DR3, CatWISE2020, AllWISE, 2MASS PSC via IRSA Gator | astrometry, motion-track epochs, catalogue overlay |
| IRSA HiPS mirrors (DSS2, 2MASS, AllWISE, SDSS, SPHEREx DR1) | sky-map tiles |

CDS services (alasky, SIMBAD) were unreachable from the hackathon network, so IRSA mirrors are used throughout. This project is not affiliated with, endorsed or certified by NASA, Caltech-IPAC or ESA.

## 6. Install and run

Requires Python 3.11+ and internet access.

```bash
git clone https://github.com/eh7r/epochloom-testing-dashboard.git
cd epochloom-testing-dashboard
python3 -m venv .venv
source .venv/bin/activate          # fish: source .venv/bin/activate.fish
pip install -r requirements.txt
uvicorn server:app --port 8000
```

Open <http://127.0.0.1:8000/>. Interactive API docs: <http://127.0.0.1:8000/docs>.

`aiohttp` is not needed (it had no Python 3.14 build); the server switches fsspec to its `requests`-based backend automatically. In my testing, `--reload` could hang during shutdown, so it is best avoided for demos. Keep the default host (`127.0.0.1`); read §10 before using `--host 0.0.0.0`.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `SPHEREX_SOURCES` | `s3,irsa` | order of cutout sources |
| `SPHEREX_S3_BASE` | AWS bucket URL | base URL of the public AWS copy |
| `SPHEREX_CACHE_MAX_GB` | `2` | disk limit for `cache/` |
| `SPHEREX_TAP_TIMEOUT` | `40` | seconds before an IRSA TAP query gives up |

## 7. API

| Endpoint | Returns |
|---|---|
| `GET /observations?ra&dec&limit` | SPHEREx images covering a position |
| `GET /cutout?url&ra&dec&size[&pmra&pmdec&epoch&mjd&lo&hi]` | PNG cutout (sky-subtracted), target pixel and stretch in headers |
| `GET /photometry?url&ra&dec&…` | flux (µJy), error, S/N, wavelength, flags |
| `GET /centroid?url&ra&dec&…` | measured position of the target in one frame |
| `POST /diffstack` · `GET /diff?id&url` | wavelength-matched difference images |
| `GET /astrometry?target` | catalogue astrometry for a preset target |
| `GET /motion?…` | observed catalogue epochs and computed path |
| `GET /catalog?ra&dec&…` | Gaia DR3 sources around a position |
| `GET /hips/spherex/...` | SPHEREx HiPS preview tiles (redirect to IRSA) |
| `GET /health` | server status |

## 8. Testing and verification

The repository has **no automated test suite yet**. Two separate sets of checks exist, and they should not be confused.

### 8.1 Current audit — 20 November 2026

Run against this repository's code in a sandbox with **Python 3.10.12 and astropy 6.1.7** (not the Python 3.14 / astropy 8 environment used during development). Live access to IRSA and AWS was blocked in that sandbox, so only offline behaviour and cached data could be checked.

| Check | Result |
|---|---|
| `GET /health`, `GET /`, `GET /docs` | 200 |
| `/cutout` with a URL on another domain | 400 (rejected) |
| `/cutout` with `..` in the archive path | 400 (rejected) |
| `/cutout` with `ra=999` | 422 (rejected) |
| `/diff?id=../../etc` | 400 (rejected) |
| `/hips/spherex/../../x` | 404 (rejected) |
| `/astrometry` for the five targets | 200, served from the disk cache after the live IRSA query failed |
| Barnard's Star astrometry returned (Gaia DR3) | pmRA −801.551, pmDec 10362.394 mas/yr |
| Obvious hard-coded secrets or API keys in the inspected project files | none found |
| Bundled `web/vendor/aladin.js` | byte-identical to `dist/aladin.js` in the npm package `aladin-lite@3.8.2` |

Not re-checked in this audit (they need live IRSA/AWS): archive search, cutouts, photometry, motion track, centroids, difference imaging, WebM export, the browser UI. No dependency vulnerability scan (e.g. `pip-audit`) was run.

### 8.2 Historical results — 15 November 2026 (hackathon)

Recorded on the development machine during the hackathon (Python 3.14). They are kept here as historical records; they were **not** repeated in the 20 November audit and are not independently verified.

| Check | Recorded result |
|---|---|
| Test star (RA 304.69253, Dec 42.45383): archive search, cutouts, spectrum | 24/24 frames, 24/24 photometry points |
| Barnard's Star motion track | 10.39″/yr |
| ε Indi B: median SPHEREx centroid vs Gaia DR3 propagated to frame dates | 0.67″ |
| RW Aurigae, D2 difference imaging | 33 of 35 frames had ≥3 wavelength-matched references |
| WebM export | VP9, 512×512, 12 frames (checked with `ffprobe`) |
| Aladin right-drag brightness/contrast blocked; pan and zoom still work | observed in headless Brave only; other browsers were not tested |

## 9. Known limitations

- Photometric errors are statistical only: no aperture correction, no deblending, no sigma-clipping. A known outlier appears near 3.17 µm in D4.
- Bright stars (e.g. Barnard's Star) saturate SPHEREx in most frames; those frames are rejected rather than measured.
- SPHEREx has a native pixel scale of approximately 6.15 arcseconds per pixel, so the images look coarse at high zoom and do not provide finer spatial detail simply by enlarging them.
- For the targets examined, the expected parallax displacement is below 1 arcsecond, smaller than a native SPHEREx pixel, and is not resolved in the SPHEREx images.
- The SPHEREx map layer uses IRSA's 2D PNG preview tiles; their wavelength is not stated in the tile metadata.
- The first load of a new target is slow (seconds per frame); results are cached afterwards.
- Pages served over HTTPS cannot call this HTTP backend (mixed content); it is meant to run locally.
- `requirements.txt` does not pin versions.

## 10. Security notes

This is a local research tool. It has not had a security audit or penetration test.

**`POST /diffstack` load.** One request may list up to 300 SPHEREx files. Each one can trigger a remote download (AWS or IRSA) and a resampling step, all inside that single request. A few such requests can tie up the server's worker threads, network bandwidth and disk. The cache cap (`SPHEREX_CACHE_MAX_GB`) is enforced only when new cutouts are downloaded, so difference-image files written by `/diffstack` can push `cache/` above the cap until the next cutout.

**CORS.** The API sends `Access-Control-Allow-Origin: *` and allows `GET` and `POST`. This was chosen so a dashboard served from one machine can use a teammate's backend (`?api=`). The wildcard CORS policy permits cross-origin browser access. Depending on browser security restrictions, another website may be able to send requests to the local API while the server is running, potentially triggering resource-intensive operations such as `/diffstack`. The API does not use cookies or authentication credentials, but this does not prevent resource-exhaustion requests.

**Input validation tested so far.** Cutout and photometry URLs must match an allow-list pattern for SPHEREx archive files on IRSA; difference-image IDs and HiPS tile paths are regex-checked; coordinates are range-checked; text placed into the page is HTML-escaped. The cases in §8.1 show these checks rejecting specific bad inputs. They do **not** prove the absence of all SSRF, path-traversal or injection issues.

**Recommended mitigations** (none applied yet; none needs an architecture change):

1. Run on `127.0.0.1` only (the default). Use `--host 0.0.0.0` only on a trusted network, and stop the server afterwards.
2. Lower the `/diffstack` limit (for example 60 files) and cap the number of `/diffstack` requests running at once.
3. Restrict `allow_origins` to the dashboard's own origin(s) unless the `?api=` teammate mode is needed.
4. Run `_trim_cache()` after `/diffstack` writes its files as well.
5. Pin dependency versions and run `pip-audit`.
6. Add automated tests for the validation rules above.

## 11. Technologies

Python, FastAPI, Uvicorn, Astropy, NumPy, Matplotlib, PyVO, fsspec, Requests; vanilla JavaScript, HTML/CSS; Aladin Lite 3.8.2.

## 12. My contribution — NASA research, testing dashboard and technical presentation

I am Yazeed Al-Harthi ([@eh7r](https://github.com/eh7r)), a member of Team EpochLoom. The final project at [epochloom.space](https://epochloom.space) is the team's collective work; I did not build it alone.

**Research**
- Researched NASA data sources, APIs, archives and mirror services, and investigated suitable methods for retrieving SPHEREx data.
- Explored NASA technical resources to support the team's development process.

**Development and testing**
- Built this separate testing and experimentation dashboard to evaluate functionality, and used it to run tests that supported the team's technical evaluation.

**Technical presentation**
- During the team's hackathon presentation, I delivered the technical explanation of the project's workflow: observation retrieval, 12 parallel image reads, North-Up alignment and image stacking; photometric measurement and its comparison with IRSA Photometry; and outputs including animated GIFs and FITS and CSV exports.
- I presented the team's reported performance results: 22 seconds of processing time, 9× faster performance and a 90 % reduction in waiting time. These figures were reported by the team during the presentation and have not been independently verified in this repository.

**AI assistance.** Parts of this dashboard's code and documentation were written with Claude Code (Anthropic). Those commits carry a `Co-Authored-By: Claude` trailer, which is why GitHub shows a Claude avatar next to mine. I directed the work, chose what to build and reviewed the results.

## 13. Team and credits

- **Team EpochLoom**, NASA Space Apps Challenge 2026, KFUPM Dhahran. Final project: [epochloom.space](https://epochloom.space).
- **SPHEREx** data: NASA / Caltech-IPAC, served by [IRSA](https://irsa.ipac.caltech.edu); public copy on AWS Open Data.
- **Gaia DR3**: ESA/Gaia/DPAC. **2MASS**, **AllWISE**, **CatWISE2020** via IRSA.
- `web/vendor/aladin.js` is [Aladin Lite](https://github.com/cds-astro/aladin-lite) v3.8.2 by CDS (Centre de Données astronomiques de Strasbourg). The dashboard does not hide the Aladin logo and link in the map view, as the Aladin Lite authors ask.

## 14. License

**Bundled Aladin Lite.** The bundled Aladin Lite 3.8.2 file (`web/vendor/aladin.js`) is unmodified and is byte-for-byte identical to the corresponding upstream distribution file. The upstream package contains conflicting licensing information: its package metadata and README identify GPL-3.0, while its license files contain additional LGPL-3.0 text. For caution, this repository provisionally documents the bundled dependency under GPL-3.0 pending clarification. This is not a legal determination of the applicable license. The upstream package's license files (`COPYING`, `COPYING.LESSER` and `LICENSE`) are not currently included in this repository.

Source code: <https://github.com/cds-astro/aladin-lite>

**This repository's own code.** No license has been chosen yet. Until one is added, all rights are reserved by default.
