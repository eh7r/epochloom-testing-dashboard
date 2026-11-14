"""SPHEREx Explorer backend.

Run:  uvicorn server:app --reload --port 8000
Open: http://127.0.0.1:8000/
"""
import hashlib
import json
import os
import random
import re
import threading
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyvo
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.utils.data import conf
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from photometry import PhotometryError, measure

conf.remote_timeout = 120

# The S3 fallback reads byte ranges over HTTPS through fsspec. fsspec's default
# HTTP backend needs aiohttp, which has no build for Python 3.14 yet; its
# synchronous backend (http_sync) uses requests instead, so use that when
# aiohttp is missing.
try:
    import aiohttp  # noqa: F401
except ImportError:
    import fsspec
    for _proto in ("http", "https"):
        fsspec.register_implementation(_proto, "fsspec.implementations.http_sync.HTTPFileSystem", clobber=True)

BASE = Path(__file__).resolve().parent
CACHE = BASE / "cache"
CACHE.mkdir(exist_ok=True)
ALLOWED = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
TAP_URL = "https://irsa.ipac.caltech.edu/TAP"
# Official public copy of the SPHEREx files on AWS Open Data (no account needed):
# https://registry.opendata.aws/spherex-qr/ . The key is the part of the IRSA
# URL after /ibe/data/spherex/ (ASSUMPTION: checked against IRSA docs, not yet
# against a live file; if it 404s you will see it in the terminal log).
S3_BASE = os.environ.get("SPHEREX_S3_BASE", "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/")
# Order in which cutout sources are tried. Example: SPHEREX_SOURCES=s3,irsa
SOURCES = [x.strip() for x in os.environ.get("SPHEREX_SOURCES", "irsa,s3").split(",") if x.strip()]

app = FastAPI(title="SPHEREx Explorer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # read-only public data, no cookies: any page may call the API
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["X-Tx", "X-Ty", "X-Nx", "X-Ny"],  # target pixel, read by the dashboard
)

# Disk cap for cache/ (bytes). Oldest files are deleted first once it is exceeded.
CACHE_MAX_BYTES = int(float(os.environ.get("SPHEREX_CACHE_MAX_GB", "2")) * 1e9)
# A SPHEREx archive file path: letters, digits, _ - . / only, ending in .fits, no "..".
_URL_PATH = re.compile(r"[A-Za-z0-9_./-]+\.fits")


def _check_url(url: str) -> None:
    """Only real SPHEREx archive files, so nobody can make this server fetch other addresses."""
    rest = url[len(ALLOWED):] if url.startswith(ALLOWED) else None
    if rest is None or not _URL_PATH.fullmatch(rest) or ".." in rest or "//" in rest:
        raise HTTPException(400, "url must be a SPHEREx archive file URL")


def _trim_cache() -> None:
    files = [f for f in CACHE.iterdir() if f.is_file() and not f.name.endswith(".part")]
    total = sum(f.stat().st_size for f in files)
    if total <= CACHE_MAX_BYTES:
        return
    for f in sorted(files, key=lambda f: f.stat().st_mtime):
        try:
            total -= f.stat().st_size
            f.unlink()
        except OSError:
            pass
        if total <= CACHE_MAX_BYTES * 0.9:
            break

service = pyvo.dal.TAPService(TAP_URL)


# --------------------------------------------------------------------------
# Metadata (TAP)
# --------------------------------------------------------------------------
@app.get("/observations")
def observations(
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    limit: int = Query(24, ge=1, le=200),
):
    q = f"""
    SELECT TOP {limit}
        'https://irsa.ipac.caltech.edu/' || a.uri AS url,
        p.time_bounds_lower,
        p.energy_bandpassname
    FROM spherex.artifact a
    JOIN spherex.plane p ON a.planeid = p.planeid
    WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly)
    ORDER BY p.time_bounds_lower
    """
    cache = CACHE / ("obs_" + hashlib.sha1(f"{ra:.6f}|{dec:.6f}|{limit}".encode()).hexdigest() + ".json")
    last = None
    for attempt in range(3):
        try:
            table = service.run_sync(q).to_table()
            rows = [
                {
                    "url": str(r["url"]),
                    "time": float(r["time_bounds_lower"]),
                    "bandpass": str(r["energy_bandpassname"]),
                }
                for r in table
            ]
            cache.write_text(json.dumps(rows))
            return rows
        except Exception as e:  # network error, 503, ...
            last = e
            time.sleep(1.5 * (attempt + 1))
    if cache.exists():  # IRSA is down: serve the last good answer for this position
        print(f"[observations] IRSA TAP failed ({last}); serving cached result")
        return json.loads(cache.read_text())
    raise HTTPException(502, f"IRSA TAP query failed: {last}")


# --------------------------------------------------------------------------
# Cutouts: cached FITS on disk, shared by /cutout and /photometry
# --------------------------------------------------------------------------
_locks: dict = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def cutout_key(url: str, ra: float, dec: float, size: float) -> str:
    return hashlib.sha1(f"{url}|{ra:.6f}|{dec:.6f}|{size}".encode()).hexdigest()


_irsa_down_until = 0.0  # circuit breaker: after repeated failures skip IRSA for a while


def _from_irsa(url, ra, dec, size, tmp):
    global _irsa_down_until
    if time.time() < _irsa_down_until:
        raise RuntimeError("IRSA recently failing, skipped")
    full = f"{url}?center={ra},{dec}d&size={size}"
    last = None
    for attempt in range(3):
        try:
            with fits.open(full, cache=False) as h:
                h.writeto(tmp, overwrite=True, output_verify="silentfix")
            return
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1) + random.random())
    _irsa_down_until = time.time() + 45
    raise RuntimeError(f"IRSA: {last}")


def _from_s3(url, ra, dec, size, tmp):
    """Read only the needed pixels of the full image from the AWS copy and
    write a cutout shaped like the IRSA one (IMAGE, FLAGS, VARIANCE, ZODI)."""
    src = S3_BASE + url[len(ALLOWED):]
    with fits.open(src, use_fsspec=True, fsspec_kwargs={"block_size": 1 << 20}) as h:
        hdr = h["IMAGE"].header
        wcs = WCS(hdr)
        x, y = (float(v) for v in wcs.world_to_pixel(SkyCoord(ra, dec, unit="deg")))
        ny, nx = hdr["NAXIS2"], hdr["NAXIS1"]
        if not (0 <= x < nx and 0 <= y < ny):
            raise RuntimeError("target is not on this detector image")
        pix = float(proj_plane_pixel_scales(wcs.celestial)[0])  # deg/pixel
        half = max(8, int(np.ceil(size / 2 / pix)))
        x0, x1 = max(0, int(round(x)) - half), min(nx, int(round(x)) + half + 1)
        y0, y1 = max(0, int(round(y)) - half), min(ny, int(round(y)) + half + 1)
        out = fits.HDUList([fits.PrimaryHDU(header=h[0].header)])
        for name in ("IMAGE", "FLAGS", "VARIANCE", "ZODI"):
            if name not in h:
                continue
            hd = h[name].header.copy()
            for k in ("BSCALE", "BZERO"):
                hd.remove(k, ignore_missing=True)
            hd["CRPIX1"] = hd["CRPIX1"] - x0  # SIP terms are relative to CRPIX, so they stay valid
            hd["CRPIX2"] = hd["CRPIX2"] - y0
            out.append(fits.ImageHDU(np.array(h[name].section[y0:y1, x0:x1]), header=hd, name=name))
        try:  # wavelength at the target, from the full file's lookup table
            sw = WCS(hdr, fobj=h, key="W")
            sw.sip = None
            w, b = sw.pixel_to_world(x, y)
            out["IMAGE"].header["WAVETGT"] = (float(u.Quantity(w).to_value(u.um)), "wavelength at target, um")
            out["IMAGE"].header["BWTGT"] = (float(u.Quantity(b).to_value(u.um)), "bandwidth at target, um")
        except Exception as e:
            print(f"[s3] no wavelength from WCS-WAVE: {e}")
        out["IMAGE"].header["CUTSRC"] = "aws-s3"
        out.writeto(tmp, overwrite=True, output_verify="silentfix")


_SRC = {"irsa": _from_irsa, "s3": _from_s3}


def get_cutout_fits(url: str, ra: float, dec: float, size: float) -> Path:
    # Only SPHEREx archive URLs: otherwise anyone could make this server
    # download arbitrary addresses.
    _check_url(url)
    key = cutout_key(url, ra, dec, size)
    path = CACHE / f"{key}.fits"
    with _lock_for(key):  # two requests for the same file download it once
        if path.exists():
            return path
        tmp = CACHE / f"{key}.part"
        errors = []
        for name in SOURCES:
            try:
                _SRC[name](url, ra, dec, size, tmp)
                tmp.replace(path)
                print(f"[cutout] {key[:8]} ok via {name}")
                _trim_cache()
                return path
            except Exception as e:
                errors.append(f"{name}: {e}")
                print(f"[cutout] {key[:8]} {name} failed: {e}")
    raise HTTPException(502, "no source returned the cutout (" + " | ".join(errors) + ")")


@app.get("/cutout")
def cutout(
    url: str,
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    size: float = Query(0.1, gt=0, le=0.5),
):
    path = get_cutout_fits(url, ra, dec, size)
    png = path.with_suffix(".v2.png")
    if not png.exists():
        with fits.open(path) as h:
            data = h["IMAGE"].data.astype(float)
        finite = np.isfinite(data)
        if not finite.any():
            raise HTTPException(422, "cutout has no valid pixels")
        lo, hi = np.percentile(data[finite], [1, 99.5])
        x = np.clip((np.nan_to_num(data, nan=lo) - lo) / (hi - lo + 1e-12), 0, 1)
        img = np.arcsinh(8 * x) / np.arcsinh(8)  # brings out faint structure
        # NaN-safe: fixed vmin/vmax and no NaN left (a NaN used to make the PNG blank)
        plt.imsave(png, img, cmap="gray", vmin=0, vmax=1, origin="lower")
    with fits.open(path) as h:  # where the target sits in this frame (for the overlay)
        ny, nx = h["IMAGE"].data.shape
        tx, ty = (float(v) for v in WCS(h["IMAGE"].header).world_to_pixel(SkyCoord(ra, dec, unit="deg")))
    return FileResponse(png, media_type="image/png",
                        headers={"X-Tx": f"{tx:.3f}", "X-Ty": f"{ty:.3f}", "X-Nx": str(nx), "X-Ny": str(ny)})


# --------------------------------------------------------------------------
# Our own photometry (see photometry.py)
# --------------------------------------------------------------------------
@app.get("/photometry")
def photometry(
    url: str,
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    size: float = Query(0.1, gt=0, le=0.5),
    r_ap: float = Query(2.0, gt=0.5, le=8),
    r_in: float = Query(3.5, gt=1, le=12),
    r_out: float = Query(6.0, gt=2, le=15),
):
    if not (r_ap < r_in < r_out):
        raise HTTPException(422, "need r_ap < r_in < r_out")
    path = get_cutout_fits(url, ra, dec, size)
    try:
        return measure(path, ra, dec, r_ap, r_in, r_out)
    except PhotometryError as e:
        raise HTTPException(422, str(e))


@app.get("/health")
def health():
    return {"ok": True, "sources": SOURCES}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
# The dashboard lives in web/ (index.html + any static files, e.g. vendor/).
# Mounted last so the API routes above take priority.
app.mount("/", StaticFiles(directory=BASE / "web", html=True), name="web")
