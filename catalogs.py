"""Catalog lookups on IRSA (Gaia DR3, CatWISE2020, AllWISE, 2MASS PSC).

Uses IRSA's Gator cone search (same IRSA-hosted tables as IRSA TAP; Gator stayed
up on 2026-11-15 while IRSA TAP returned 502). Answers are cached on disk so a
demo survives IRSA outages. Every value returned comes from the catalog row;
anything computed is labelled by the caller.

Column names and units were checked against the live Gator output / data
dictionaries on 2026-11-15:
  gaia_dr3_source : ra, dec @ ref_epoch (2016.0), pmra, pmdec [mas/yr], parallax [mas]
  catwise_2020    : ra, dec, meanobsmjd [d], pmra, pmdec [arcsec/yr]
  allwise_p3as_psd: ra, dec, w1mjdmean [d], pmra, pmdec [mas/yr]
  fp_psc (2MASS)  : ra, dec, xdate (observation date)
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import requests
from astropy.io import ascii as aascii
from astropy.time import Time

GATOR = "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query"
CACHE = Path(__file__).resolve().parent / "cache"
CACHE.mkdir(exist_ok=True)

COLS = {
    "gaia_dr3_source": "source_id,ra,dec,ref_epoch,pmra,pmdec,parallax,phot_g_mean_mag",
    "catwise_2020": "source_name,source_id,ra,dec,meanobsmjd,pmra,pmdec,sigpmra,sigpmdec,w1mpro,w2mpro",
    "allwise_p3as_psd": "designation,ra,dec,w1mjdmean,pmra,pmdec,w1mpro,w2mpro",
    "fp_psc": "designation,ra,dec,xdate,j_m,h_m,k_m",
}
LABEL = {"gaia_dr3_source": "Gaia DR3", "catwise_2020": "CatWISE2020",
         "allwise_p3as_psd": "AllWISE", "fp_psc": "2MASS PSC"}


class CatalogError(Exception):
    pass


def cone(catalog, ra, dec, radius_arcsec, timeout=60):
    """Rows of `catalog` within radius of (ra, dec) as a list of dicts (with 'dist' in arcsec)."""
    if catalog not in COLS:
        raise CatalogError(f"unknown catalog {catalog}")
    params = {"catalog": catalog, "spatial": "cone", "objstr": f"{ra:.7f} {dec:+.7f}",
              "radius": f"{radius_arcsec:.3f}", "radunits": "arcsec", "outfmt": "1",
              "selcols": COLS[catalog]}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    path = CACHE / f"cat_{key}.json"
    try:
        r = requests.get(GATOR, params=params, timeout=timeout)
        r.raise_for_status()
        if "[struct stat=\"ERROR\"" in r.text[:500]:
            raise CatalogError(r.text[:300])
        rows = _parse(r.text)
        path.write_text(json.dumps(rows))
        return rows
    except Exception as e:
        if path.exists():
            print(f"[catalog] {catalog} live query failed ({e}); serving cached answer")
            return json.loads(path.read_text())
        raise CatalogError(f"IRSA {LABEL.get(catalog, catalog)} query failed: {e}")


def _parse(text):
    if "|" not in text:  # no rows -> Gator returns a header-only or empty table
        return []
    t = aascii.read(text.splitlines(), format="ipac")
    out = []
    for row in t:
        d = {}
        for c in t.colnames:
            v = row[c]
            if np.ma.is_masked(v):
                d[c] = None
            elif isinstance(v, (np.integer,)):
                d[c] = int(v)
            elif isinstance(v, (np.floating, float)):
                d[c] = float(v)
            else:
                d[c] = str(v)
        out.append(d)
    return out


def epoch_of(catalog, row):
    """(julian_year, mjd, note) at which the row's ra/dec apply."""
    if catalog == "gaia_dr3_source":
        jy = float(row["ref_epoch"])
        return jy, float(Time(jy, format="jyear").mjd), "Gaia DR3 ref_epoch"
    if catalog == "catwise_2020":
        m = float(row["meanobsmjd"])
        return float(Time(m, format="mjd").jyear), m, "CatWISE2020 meanobsmjd (mean observation epoch)"
    if catalog == "allwise_p3as_psd":
        m = row.get("w1mjdmean")
        if m is None:
            return None, None, "AllWISE: no w1mjdmean"
        return float(Time(m, format="mjd").jyear), float(m), "AllWISE w1mjdmean (mean W1 epoch)"
    if catalog == "fp_psc":
        t = Time(str(row["xdate"]))
        return float(t.jyear), float(t.mjd), "2MASS xdate (observation date)"
    raise CatalogError(catalog)


def pm_mas(catalog, row):
    """(pmra, pmdec) in mas/yr, or (None, None)."""
    a, d = row.get("pmra"), row.get("pmdec")
    if a is None or d is None:
        return None, None
    f = 1000.0 if catalog == "catwise_2020" else 1.0  # CatWISE2020 is arcsec/yr
    return round(float(a) * f, 4), round(float(d) * f, 4)


# --------------------------------------------------------------------------
# Demo targets. `seed` is only WHERE we search (an approximate 2025.6 position
# computed earlier); every returned value comes from the selected catalog row.
# --------------------------------------------------------------------------
TARGETS = {
    "barnard": dict(name="Barnard's Star", seed=(269.446357, 4.767053), radius=150,
                    tries=[("gaia_dr3_source", "max_pm", 1000)]),
    "rwaur": dict(name="RW Aurigae A", seed=(76.956108, 30.401151), radius=10,
                  tries=[("gaia_dr3_source", "brightest", None)]),
    "rdra": dict(name="R Draconis", seed=(248.1675, 66.755), radius=60,
                 tries=[("gaia_dr3_source", "brightest", None)]),
    # Gaia DR3 does contain eps Indi B (Ba+Bb unresolved; parallax ~271 mas, same as eps Indi A).
    # If ever not found, fall back to CatWISE2020.
    "epsindib": dict(name="ε Indi Ba/Bb (unresolved)", seed=(331.096267, -56.801090), radius=150,
                     tries=[("gaia_dr3_source", "max_pm", 1000), ("catwise_2020", "best_pm", 1000)]),
    # Not in Gaia (too faint/red): CatWISE2020.
    "wise0855": dict(name="WISE 0855−0714", seed=(133.760417, -7.242292), radius=150,
                     tries=[("catwise_2020", "best_pm", 5000)]),
}
RULE_TEXT = {
    "max_pm": "highest total proper motion (≥ {min} mas/yr) within {r}\" of the search position",
    "brightest": "brightest Gaia G within {r}\" of the search position",
    "best_pm": "smallest proper-motion uncertainty among rows moving ≥ {min} mas/yr within {r}\" "
               "(CatWISE2020 lists fast movers more than once)",
}


def _pmtot(cat, row):
    a, d = pm_mas(cat, row)
    return float(np.hypot(a, d)) if a is not None else 0.0


def astrometry(key):
    t = TARGETS.get(key)
    if not t:
        raise CatalogError(f"unknown target {key}; known: {', '.join(TARGETS)}")
    ra0, dec0 = t["seed"]
    notes = []
    for cat, rule, pmmin in t["tries"]:
        rows = cone(cat, ra0, dec0, t["radius"])
        if rule == "brightest":
            cand = sorted((r for r in rows if r.get("phot_g_mean_mag") is not None), key=lambda r: r["phot_g_mean_mag"])
        else:
            cand = [r for r in rows if _pmtot(cat, r) >= pmmin]
            if rule == "max_pm":
                cand.sort(key=lambda r: -_pmtot(cat, r))
            else:
                cand.sort(key=lambda r: np.hypot(r.get("sigpmra") or 9e9, r.get("sigpmdec") or 9e9))
        if not cand:
            notes.append(f"not found in {LABEL[cat]} by rule '{rule}'")
            continue
        row = cand[0]
        jy, mjd, enote = epoch_of(cat, row)
        pmra, pmdec = pm_mas(cat, row)
        out = {
            "target": key, "name": t["name"],
            "ra": row["ra"], "dec": row["dec"], "ref_epoch": jy, "ref_epoch_mjd": mjd, "epoch_note": enote,
            "pmra": pmra, "pmdec": pmdec, "parallax": row.get("parallax"),
            "source_table": cat, "catalog": LABEL[cat],
            "source_id": str(row.get("source_id") or row.get("source_name")),
            "source_name": row.get("source_name"),
            "mag": ({"G": row["phot_g_mean_mag"]} if cat == "gaia_dr3_source" else
                    {"W1": row.get("w1mpro"), "W2": row.get("w2mpro")}),
            "selection": RULE_TEXT[rule].format(min=pmmin, r=t["radius"]),
            "candidates": len(cand),
            "service": "IRSA Gator cone search (" + GATOR + ")",
            "notes": notes,
        }
        if cat == "catwise_2020":
            out["pm_units_in_catalog"] = "arcsec/yr (converted to mas/yr here)"
            out["other_rows"] = [{"ra": r["ra"], "dec": r["dec"], "meanobsmjd": r["meanobsmjd"],
                                  "source_name": r["source_name"]} for r in cand[1:]]
        return out
    raise CatalogError(f"{t['name']}: " + "; ".join(notes))
