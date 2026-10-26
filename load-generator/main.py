import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from engine import DEFAULT_TARGET_URL, LoadConfig, LoadUpdate, engine, monitor
from scenarios import get_scenario, list_scenarios

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Load Generator")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def _start_monitor() -> None:
    asyncio.create_task(monitor.run())


@app.get("/health")
def health():
    return {"status": "ok", "service": "load-generator"}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/scenarios")
def scenarios():
    return {"scenarios": list_scenarios()}


@app.post("/api/load/start")
async def start(cfg: LoadConfig):
    await engine.start(cfg)
    return {"status": "started", "config": cfg.model_dump()}


@app.post("/api/scenarios/{scenario_id}/run")
async def run_scenario(scenario_id: str, target_url: Optional[str] = None):
    scenario = get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
    data = {**scenario["config"], "scenario_name": scenario_id}
    if target_url:
        data["target_url"] = target_url
    await engine.start(LoadConfig(**data))
    return {"status": "started", "scenario": scenario_id, "name": scenario["name"]}


@app.post("/api/load/stop")
async def stop():
    await engine.stop()
    return {"status": "stopped", "last_run": engine.history[0] if engine.history else None}


@app.post("/api/load/update")
def update(upd: LoadUpdate):
    return engine.update(upd)


@app.get("/api/load/status")
def status():
    return engine.status()


@app.get("/api/history")
def history():
    return {"history": engine.history}


class CapacityRequest(BaseModel):
    worker_slots: int = Field(ge=0, le=1000)


async def _app_capacity(method: str, body: Optional[dict] = None) -> dict:
    """Proxy to the demo app's capacity API so the browser needs only this origin."""
    target = (engine.config.target_url if engine.config and engine.config.target_url else DEFAULT_TARGET_URL)
    url = f"{target.rstrip('/')}/api/admin/capacity"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
            async with s.request(method, url, json=body) as r:
                return await r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"demo app unreachable at {url}: {exc}") from exc


@app.get("/api/app/capacity")
async def get_app_capacity():
    return await _app_capacity("GET")


@app.post("/api/app/capacity")
async def set_app_capacity(req: CapacityRequest):
    return await _app_capacity("POST", req.model_dump())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
