"""StormScope UK backend. Run: uvicorn main:app --host 0.0.0.0 --port 8000"""
import time, asyncio, pathlib, logging
import httpx
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
import opera
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("stormscope")
app = FastAPI(title="StormScope UK")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
client = httpx.Client(headers={"User-Agent": "StormScope-UK/0.1 (personal use)"})
RV = "https://api.rainviewer.com/public/weather-maps.json"
_cache = {"frames": (0, None), "storms": (0, dict(status="starting", storms=[], lines=[]))}
_tiles, _paths = {}, {}
tracker = opera.Tracker()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/frames")
def frames():
    ts, data = _cache["frames"]
    if time.time() - ts > 60:
        try:
            r = client.get(RV, timeout=15); r.raise_for_status()
            d = r.json(); past = d["radar"]["past"]
            for f in past:
                _paths[f["path"].split("/")[-1]] = d["host"] + f["path"]
            data = dict(status="ok", source="RainViewer (observed, colour tiles)",
                        frames=[dict(id=f["path"].split("/")[-1], time=f["time"]) for f in past])
            _cache["frames"] = (time.time(), data)
        except Exception as e:
            return dict(status="unavailable", reason=str(e), frames=[])
    return data


@app.get("/api/tile/{fid}/{z}/{x}/{y}.png")
def tile(fid: str, z: int, x: int, y: int):
    key = (fid, z, x, y)
    hit = _tiles.get(key)
    if hit and time.time() - hit[0] < 1800:
        return Response(hit[1], media_type="image/png", headers={"Cache-Control": "max-age=1800"})
    base = _paths.get(fid)
    if not base:
        return Response(status_code=404)
    try:
        r = client.get(f"{base}/256/{z}/{x}/{y}/2/1_1.png", timeout=15)
        if r.status_code != 200:
            return Response(status_code=r.status_code)
    except Exception:
        return Response(status_code=502)
    if len(_tiles) > 4000:
        _tiles.clear()
    _tiles[key] = (time.time(), r.content)
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "max-age=1800"})


@app.get("/api/storms")
def storms():
    return _cache["storms"][1]


async def refresh_loop():
    while True:
        try:
            res = await asyncio.to_thread(opera.run, client, tracker)
        except Exception as e:
            log.exception("OPERA refresh failed")
            res = dict(status="unavailable", reason=f"OPERA refresh failed: {e}", storms=[], lines=[])
        _cache["storms"] = (time.time(), res)
        await asyncio.sleep(300)


@app.on_event("startup")
async def startup():
    asyncio.create_task(refresh_loop())

app.mount("/", StaticFiles(directory=str(pathlib.Path(__file__).parent.parent / "public"), html=True), name="static")
