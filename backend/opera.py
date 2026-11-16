"""OPERA composite acquisition, decoding, cell detection and tracking.
Data: EUMETNET Open Radar Data public S3 24h cache (no credentials).
Only observed reflectivity (DBZH) is used. No Doppler velocity exists in this product,
so rotation/supercell/tornado classification is never attempted here."""
import io, re, math, datetime as dt
import numpy as np, h5py, httpx
from pyproj import Transformer
from scipy import ndimage

S3 = "https://s3.waw3-1.cloudferro.com/openradar-24h"
BBOX = (48.0, 62.0, -12.0, 5.0)  # lat0, lat1, lon0, lon1 (UK, Ireland, Irish Sea)
CELL_DBZ, MIN_PIX = 40.0, 6
LINE_DBZ, LINE_KM, LINE_ASPECT = 35.0, 100.0, 4.0
MAX_MATCH_KM = 45.0  # per 15 min step


def _s(v):
    return v.decode() if isinstance(v, bytes) else v


def list_keys(client, day: dt.date):
    url = f"{S3}?list-type=2&prefix={day:%Y/%m/%d}/OPERA/COMP/"
    r = client.get(url, timeout=30); r.raise_for_status()
    keys = re.findall(r"<Key>([^<]*@DBZH\.h5)</Key>", r.text)
    out = {}
    for k in keys:
        m = re.search(r"@(\d{8}T\d{4})@", k)
        if m:
            out[dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(tzinfo=dt.timezone.utc)] = k
    return out


def available(client, n=6):
    now = dt.datetime.now(dt.timezone.utc)
    found = {}
    for d in (now.date() - dt.timedelta(days=1), now.date()):
        try:
            found.update(list_keys(client, d))
        except Exception:
            pass
    return sorted(found.items())[-n:]


def decode(blob: bytes):
    """Return (dbz 2D float32 cropped to BBOX, x_km_origin helpers) from one ODIM file."""
    with h5py.File(io.BytesIO(blob), "r") as f:
        where = f["where"].attrs
        proj = _s(where["projdef"])
        xs, ys = int(where["xsize"]), int(where["ysize"])
        dx, dy = float(where["xscale"]), float(where["yscale"])
        tr = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
        x0, y0 = tr.transform(float(where["UL_lon"]), float(where["UL_lat"]))
        ds = f["dataset1"]; d = ds["data1"]
        what = dict(d["what"].attrs) if "what" in d else {}
        if "gain" not in what and "what" in ds:
            what = {**dict(ds["what"].attrs), **what}
        raw = d["data"][...]
    gain, off = float(what.get("gain", 1)), float(what.get("offset", 0))
    nodata, undet = float(what.get("nodata", -1e9)), float(what.get("undetect", -1e9))
    # crop to BBOX first (full grid is large)
    la = np.linspace(BBOX[0], BBOX[1], 30); lo = np.linspace(BBOX[2], BBOX[3], 30)
    gx, gy = tr.transform(*np.meshgrid(lo, la))
    c0 = max(int((gx.min() - x0) / dx), 0); c1 = min(int((gx.max() - x0) / dx) + 1, xs)
    r0 = max(int((y0 - gy.max()) / dy), 0); r1 = min(int((y0 - gy.min()) / dy) + 1, ys)
    sub = raw[r0:r1, c0:c1].astype(np.float32)
    dbz = sub * gain + off
    dbz[(sub == nodata)] = np.nan
    dbz[(sub == undet)] = -99.0
    inv = Transformer.from_crs(proj, "EPSG:4326", always_xy=True)
    return dbz, dict(x0=x0 + c0 * dx, y0=y0 - r0 * dy, dx=dx, dy=dy, inv=inv)


def _ll(g, rows, cols):
    x = g["x0"] + (np.asarray(cols) + .5) * g["dx"]
    y = g["y0"] - (np.asarray(rows) + .5) * g["dy"]
    lon, lat = g["inv"].transform(x, y)
    return np.asarray(lat), np.asarray(lon)


def detect(dbz, g):
    """Return convective cells and squall-line candidates from one scan."""
    km = g["dx"] / 1000.0
    cells, lines = [], []
    lab, n = ndimage.label(np.nan_to_num(dbz, nan=-99) >= CELL_DBZ)
    for i in range(1, n + 1):
        rr, cc = np.nonzero(lab == i)
        if len(rr) < MIN_PIX:
            continue
        lat, lon = _ll(g, rr.mean(), cc.mean())
        cells.append(dict(lat=float(lat), lon=float(lon), max_dbz=float(np.nanmax(dbz[rr, cc])),
                          area_km2=float(len(rr) * km * km)))
    lab, n = ndimage.label(np.nan_to_num(dbz, nan=-99) >= LINE_DBZ)
    for i in range(1, n + 1):
        rr, cc = np.nonzero(lab == i)
        if len(rr) < 40:
            continue
        pts = np.stack([cc, rr], 1).astype(float) * km
        cov = np.cov((pts - pts.mean(0)).T); w, v = np.linalg.eigh(cov)
        length, width = 4 * math.sqrt(max(w[1], 0)), 4 * math.sqrt(max(w[0], 1e-6))
        if length >= LINE_KM and length / width >= LINE_ASPECT:
            hi = float((dbz[rr, cc] >= 45).mean())
            lat, lon = _ll(g, rr.mean(), cc.mean())
            order = np.argsort(pts @ v[:, 1])
            ends = [order[0], order[-1]]
            elat, elon = _ll(g, rr[ends], cc[ends])
            lines.append(dict(lat=float(lat), lon=float(lon), length_km=round(length), width_km=round(width),
                              max_dbz=float(np.nanmax(dbz[rr, cc])), frac_ge45=round(hi, 2),
                              axis=[[float(elat[0]), float(elon[0])], [float(elat[1]), float(elon[1])]]))
    return cells, lines


def _dist(a, b):
    dlat = (a["lat"] - b["lat"]) * 111.2
    dlon = (a["lon"] - b["lon"]) * 111.2 * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlon)


class Tracker:
    """Nearest-neighbour cell tracker. IDs persist across refreshes while the process runs."""
    def __init__(self):
        self.tracks, self.next_id, self.seen = {}, 1, set()

    def update(self, t: dt.datetime, cells):
        if t in self.seen:
            return
        self.seen.add(t)
        free = {k: v for k, v in self.tracks.items() if v["active"]}
        for c in sorted(cells, key=lambda c: -c["max_dbz"]):
            best, bd = None, MAX_MATCH_KM
            for k, tr in free.items():
                d = _dist(tr["hist"][-1], c)
                if d < bd and (t - tr["hist"][-1]["t"]).total_seconds() <= 1800:
                    best, bd = k, d
            if best is None:
                best = self.next_id; self.next_id += 1
                self.tracks[best] = dict(first=t, hist=[], active=True)
            else:
                free.pop(best, None)
            self.tracks[best]["hist"].append({**c, "t": t})
            self.tracks[best]["last"] = t
        for k, tr in self.tracks.items():
            if tr["last"] != t:
                tr["active"] = False

    def export(self, now_t):
        out = []
        for k, tr in self.tracks.items():
            if tr["last"] != now_t:
                continue
            h = tr["hist"]; cur = h[-1]
            item = dict(id=f"C{k:04d}", first_seen=tr["first"].isoformat(), observed_at=cur["t"].isoformat(),
                        lat=cur["lat"], lon=cur["lon"], max_dbz=cur["max_dbz"], scans=len(h),
                        trail=[[p["lat"], p["lon"]] for p in h],
                        dbz_history=[round(p["max_dbz"], 1) for p in h])
            if len(h) >= 3:
                dtm = (h[-1]["t"] - h[0]["t"]).total_seconds() / 3600
                dn = (cur["lat"] - h[0]["lat"]) * 111.2
                de = (cur["lon"] - h[0]["lon"]) * 111.2 * math.cos(math.radians(cur["lat"]))
                spd = math.hypot(dn, de) / dtm if dtm else 0
                brg = (math.degrees(math.atan2(de, dn)) + 360) % 360
                item.update(speed_kmh=round(spd), bearing_deg=round(brg),
                            trend="intensifying" if h[-1]["max_dbz"] - h[0]["max_dbz"] >= 3 else
                                  "weakening" if h[0]["max_dbz"] - h[-1]["max_dbz"] >= 3 else "steady",
                            extrapolated=[dict(minutes=m,
                                lat=cur["lat"] + dn / dtm * m / 60 / 111.2,
                                lon=cur["lon"] + de / dtm * m / 60 / (111.2 * math.cos(math.radians(cur["lat"]))))
                                for m in (15, 30, 60)])
            item["classification"] = "Convective cell (algorithmic detection)"
            item["rotation"] = "insufficient data: no Doppler velocity in OPERA composites"
            item["confidence"] = "low" if len(h) < 3 else "moderate"
            out.append(item)
        return out


def run(client, tracker):
    scans = available(client)
    if not scans:
        return dict(status="unavailable", reason="No OPERA composite files found in the 24h cache", storms=[], lines=[])
    if not hasattr(tracker,'lines'): tracker.lines=[]
    for t, key in scans:
        if t in tracker.seen:
            continue
        r = client.get(f"{S3}/{key}", timeout=120); r.raise_for_status()
        dbz, g = decode(r.content)
        cells, lines = detect(dbz, g)
        tracker.update(t, cells)
        if t == scans[-1][0]: tracker.lines = lines
    t = scans[-1][0]
    age = (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 60
    return dict(status="ok", source="EUMETNET OPERA composite DBZH (observed)", scan_time=t.isoformat(),
                age_minutes=round(age), delayed=age > 40, scans_used=len(scans), storms=tracker.export(t),
                lines=tracker.lines,
                notes="Detections are algorithmic. Motion is extrapolation, not a forecast. Not an official warning.")
