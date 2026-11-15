"""Aperture photometry on SPHEREx spectral-image cutouts (no IRSA tool, no forced PSF fit).

One cutout = one wavelength at the target position. A spectrum is the set of
(wavelength, flux) points from many cutouts.
"""
from pathlib import Path

import numpy as np
from astropy import log as astropy_log
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_area

astropy_log.setLevel("WARNING")  # silence the harmless SIP notice

# Band wavelength ranges in microns (from the IRSA Spectrophotometry Tool form).
# Detector N is assumed to be band N; used only as a sanity check.
BANDS = {1: (0.75, 1.11), 2: (1.11, 1.64), 3: (1.64, 2.42),
         4: (2.42, 3.82), 5: (3.82, 4.42), 6: (4.42, 5.00)}

# Flag bits that make a pixel unusable. Bit numbers are read from the FLAGS
# header (MSKNxxxx keywords), so they follow the file, not a hard-coded table.
BAD_FLAGS = {"TRANSIENT", "OVERFLOW", "SUR_ERROR", "NONFUNC", "MISSING_DATA",
             "HOT", "COLD", "NONLINEAR", "BLOOM", "SNOWBALL", "HALO"}
# Warnings only: kept in the measurement but reported back to the caller.
WARN_FLAGS = {"DICHROIC", "PERSIST", "OUTLIER", "CROSSTALK", "STREAK",
              "GHOST", "GHOST_FPA", "GHOST_EXT", "PHANMISS"}


class PhotometryError(Exception):
    """The measurement is impossible or meaningless for this cutout."""


def _flag_names(header) -> dict:
    return {int(k[4:]): str(header[k]).strip() for k in header if k.startswith("MSKN")}


def measure(path, ra, dec, r_ap=2.0, r_in=3.5, r_out=6.0):
    """Measure flux at (ra, dec) in one cutout. Radii are in detector pixels."""
    with fits.open(path) as h:
        hdr = h["IMAGE"].header
        img = h["IMAGE"].data.astype(float)
        var = h["VARIANCE"].data.astype(float)
        flg = h["FLAGS"].data.astype(np.int64)
        names = _flag_names(h["FLAGS"].header)

        wcs = WCS(hdr)
        x, y = wcs.world_to_pixel(SkyCoord(ra, dec, unit="deg"))
        x, y = float(x), float(y)
        ny, nx = img.shape
        m = r_out + 1
        if not (m < x < nx - 1 - m and m < y < ny - 1 - m):
            raise PhotometryError("target too close to the cutout edge")

        wl = bw = None
        try:
            sw = WCS(hdr, fobj=h, key="W")
            sw.sip = None
            w, b = sw.pixel_to_world(x, y)
            wl = float(u.Quantity(w).to_value(u.um))
            bw = float(u.Quantity(b).to_value(u.um))
        except Exception:
            pass
        if wl is None and hdr.get("WAVETGT") is not None:  # cutout made from the AWS copy
            wl = float(hdr["WAVETGT"])
            bw = float(hdr["BWTGT"]) if hdr.get("BWTGT") is not None else None
        det = int(hdr.get("DETECTOR", 0) or 0)
        mjd = hdr.get("MJD-OBS")

    bad_bits = sum(1 << b for b, n in names.items() if n in BAD_FLAGS)
    yy, xx = np.indices(img.shape)
    rr = np.hypot(xx - x, yy - y)
    ap = rr <= r_ap
    an = (rr >= r_in) & (rr <= r_out)

    good = np.isfinite(img) & np.isfinite(var) & ((flg & bad_bits) == 0)
    ap_g, an_g = ap & good, an & good
    n_ap = int(ap.sum())
    if ap_g.sum() < max(3, n_ap // 2):
        raise PhotometryError("aperture is mostly saturated/flagged")
    if an_g.sum() < 8:
        raise PhotometryError("not enough clean pixels for the background")

    bkg = float(np.median(img[an_g]))
    net = (float(img[ap_g].mean()) - bkg) * n_ap  # MJy/sr summed over the aperture
    pix_sr = proj_plane_pixel_area(wcs.celestial) * (np.pi / 180) ** 2
    to_uJy = pix_sr * 1e12  # MJy/sr * sr = MJy ; 1 MJy = 1e12 uJy

    err_poisson = np.sqrt(var[ap_g].sum()) * (n_ap / ap_g.sum())
    err_bkg = img[an_g].std() / np.sqrt(an_g.sum()) * 1.25 * n_ap
    err = float(np.hypot(err_poisson, err_bkg))

    warn = sorted({names[b] for b in names if names[b] in WARN_FLAGS
                   and ((flg[ap] >> b) & 1).any()})
    lo, hi = BANDS.get(det, (None, None))
    band_ok = None
    if wl is not None and lo is not None:
        band_ok = bool(lo - 0.05 <= wl <= hi + 0.05)

    return {
        "wavelength": wl,
        "bandwidth": bw,
        "flux_uJy": net * to_uJy,
        "err_uJy": err * to_uJy,
        "snr": (net / err) if err > 0 else None,
        "background": bkg,
        "detector": det,
        "mjd": float(mjd) if mjd is not None else None,
        "band_ok": band_ok,
        "warnings": warn,
        "x": x,
        "y": y,
        "nx": nx,
        "ny": ny,
        "good_frac": float(ap_g.sum() / n_ap),
    }


def centroid(path, ra, dec, r_win=2.5, r_in=4.0, r_out=7.0, iters=4, min_snr=10.0):
    """Flux-weighted centroid of the source near (ra, dec) in one cutout, as sky coordinates.
    Background = median of a sky ring; refused (PhotometryError) if the window has saturated or
    bad pixels, or S/N < min_snr. Error: statistical term with a 0.3" empirical floor (see below)."""
    with fits.open(path) as h:
        hdr = h["IMAGE"].header
        img = h["IMAGE"].data.astype(float)
        var = h["VARIANCE"].data.astype(float)
        flg = h["FLAGS"].data.astype(np.int64)
        names = _flag_names(h["FLAGS"].header)
        wcs = WCS(hdr).celestial
        mjd = hdr.get("MJD-OBS")
    x0, y0 = (float(v) for v in wcs.world_to_pixel(SkyCoord(ra, dec, unit="deg")))
    ny, nx = img.shape
    if not (r_out + 1 < x0 < nx - r_out - 2 and r_out + 1 < y0 < ny - r_out - 2):
        raise PhotometryError("target too close to the cutout edge")
    bad_bits = sum(1 << b for b, n in names.items() if n in BAD_FLAGS)
    yy, xx = np.indices(img.shape)
    good = np.isfinite(img) & np.isfinite(var) & ((flg & bad_bits) == 0)
    xc, yc = x0, y0
    for _ in range(iters):
        rr = np.hypot(xx - xc, yy - yc)
        win, ring = rr <= r_win, (rr >= r_in) & (rr <= r_out) & good
        if (win & ~good).any():
            raise PhotometryError("saturated/flagged pixel in the centroid window")
        if ring.sum() < 8:
            raise PhotometryError("not enough clean sky pixels")
        w = np.clip(img[win] - np.median(img[ring]), 0, None)
        if w.sum() <= 0:
            raise PhotometryError("no flux above the background")
        xn, yn = float((w * xx[win]).sum() / w.sum()), float((w * yy[win]).sum() / w.sum())
        if np.hypot(xn - x0, yn - y0) > 2.0:
            raise PhotometryError("centroid wandered > 2 px from the predicted position (blend or no source)")
        xc, yc = xn, yn
    net = float((img[win] - np.median(img[ring])).sum())
    snr = net / float(np.sqrt(var[win].sum())) if var[win].sum() > 0 else 0.0
    if snr < min_snr:
        raise PhotometryError(f"S/N {snr:.1f} < {min_snr:.0f}")
    pos = wcs.pixel_to_world(xc, yc)
    pix = float(np.sqrt(proj_plane_pixel_area(wcs)) * 3600)
    return {"ra": float(pos.ra.deg), "dec": float(pos.dec.deg), "mjd": float(mjd) if mjd is not None else None,
            "x": xc, "y": yc, "snr": snr, "pixel_arcsec": pix,
            # statistical FWHM/(2.355 S/N) with a 0.3" floor: the per-frame scatter measured on the
            # test star (2026-11-15) was 0.12-0.16" in D1 and 0.2-0.4" in D4; the statistical term alone
            # (~0.03") is far too optimistic. Blends (e.g. the test star has a G=14 neighbour at 1.5")
            # shift the centroid further; that is not included.
            "err_arcsec": float(np.hypot(2.0 * pix / (2.355 * snr), 0.3)),
            "offset_from_prediction_arcsec": float(np.hypot(xc - x0, yc - y0) * pix)}
