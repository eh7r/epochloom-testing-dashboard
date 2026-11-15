"""Difference imaging for the blink viewer: each frame minus a wavelength-matched median.

SPHEREx frames of one sky position come at different roll angles and pointings, so
pixels cannot be subtracted directly. Every frame is resampled (bilinear, through its
own WCS incl. SIP) onto one sky-fixed TAN grid; static stars then line up and cancel,
while moving or variable sources leave +/- residuals.

SPHEREx is a linear variable filter: frames of one band see the target at different
wavelengths, so a plain band median leaves residuals from the spectrum alone (measured on
the test star: one star's flux varied x3 across D1). The reference for each frame is
therefore the median of the OTHER frames within +-3% of its wavelength (min 3 frames);
residuals can still include the remaining wavelength spread.
"""
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

PIX_ARCSEC = 6.15  # SPHEREx pixel


def grid_wcs(ra, dec, n, pix_arcsec=PIX_ARCSEC):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    w.wcs.cdelt = [-pix_arcsec / 3600, pix_arcsec / 3600]
    return w


def resample(path, gw, n):
    """Sky-median-subtracted IMAGE of a cutout, resampled onto grid gw (n x n). NaN outside."""
    with fits.open(path) as h:
        img = h["IMAGE"].data.astype(float)
        fw = WCS(h["IMAGE"].header).celestial
    fin = np.isfinite(img)
    img = img - np.median(img[fin])
    yy, xx = np.mgrid[0:n, 0:n]
    sky = gw.pixel_to_world(xx.ravel(), yy.ravel())
    fx, fy = (np.asarray(v, float).reshape(n, n) for v in fw.world_to_pixel(sky))
    ny, nx = img.shape
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    ok = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < nx) & (y0 + 1 < ny)
    x0c, y0c = np.clip(x0, 0, nx - 2), np.clip(y0, 0, ny - 2)
    dx, dy = fx - x0, fy - y0
    a, b = img[y0c, x0c], img[y0c, x0c + 1]
    c, d = img[y0c + 1, x0c], img[y0c + 1, x0c + 1]
    out = a * (1 - dx) * (1 - dy) + b * dx * (1 - dy) + c * (1 - dx) * dy + d * dx * dy
    out[~ok] = np.nan
    return out


def frame_scale_arcsec(path):
    with fits.open(path) as h:
        return float(proj_plane_pixel_scales(WCS(h["IMAGE"].header).celestial)[0] * 3600)


def render(res, lim, png):
    """Symmetric asinh stretch: mid-grey = 0, white = brighter than the median, black = fainter."""
    import matplotlib.pyplot as plt
    v = np.nan_to_num(res, nan=0.0)
    x = np.sign(v) * np.arcsinh(6 * np.abs(v) / lim) / np.arcsinh(6)
    plt.imsave(png, np.clip(0.5 + 0.5 * x, 0, 1), cmap="gray", vmin=0, vmax=1, origin="lower")


def target_pixel(gw, ra, dec):
    x, y = gw.world_to_pixel(SkyCoord(ra * u.deg, dec * u.deg))
    return float(x), float(y)


def target_wavelength(path, ra, dec):
    """Wavelength (um) at the target in this cutout: WCS-WAVE lookup (IRSA cutouts) or the
    WAVETGT keyword written for AWS cutouts. None if neither is available."""
    with fits.open(path) as h:
        hdr = h["IMAGE"].header
        try:
            x, y = WCS(hdr).celestial.world_to_pixel(SkyCoord(ra * u.deg, dec * u.deg))
            sw = WCS(hdr, fobj=h, key="W")
            sw.sip = None
            w, _ = sw.pixel_to_world(float(x), float(y))
            return float(u.Quantity(w).to_value(u.um))
        except Exception:
            pass
        v = hdr.get("WAVETGT")
        return float(v) if v is not None else None


def references(wls, tol):
    """For frame i: indices j != i with |wl_j - wl_i| <= tol * wl_i (same LVF wavelength)."""
    out = []
    for i, wi in enumerate(wls):
        if wi is None:
            out.append([])
            continue
        out.append([j for j, wj in enumerate(wls) if j != i and wj is not None and abs(wj - wi) <= tol * wi])
    return out
