# SPHEREx Explorer

**NASA Space Apps Challenge 2026 · Team EpochLoom · Challenge: _Planet X and SPHEREx_**

A small web tool to explore NASA's **SPHEREx** all-sky spectral survey.
Type a sky position and it:

1. finds every SPHEREx image that covers it (IRSA archive),
2. downloads small cutouts and **blinks** them over time, band by band,
3. builds the object's **spectrum with our own aperture photometry** (0.75–5 µm, 6 bands),
4. shows the field on an interactive sky map (DSS2 / 2MASS / AllWISE / SDSS).

No account, API key or login is needed. All data is public.

---

## 1. Requirements

| | |
|---|---|
| Python | **3.11 or newer** (astropy 8 needs it) |
| Git | any recent version |
| Internet | the server talks to IRSA (and AWS as a fallback) |
| Browser | any modern browser (Chrome, Firefox, Safari, Brave, Edge) with WebGL2 |

---

## 2. Install and run

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
git clone <REPO_URL> spherex
cd spherex
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

### macOS

Install [Homebrew](https://brew.sh) first if you do not have it, then:

```bash
brew install git python
git clone <REPO_URL> spherex
cd spherex
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

### Arch Linux (fish shell)

```fish
git clone <REPO_URL> spherex; and cd spherex
python -m venv .venv
source .venv/bin/activate.fish
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

Then open **<http://127.0.0.1:8000/>**.

Next time you only need:

```bash
cd spherex
source .venv/bin/activate          # fish: source .venv/bin/activate.fish
uvicorn server:app --reload --port 8000
```

---

## 3. Open it from a phone, tablet or another laptop

The dashboard is a plain web page served by the backend, so any device on the **same Wi-Fi** can use it.

1. Start the server listening on the network:

   ```bash
   uvicorn server:app --host 0.0.0.0 --port 8000
   ```

2. Find the laptop's local IP address:

   | System | Command |
   |---|---|
   | Debian / Ubuntu | `hostname -I` |
   | macOS | `ipconfig getifaddr en0` |

3. On the other device open `http://<that-IP>:8000/` (for example `http://192.168.1.23:8000/`).

If it does not load:

- **macOS** asks once whether Python may accept incoming connections: click **Allow**.
- **Debian** with `ufw` enabled: `sudo ufw allow 8000/tcp`.
- Some event or campus Wi-Fi networks block device-to-device traffic. Use a phone hotspot instead.

> ⚠️ With `--host 0.0.0.0` anyone on the same network can use your server (it only reads public data, and its disk cache is capped at 2 GB). Stop it with `Ctrl+C` when you are done.

### Use a teammate's server from your browser

The dashboard can talk to any running backend. Add `?api=` to the address:

```
http://127.0.0.1:8000/?api=http://192.168.1.23:8000
```

---

## 4. How to use

1. **Target**: pick a preset or type RA / Dec in degrees, then **Search archive**.
2. **Bands**: toggle D1…D6 to filter the blink viewer and the spectrum.
3. **Blink**: choose a band, press **Play**; the circles show our photometry aperture and sky ring.
4. **Spectrum**: press **Extract spectrum**. Each point is one image. Hover a point for details, **CSV** to download.
5. **Sky map**: shows the target (pink cross) and the cutout footprint (cyan box) on the chosen survey.

### Fast-moving targets: use the 2025 position

SPHEREx observed in 2025–2026, but catalogues give J2000 positions. Stars with large proper motion have moved a lot since then, and the tool does **not** correct for this yet. Positions below were computed for 2025.6 from published J2000 coordinates and proper motions (check them before relying on them):

| Target | RA (deg) | Dec (deg) | Moved since catalogue epoch |
|---|---|---|---|
| Barnard's Star | 269.446357 | +4.767053 | ≈ 266″ |
| ε Indi Ba/Bb | 331.096267 | −56.801090 | ≈ 123″ |
| WISE 0855−0714 | 133.760417 | −7.242292 | ≈ 124″ |
| RW Aurigae A | 76.956108 | +30.401151 | < 1″ |

Notes from our tests: Barnard's Star is so bright that it saturates SPHEREx in most frames (the photometry rejects them). WISE 0855 is extremely faint at short wavelengths; expect a signal, if any, only in D5–D6.

---

## 5. Project layout

```
server.py          FastAPI backend: API + serves the web/ folder
photometry.py      our aperture photometry (one cutout -> flux, error, wavelength)
requirements.txt
web/
  index.html       the dashboard (single file, no build step)
  vendor/aladin.js Aladin Lite 3.8.2, served locally
cache/             downloaded cutouts (created on first run, not in git)
```

### API

| Endpoint | What it returns |
|---|---|
| `GET /observations?ra=&dec=&limit=` | SPHEREx images covering the position (IRSA TAP) |
| `GET /cutout?url=&ra=&dec=&size=` | PNG cutout; headers `X-Tx`, `X-Ty` give the target pixel |
| `GET /photometry?url=&ra=&dec=&size=&r_ap=&r_in=&r_out=` | flux (µJy), error, S/N, wavelength, flags |
| `GET /health` | server status |
| `GET /docs` | interactive API documentation |

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SPHEREX_SOURCES` | `irsa,s3` | order of cutout sources (`s3,irsa` prefers AWS) |
| `SPHEREX_S3_BASE` | AWS bucket URL | base URL of the public AWS copy |
| `SPHEREX_CACHE_MAX_GB` | `2` | disk limit for `cache/`; oldest files are deleted first |

Example: `SPHEREX_SOURCES=s3,irsa uvicorn server:app --port 8000`

---

## 6. How the photometry works

- **Background**: median of the clean pixels in a sky ring around the target.
- **Flux**: (mean in the aperture − background) × aperture pixels × pixel solid angle, converted from MJy/sr to µJy.
- **Error**: from the archive's VARIANCE plane, plus the scatter of the sky ring. Statistical only.
- **Wavelength**: per pixel, from each image's own `WCS-WAVE` table (SPHEREx wavelength changes across the detector).
- **Rejected**: targets near the cutout edge, apertures that are mostly saturated or flagged, too few clean sky pixels.
- Defaults: aperture 2 px, sky ring 3.5–6 px (1 px ≈ 6.15″).

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Frames say *failed* / HTTP 502 | IRSA sometimes answers 503. The server retries and falls back to AWS automatically; press **Retry failed frames**. |
| Sky map is black | Hard-refresh the page (`Ctrl+Shift+R`, macOS `Cmd+Shift+R`). Tiles come from IRSA and take ~20 s the first time. |
| `pip` cannot find `aiohttp` | Not needed. The server uses fsspec's `requests`-based HTTP backend when aiohttp is missing. |
| `Address already in use` | Another server is on port 8000: use `--port 8001`. |
| Spectrum point marked "saturated/flagged" | The star is too bright for SPHEREx in that frame; this is real, not a bug. |

---

## 8. Known limits

- Errors are statistical only: no aperture correction, no deblending of close neighbours.
- No proper-motion correction (see the table above).
- The AWS fallback works on live SPHEREx files, but AWS also drops connections now and then.
- The first load of any target is slow (≈ 15–40 s per frame from IRSA). Everything is cached afterwards.

---

## 9. Credits and licences

- **SPHEREx** data: NASA / Caltech-IPAC, served by **IRSA** (<https://irsa.ipac.caltech.edu>). Public copy on **AWS Open Data** (`nasa-irsa-spherex`).
- Sky-map tiles: DSS2, 2MASS, AllWISE and SDSS HiPS mirrored by IRSA.
- `web/vendor/aladin.js` is **[Aladin Lite](https://github.com/cds-astro/aladin-lite) v3.8.2** by CDS, Strasbourg, licensed under **GPL v3.0**. Please keep the Aladin logo and link in the map view.
- Built with FastAPI, astropy, NumPy, matplotlib and pyvo.
