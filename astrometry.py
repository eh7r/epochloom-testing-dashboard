"""Position propagation (proper motion) shared by /cutout, /photometry and the motion track."""
import warnings

from astropy import units as u
from astropy.coordinates import Distance, SkyCoord
from astropy.time import Time


def propagate(ra, dec, pmra, pmdec, epoch, mjd=None, jyear=None, parallax=None):
    """Move (ra, dec) [deg] given at Julian year `epoch` to MJD `mjd` (or Julian year `jyear`)
    using astropy SkyCoord.apply_space_motion. pmra is mu_alpha* (includes cos dec), mas/yr.
    Returns (ra, dec) in deg. Barycentric: the annual parallax wobble is not applied
    (it is < 1", far below SPHEREx's ~6" pixel)."""
    if epoch is None or (not pmra and not pmdec):
        return float(ra), float(dec)
    dist = Distance(parallax=parallax * u.mas) if parallax and parallax > 0 else None
    c = SkyCoord(ra * u.deg, dec * u.deg, distance=dist,
                 pm_ra_cosdec=pmra * u.mas / u.yr, pm_dec=pmdec * u.mas / u.yr,
                 obstime=Time(epoch, format="jyear"))
    t = Time(mjd, format="mjd") if mjd is not None else Time(jyear, format="jyear")
    with warnings.catch_warnings():  # no distance -> astropy warns and assumes a large one: fine here
        warnings.simplefilter("ignore")
        n = c.apply_space_motion(new_obstime=t)
    return float(n.ra.deg), float(n.dec.deg)
