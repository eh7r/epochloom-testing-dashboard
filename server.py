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
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from astrometry import propagate
import catalogs
from photometry import PhotometryError, measure
import diff as diffmod
from pydantic import BaseModel, Field

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
# Order in which cutout sources are tried. AWS first: it reads only the pixels we
# need and does not have IRSA's frequent 503s. Example: SPHEREX_SOURCES=irsa,s3
SOURCES = [x.strip() for x in os.environ.get("SPHEREX_SOURCES", "s3,irsa").split(",") if x.strip()]

app = FastAPI(title="SPHEREx Explorer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # read-only public data, no cookies: any page may call the API
    allow_methods=["GET", "POST"],  # POST only for /diffstack (long frame list)
    allow_headers=["*"],
    # target pixel, own stretch, propagated position: read by the dashboard
    expose_headers=["X-Tx", "X-Ty", "X-Nx", "X-Ny", "X-Lo", "X-Hi", "X-Ra", "X-Dec", "X-Flags",
                    "X-Wl", "X-RefLo", "X-RefHi", "X-NRef"],
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
TAP_TIMEOUT = float(os.environ.get("SPHEREX_TAP_TIMEOUT", "40"))


def tap_query(adql: str, timeout: float = TAP_TIMEOUT):
    """Synchronous IRSA TAP query with a hard timeout (pyvo has none, so a dead
    IRSA used to hang /observations forever instead of falling back to cache)."""
    import io

    import requests
    from astropy.io.votable import parse_single_table

    r = requests.post(f"{TAP_URL}/sync", data={"QUERY": adql, "LANG": "ADQL", "REQUEST": "doQuery",
                                               "FORMAT": "votable"}, timeout=timeout)
    r.raise_for_status()
    return parse_single_table(io.BytesIO(r.content)).to_table()


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
    have_cache = cache.exists() or bool(_obs_nearest(ra, dec))
    for attempt in range(1 if have_cache else 3):  # cached answer: one short try, no retries
        try:
            table = tap_query(q, timeout=15 if have_cache else TAP_TIMEOUT)
            rows = [
                {
                    "url": str(r["url"]),
                    "time": float(r["time_bounds_lower"]),
                    "bandpass": str(r["energy_bandpassname"]),
                }
                for r in table
            ]
            cache.write_text(json.dumps(rows))
            _obs_index_add(cache.name, ra, dec, limit)
            return rows
        except Exception as e:  # network error, 503, ...
            last = e
            time.sleep(1.5 * (attempt + 1))
    if cache.exists():  # IRSA is down: serve the last good answer for this position
        print(f"[observations] IRSA TAP failed ({last}); serving cached result")
        return json.loads(cache.read_text())
    near = _obs_nearest(ra, dec)
    if near:  # a saved search a few arcsec/arcmin away covers the same detector images (~3.5 deg wide)
        print(f"[observations] IRSA TAP failed ({last}); serving cached result {near[1]:.1f}\" away")
        return json.loads((CACHE / near[0]).read_text())[:limit]
    raise HTTPException(502, f"IRSA TAP query failed: {last}")


_OBS_INDEX = CACHE / "obs_index.json"  # cache file -> [ra, dec, limit] of the query that made it


def _obs_index_add(name, ra, dec, limit):
    try:
        idx = json.loads(_OBS_INDEX.read_text()) if _OBS_INDEX.exists() else {}
        idx[name] = [ra, dec, limit]
        _OBS_INDEX.write_text(json.dumps(idx))
    except Exception as e:
        print(f"[observations] index not updated: {e}")


def _obs_nearest(ra, dec, max_deg=0.1):
    """Closest cached search within max_deg (largest limit wins ties): (file, arcsec) or None."""
    try:
        idx = json.loads(_OBS_INDEX.read_text())
    except Exception:
        return None
    best = None
    c0 = SkyCoord(ra, dec, unit="deg")
    for name, (r, d, lim) in idx.items():
        if not (CACHE / name).exists():
            continue
        sep = c0.separation(SkyCoord(r, d, unit="deg")).deg
        if sep <= max_deg and (best is None or (sep, -lim) < (best[1], -best[2])):
            best = (name, sep, lim)
    return (best[0], best[1] * 3600) if best else None


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
            # h[name] in try/except, not "name in h": the membership test makes
            # astropy read every remaining header, i.e. extra S3 requests.
            try:
                h[name]
            except KeyError:
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


def frame_pos(ra, dec, pmra, pmdec, epoch, mjd):
    """Target position at this frame's time. With pm + epoch + mjd the position is
    propagated (astropy apply_space_motion); otherwise the given position is used.
    Rounded to 1e-6 deg (3.6 mas) so cache keys are stable."""
    if mjd is not None and epoch is not None and (pmra or pmdec):
        ra, dec = propagate(ra, dec, pmra, pmdec, epoch, mjd=mjd)
    return round(ra % 360, 6), round(dec, 6)


def _sky_sub(path):
    """IMAGE minus its own median. Zodiacal light is NOT subtracted in SPHEREx L2
    images and changes with date, so without this a shared stretch would mostly
    show the zodi level, not the sources."""
    with fits.open(path) as h:
        data = h["IMAGE"].data.astype(float)
    finite = np.isfinite(data)
    if not finite.any():
        raise HTTPException(422, "cutout has no valid pixels")
    return data - np.median(data[finite]), finite


def _render_png(img, lo, hi, png):
    x = np.clip((np.nan_to_num(img, nan=lo) - lo) / (hi - lo + 1e-12), 0, 1)
    out = np.arcsinh(8 * x) / np.arcsinh(8)  # brings out faint structure
    # NaN-safe: fixed vmin/vmax and no NaN left (a NaN used to make the PNG blank)
    plt.imsave(png, out, cmap="gray", vmin=0, vmax=1, origin="lower")


def _png_response(path, png, ra, dec, lo, hi):
    with fits.open(path) as h:  # where the target sits in this frame (for the overlay)
        ny, nx = h["IMAGE"].data.shape
        tx, ty = (float(v) for v in WCS(h["IMAGE"].header).world_to_pixel(SkyCoord(ra, dec, unit="deg")))
    return FileResponse(png, media_type="image/png",
                        headers={"X-Tx": f"{tx:.3f}", "X-Ty": f"{ty:.3f}", "X-Nx": str(nx), "X-Ny": str(ny),
                                 "X-Lo": f"{lo:.6g}", "X-Hi": f"{hi:.6g}",
                                 "X-Ra": f"{ra:.6f}", "X-Dec": f"{dec:.6f}"})


@app.get("/cutout")
def cutout(
    url: str,
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    size: float = Query(0.1, gt=0, le=0.5),
    pmra: float = Query(0.0, ge=-20000, le=20000, description="mas/yr, includes cos(dec)"),
    pmdec: float = Query(0.0, ge=-20000, le=20000, description="mas/yr"),
    epoch: float | None = Query(None, ge=1900, le=2100, description="Julian year of ra/dec"),
    mjd: float | None = Query(None, ge=40000, le=80000, description="frame time; position is propagated to it"),
    lo: float | None = Query(None, description="shared stretch low (sky-subtracted MJy/sr)"),
    hi: float | None = Query(None, description="shared stretch high"),
):
    """PNG of the frame, sky-median subtracted. Without lo/hi the frame gets its own
    1-99.5 percentile stretch (X-Lo/X-Hi report it); with lo/hi every frame of a band
    can share one stretch, so brightness changes between dates stay visible."""
    ra, dec = frame_pos(ra, dec, pmra, pmdec, epoch, mjd)
    path = get_cutout_fits(url, ra, dec, size)
    img, finite = _sky_sub(path)
    if lo is None or hi is None or not hi > lo:
        lo, hi = (float(v) for v in np.percentile(img[finite], [1, 99.5]))
        png = path.with_suffix(".v3.png")
    else:
        png = path.with_suffix(f".v3_{lo:.5g}_{hi:.5g}.png")
    if not png.exists():
        _render_png(img, lo, hi, png)
    return _png_response(path, png, ra, dec, lo, hi)


# --------------------------------------------------------------------------
# Difference imaging: frame minus the median of all frames of a band (diff.py)
# --------------------------------------------------------------------------
class DiffReq(BaseModel):
    urls: list[str] = Field(..., min_length=2, max_length=300)
    mjds: list[float] = Field(..., min_length=2, max_length=300)
    ra: float = Field(..., ge=0, le=360)
    dec: float = Field(..., ge=-90, le=90)
    size: float = Field(0.1, gt=0, le=0.5)
    pmra: float = Field(0.0, ge=-20000, le=20000)
    pmdec: float = Field(0.0, ge=-20000, le=20000)
    epoch: float | None = Field(None, ge=1900, le=2100)
    tol: float = Field(0.03, ge=0.005, le=0.2, description="reference frames within +-tol of the frame's wavelength")


MIN_REFS = 3
_DIFF_ID = re.compile(r"[0-9a-f]{16}")


@app.post("/diffstack")
def diffstack(req: DiffReq):
    """Resample every frame of a band onto one sky-fixed grid and record each frame's wavelength at
    the target. Returns an id for /diff (which subtracts a wavelength-matched median)."""
    if len(req.urls) != len(req.mjds):
        raise HTTPException(422, "urls and mjds must have the same length")
    for url in req.urls:
        _check_url(url)
    pos = [frame_pos(req.ra, req.dec, req.pmra, req.pmdec, req.epoch, m) for m in req.mjds]
    gra, gdec = frame_pos(req.ra, req.dec, req.pmra, req.pmdec, req.epoch, float(np.median(req.mjds)))
    n = max(16, int(round(req.size * 3600 / diffmod.PIX_ARCSEC)))
    key = hashlib.sha1(json.dumps([sorted(zip(req.urls, req.mjds)), req.ra, req.dec, req.size, req.pmra,
                                   req.pmdec, req.epoch, req.tol, "v2-lvf"]).encode()).hexdigest()[:16]
    meta_p, cube_p = CACHE / f"diff_{key}.json", CACHE / f"diff_{key}.npy"
    if meta_p.exists() and cube_p.exists():
        return {k: v for k, v in json.loads(meta_p.read_text()).items() if k != "frames"} | {"id": key}
    gw = diffmod.grid_wcs(gra, gdec, n)
    stack, frames, wls, skipped = [], {}, [], []
    for url, (fra, fdec) in zip(req.urls, pos):
        try:
            path = get_cutout_fits(url, fra, fdec, req.size)
            stack.append(diffmod.resample(path, gw, n))
            wls.append(diffmod.target_wavelength(path, fra, fdec))
            frames[url] = [fra, fdec, len(stack) - 1]
        except Exception as e:
            skipped.append(f"{url.rsplit('/', 1)[-1]}: {e}")
    if len(stack) < 2:
        raise HTTPException(422, "need at least 2 usable frames")
    cube = np.array(stack)
    # SPHEREx is a linear variable filter: frames of one band see the target at different
    # wavelengths. Each frame's reference = median of the OTHER frames within +-tol in wavelength.
    refs = diffmod.references(wls, req.tol)
    resid = []
    with np.errstate(all="ignore"):
        for i, r in enumerate(refs):
            if len(r) >= MIN_REFS:
                resid.append(cube[i] - np.nanmedian(cube[r], axis=0))
        lim = float(np.nanpercentile(np.abs(np.array(resid)), 99.5)) if resid else 1.0
    np.save(cube_p, cube)
    meta = {"n_frames": len(stack), "n_subtractable": len(resid), "lim": lim or 1.0, "tol": req.tol,
            "min_refs": MIN_REFS, "grid_n": n, "grid_ra": gra, "grid_dec": gdec, "skipped": skipped,
            "size": req.size, "wavelengths": wls, "refs": refs, "frames": frames}
    meta_p.write_text(json.dumps(meta))
    return {k: v for k, v in meta.items() if k not in ("frames", "refs")} | {"id": key}


@app.get("/diff")
def diff_png(id: str, url: str):
    """PNG of (frame - median of the other frames within +-tol in wavelength) on the stack's sky
    grid, symmetric shared stretch. 422 (with the reason) when fewer than 3 such frames exist.
    X-Tx/X-Ty: the target's (propagated) position on that grid for this frame."""
    if not _DIFF_ID.fullmatch(id):
        raise HTTPException(400, "bad id")
    meta_p, cube_p = CACHE / f"diff_{id}.json", CACHE / f"diff_{id}.npy"
    if not meta_p.exists():
        raise HTTPException(404, "unknown stack; call /diffstack first")
    meta = json.loads(meta_p.read_text())
    if url not in meta["frames"]:
        raise HTTPException(404, "frame not in this stack")
    fra, fdec, i = meta["frames"][url]
    wl, refs, tol = meta["wavelengths"][i], meta["refs"][i], meta["tol"]
    if wl is None:
        raise HTTPException(422, "no wavelength at the target for this frame; not subtracted")
    if len(refs) < meta["min_refs"]:
        raise HTTPException(422, f"only {len(refs)} other frame(s) within ±{tol*100:.0f}% of "
                                 f"λ={wl:.3f} µm (need {meta['min_refs']}); not subtracted")
    rw = [meta["wavelengths"][j] for j in refs]
    n = meta["grid_n"]
    gw = diffmod.grid_wcs(meta["grid_ra"], meta["grid_dec"], n)
    png = CACHE / f"diff_{id}_{i}.png"
    if not png.exists():
        cube = np.load(cube_p, mmap_mode="r")
        with np.errstate(all="ignore"):
            res = cube[i] - np.nanmedian(cube[refs], axis=0)
        diffmod.render(res, meta["lim"], png)
    tx, ty = diffmod.target_pixel(gw, fra, fdec)
    return FileResponse(png, media_type="image/png",
                        headers={"X-Tx": f"{tx:.3f}", "X-Ty": f"{ty:.3f}", "X-Nx": str(n), "X-Ny": str(n),
                                 "X-Ra": f"{fra:.6f}", "X-Dec": f"{fdec:.6f}", "X-Wl": f"{wl:.4f}",
                                 "X-RefLo": f"{min(rw):.4f}", "X-RefHi": f"{max(rw):.4f}", "X-NRef": str(len(refs))})


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
    pmra: float = Query(0.0, ge=-20000, le=20000),
    pmdec: float = Query(0.0, ge=-20000, le=20000),
    epoch: float | None = Query(None, ge=1900, le=2100),
    mjd: float | None = Query(None, ge=40000, le=80000),
):
    if not (r_ap < r_in < r_out):
        raise HTTPException(422, "need r_ap < r_in < r_out")
    ra, dec = frame_pos(ra, dec, pmra, pmdec, epoch, mjd)
    path = get_cutout_fits(url, ra, dec, size)
    try:
        return measure(path, ra, dec, r_ap, r_in, r_out)
    except PhotometryError as e:
        raise HTTPException(422, str(e))


# --------------------------------------------------------------------------
# Catalog astrometry (IRSA: Gaia DR3, CatWISE2020, AllWISE, 2MASS) - see catalogs.py
# --------------------------------------------------------------------------
@app.get("/astrometry")
def astrometry_ep(target: str | None = Query(None, description="one of: " + ", ".join(catalogs.TARGETS))):
    """Reference astrometry for the demo targets, straight from the IRSA catalog row:
    ra, dec, ref epoch, pmra, pmdec (mas/yr), parallax, source table and id.
    Without `target`: all targets (failures reported per target)."""
    if target:
        if target not in catalogs.TARGETS:
            raise HTTPException(404, "unknown target; known: " + ", ".join(catalogs.TARGETS))
        try:
            return catalogs.astrometry(target)
        except catalogs.CatalogError as e:
            raise HTTPException(502, str(e))
    out = []
    for k in catalogs.TARGETS:
        try:
            out.append(catalogs.astrometry(k))
        except catalogs.CatalogError as e:
            out.append({"target": k, "name": catalogs.TARGETS[k]["name"], "error": str(e)})
    return out


@app.get("/motion")
def motion_ep(
    target: str | None = Query(None, description="demo target key (see /astrometry); or give ra/dec/pm/epoch"),
    ra: float | None = Query(None, ge=0, le=360),
    dec: float | None = Query(None, ge=-90, le=90),
    pmra: float = Query(0.0, ge=-20000, le=20000),
    pmdec: float = Query(0.0, ge=-20000, le=20000),
    epoch: float = Query(2016.0, ge=1900, le=2100),
    parallax: float | None = Query(None, ge=0, le=1000),
):
    """Motion track: observed catalog positions (2MASS, AllWISE, CatWISE2020, Gaia DR3), each matched
    at the reference position propagated to that survey's epoch, plus the computed yearly path."""
    if target:
        if target not in catalogs.TARGETS:
            raise HTTPException(404, "unknown target; known: " + ", ".join(catalogs.TARGETS))
        try:
            ref = catalogs.astrometry(target)
        except catalogs.CatalogError as e:
            raise HTTPException(502, str(e))
    elif ra is not None and dec is not None:
        ref = {"ra": ra, "dec": dec, "ref_epoch": epoch, "pmra": pmra, "pmdec": pmdec, "parallax": parallax,
               "catalog": "user input (not catalog-verified)", "source_table": None, "source_id": None}
    else:
        raise HTTPException(422, "give target, or ra and dec")
    return catalogs.motion(ref)


@app.get("/catalog")
def catalog_ep(
    catalog: str = Query("gaia_dr3_source", description="gaia_dr3_source | catwise_2020 | allwise_p3as_psd | fp_psc"),
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    radius: float = Query(120, gt=0, le=900, description="arcsec"),
):
    """Cone search of an IRSA catalog (rows as stored in the catalog, positions at the catalog's own epoch)."""
    if catalog not in catalogs.COLS:
        raise HTTPException(422, "catalog must be one of " + ", ".join(catalogs.COLS))
    try:
        rows = catalogs.cone(catalog, ra, dec, radius)
    except catalogs.CatalogError as e:
        raise HTTPException(502, str(e))
    return {"catalog": catalogs.LABEL[catalog], "table": catalog, "radius_arcsec": radius, "rows": rows[:3000]}


# --------------------------------------------------------------------------
# SPHEREx HiPS for the sky map. IRSA's only SPHEREx HiPS (hips/list) is
# SPHEREx/spherex_allsky_cube: its cube slices exist only as FITS tiles
# (custom_tile_format = fits-cube), but it also has 2D PNG tiles (NpixN.png,
# Allsky.png). Aladin sees dataproduct_type=cube and asks for PNG slices
# (NpixN_k.png) that do not exist, so the map stays empty. Serving the same
# properties as a 2D image HiPS makes Aladin use the PNG tiles, which are
# redirected to IRSA (nothing is copied or modified).
# --------------------------------------------------------------------------
SPHEREX_HIPS = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube"
_HIPS_TILE = re.compile(r"Norder\d{1,2}/(Dir\d+/Npix\d+|Allsky)\.png")
_hips_props = None


@app.get("/hips/spherex/properties", response_class=PlainTextResponse)
def hips_props():
    global _hips_props
    if _hips_props is None:
        import requests
        r = requests.get(SPHEREX_HIPS + "/properties", timeout=30)
        r.raise_for_status()
        keep = []
        for line in r.text.splitlines():
            k = line.split("=", 1)[0].strip()
            if k.startswith(("hips_cube", "data_cube", "custom_tile", "dataproduct_type")):
                continue
            if k == "hips_service_url":
                continue
            if k == "obs_title":
                line = "obs_title = SPHEREx DR1 all-sky (IRSA PNG preview tiles of spherex_allsky_cube)"
            keep.append(line)
        keep.append("dataproduct_type = image")
        _hips_props = "\n".join(keep) + "\n"
    return _hips_props


@app.get("/hips/spherex/{path:path}")
def hips_tile(path: str):
    if not _HIPS_TILE.fullmatch(path):
        raise HTTPException(404, "not a SPHEREx HiPS PNG tile")
    return RedirectResponse(f"{SPHEREX_HIPS}/{path}", status_code=302)


@app.get("/health")
def health():
    return {"ok": True, "sources": SOURCES}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
# The dashboard lives in web/ (index.html + any static files, e.g. vendor/).
# Mounted last so the API routes above take priority.
app.mount("/", StaticFiles(directory=BASE / "web", html=True), name="web")
