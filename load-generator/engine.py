"""Load engine: Fixed Users (closed model) and Fixed Rate (open model).

Little's Law ties the two together:  in-flight = TPS x response time.
  * Fixed Users  — N virtual users each loop: send, wait for the reply, think.
                   TPS is the *outcome*:  TPS = users / (response time + think time).
  * Fixed Rate   — requests start on a schedule at the target TPS whether or not
                   earlier ones have finished. Concurrency is the outcome:
                   users needed = target TPS x response time. If that exceeds
                   max_users the generator can't keep up and skips requests.
"""
import asyncio
import logging
import os
import random
import statistics
import time
from collections import deque
from typing import Any, Deque, Dict, List, Literal, Optional, Tuple

import aiohttp
from fastapi import HTTPException
from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("loadgen")

DEFAULT_TARGET_URL = os.getenv("TARGET_URL", "http://demo-app:8000").rstrip("/")
WINDOW_SECONDS = 5.0           # sliding window for live TPS / response time
INTENTIONAL_ERROR_PATH = "/api/error"

# ---------------------------------------------------------------------------
# Prometheus metrics. Names of the original metrics are unchanged so existing
# dashboards and alerts keep working; "rps" in a metric name means TPS here.
# ---------------------------------------------------------------------------
REQUESTS_TOTAL = Counter("loadgen_requests_total", "Requests completed by the load generator",
                         ["method", "endpoint", "status_code", "scenario"])
REQUEST_DURATION = Histogram("loadgen_request_duration_seconds", "Client-observed response time",
                             ["endpoint", "status_code"],
                             buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0])
IN_FLIGHT = Gauge("loadgen_requests_in_flight", "Requests sent and waiting for a response")
TARGET_TPS = Gauge("loadgen_target_rps", "Target TPS (Fixed Rate mode; 0 in Fixed Users mode)")
ACHIEVED_TPS = Gauge("loadgen_achieved_rps", "Measured TPS over the last 5 seconds")
ERRORS_TOTAL = Counter("loadgen_errors_total", "Error responses by type "
                       "(intentional_5xx = expected errors from /api/error)", ["error_type"])
USERS = Gauge("loadgen_concurrent_users", "Virtual users: configured (Fixed Users) or currently busy (Fixed Rate)")
SCENARIO_ACTIVE = Gauge("loadgen_scenario_active", "1 for the scenario currently running", ["scenario_name"])
BYTES_SENT = Counter("loadgen_bytes_sent_total", "Approximate request bytes sent")
BYTES_RECEIVED = Counter("loadgen_bytes_received_total", "Approximate response bytes received")
POOL_SIZE = Gauge("loadgen_connection_pool_size", "HTTP connection pool size")

MODE = Gauge("loadgen_mode", "1 for the active load model", ["mode"])
THINK_TIME = Gauge("loadgen_think_time_seconds", "Configured think time per virtual user")
MAX_USERS = Gauge("loadgen_max_users", "Fixed Rate: most requests allowed in flight at once")
SKIPPED_TOTAL = Counter("loadgen_skipped_requests_total",
                        "Fixed Rate: scheduled requests not sent because max_users were all busy")
AVG_RESPONSE = Gauge("loadgen_avg_response_seconds", "Average response time over the last 5 seconds")
PREDICTED_MAX_TPS = Gauge("loadgen_predicted_max_tps",
                          "Little's Law ceiling: users / (response time + think time)")
REQUIRED_USERS = Gauge("loadgen_required_users", "Little's Law: users needed = target TPS x response time")
LITTLE_L = Gauge("loadgen_littles_law_in_flight_expected", "TPS x response time (should match in-flight)")
CPU_RATIO = Gauge("loadgen_cpu_utilization_ratio", "Load generator CPU use as a fraction of its CPU limit")
LOOP_LAG = Gauge("loadgen_event_loop_lag_seconds", "How late the generator's event loop runs (overload signal)")
RT_INFLATION = Gauge("loadgen_response_time_inflation_ratio",
                     "Current response time / best response time seen this run (queueing signal)")
DIAGNOSIS = Gauge("loadgen_diagnosis", "1 for the current diagnosis state", ["state"])

DIAGNOSIS_STATES = ["idle", "warming_up", "healthy", "not_enough_users", "app_saturated",
                    "generator_overloaded", "failing"]
MODES = ["fixed_users", "fixed_rate"]


def _reset_gauges() -> None:
    for g in (IN_FLIGHT, TARGET_TPS, ACHIEVED_TPS, USERS, POOL_SIZE, THINK_TIME, MAX_USERS,
              AVG_RESPONSE, PREDICTED_MAX_TPS, REQUIRED_USERS, LITTLE_L, RT_INFLATION):
        g.set(0)
    for m in MODES:
        MODE.labels(mode=m).set(0)
    for s in DIAGNOSIS_STATES:
        DIAGNOSIS.labels(state=s).set(1 if s == "idle" else 0)


_reset_gauges()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class LoadConfig(BaseModel):
    mode: Literal["fixed_users", "fixed_rate"] = "fixed_rate"
    target_url: Optional[str] = None
    # Fixed Users
    virtual_users: int = Field(default=10, ge=1, le=200)
    think_time_ms: int = Field(default=0, ge=0, le=10000)
    # Fixed Rate
    target_tps: float = Field(default=10.0, ge=0.5, le=1000.0)
    max_users: int = Field(default=50, ge=1, le=500)
    # Both
    duration_seconds: int = Field(default=0, ge=0, le=7200)   # 0 = until stopped
    ramp_up_seconds: int = Field(default=5, ge=0, le=600)
    ramp_down_seconds: int = Field(default=0, ge=0, le=600)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    # CPU the generator burns per request, like a heavy test script parsing and
    # checking responses. Lets you overload the generator itself on purpose.
    script_cpu_ms: float = Field(default=0.0, ge=0.0, le=50.0)
    traffic_mix: Dict[str, float] = Field(default_factory=lambda: {
        "GET /api/order?tier=gold": 60,
        "GET /api/order?tier=silver": 25,
        "GET /api/slow?delay=0.3": 10,
        "GET /api/error": 5,
    })
    chaos: bool = False
    app_worker_slots: Optional[int] = Field(default=None, ge=0, le=1000)  # configure the demo app for the run
    scenario_name: str = "custom"

    @model_validator(mode="before")
    @classmethod
    def _legacy_fields(cls, data: Any) -> Any:
        """Accept the pre-v3 API (target_rps / concurrency)."""
        if isinstance(data, dict):
            data = dict(data)
            if "target_rps" in data and "target_tps" not in data:
                data["target_tps"] = data.pop("target_rps")
            if "concurrency" in data:
                conc = data.pop("concurrency")
                data.setdefault("max_users", conc)
                data.setdefault("virtual_users", conc)
        return data


class LoadUpdate(BaseModel):
    target_tps: Optional[float] = Field(None, ge=0.5, le=1000.0)
    max_users: Optional[int] = Field(None, ge=1, le=500)
    virtual_users: Optional[int] = Field(None, ge=1, le=200)
    think_time_ms: Optional[int] = Field(None, ge=0, le=10000)
    script_cpu_ms: Optional[float] = Field(None, ge=0.0, le=50.0)

    @model_validator(mode="before")
    @classmethod
    def _legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "target_rps" in data:
                data.setdefault("target_tps", data.pop("target_rps"))
            if "concurrency" in data:
                conc = data.pop("concurrency")
                data.setdefault("max_users", conc)
                data.setdefault("virtual_users", conc)
        return data


# ---------------------------------------------------------------------------
# Generator self-monitoring (the "is my test tool the bottleneck?" signal)
# ---------------------------------------------------------------------------
def cpu_limit_cores() -> float:
    """CPU limit of this container (cgroup v2, then v1), else the host core count."""
    try:
        quota, period = open("/sys/fs/cgroup/cpu.max").read().split()
        if quota != "max":
            return int(quota) / int(period)
    except (OSError, ValueError):
        pass
    try:
        quota = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
        period = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
        if quota > 0:
            return quota / period
    except (OSError, ValueError):
        pass
    return float(os.cpu_count() or 1)


CPU_LIMIT = cpu_limit_cores()


class SelfMonitor:
    """Samples this process's CPU use and asyncio event-loop lag."""

    def __init__(self) -> None:
        self.cpu_ratio = 0.0
        self.loop_lag = 0.0
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        # don't count interpreter start-up / imports as load
        self._last_wall, self._last_cpu = time.monotonic(), time.process_time()
        while True:
            expected = loop.time() + 0.25
            await asyncio.sleep(0.25)
            lag = max(0.0, loop.time() - expected)
            self.loop_lag = 0.7 * self.loop_lag + 0.3 * lag
            now_wall, now_cpu = time.monotonic(), time.process_time()
            dw = now_wall - self._last_wall
            if dw >= 1.0:
                used_cores = (now_cpu - self._last_cpu) / dw
                self.cpu_ratio = used_cores / CPU_LIMIT if CPU_LIMIT > 0 else 0.0
                self._last_wall, self._last_cpu = now_wall, now_cpu
                CPU_RATIO.set(round(self.cpu_ratio, 3))
            LOOP_LAG.set(round(self.loop_lag, 4))


monitor = SelfMonitor()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
Sample = Tuple[float, float, str, str]   # (finished_at, response_seconds, endpoint, outcome)


class LoadEngine:
    def __init__(self) -> None:
        self.running = False
        self.config: Optional[LoadConfig] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.tasks: set = set()
        self.user_tasks: Dict[int, asyncio.Task] = {}
        self.control_task: Optional[asyncio.Task] = None
        self.dispatch_task: Optional[asyncio.Task] = None
        self.traffic: List[Tuple[str, str, float]] = []

        # live values (may differ from config during ramp-up / chaos)
        self.cur_users = 0
        self.cur_tps = 0.0
        self.in_flight = 0
        self.dispatch_behind = 0.0       # Fixed Rate: how far behind schedule the dispatcher runs

        self.samples: Deque[Sample] = deque(maxlen=20000)
        self.in_flight_samples: Deque[Tuple[float, int]] = deque(maxlen=200)
        self.skipped_times: Deque[float] = deque(maxlen=20000)
        self.baseline: Dict[str, float] = {}
        self.prev_app_slots: Optional[int] = None
        self.started_at = 0.0
        self.state = "IDLE"
        self.counts = self._zero_counts()
        self.snapshot: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []

    @staticmethod
    def _zero_counts() -> Dict[str, int]:
        return {"sent": 0, "ok": 0, "intentional_errors": 0, "failures": 0, "skipped": 0}

    # -- traffic mix -------------------------------------------------------
    @staticmethod
    def _parse_mix(mix: Dict[str, float]) -> List[Tuple[str, str, float]]:
        total = sum(w for w in mix.values() if w > 0)
        if total <= 0:
            return [("GET", "/api/order?tier=gold", 1.0)]
        out, cum = [], 0.0
        for entry, weight in mix.items():
            if weight <= 0:
                continue
            parts = entry.strip().split(" ", 1)
            method, path = (parts[0].upper(), parts[1]) if len(parts) == 2 else ("GET", parts[0])
            cum += weight / total
            out.append((method, path, cum))
        return out

    def _pick(self) -> Tuple[str, str]:
        r = random.random()
        for method, path, cum in self.traffic:
            if r <= cum:
                return method, path
        return self.traffic[-1][0], self.traffic[-1][1]

    # -- one request ---------------------------------------------------------
    async def _send_one(self) -> None:
        cfg = self.config
        method, path = self._pick()
        endpoint = path.split("?")[0]
        url = f"{(cfg.target_url or DEFAULT_TARGET_URL).rstrip('/')}{path}"
        self.in_flight += 1
        IN_FLIGHT.inc()
        BYTES_SENT.inc(120)
        t0 = time.perf_counter()
        status = "exception"
        outcome = "failure"
        try:
            async with self.session.request(method, url, timeout=aiohttp.ClientTimeout(total=cfg.timeout_seconds)) as resp:
                body = await resp.read()
                BYTES_RECEIVED.inc(len(body) + 150)
                status = str(resp.status)
                if resp.status < 400:
                    outcome = "ok"
                elif resp.status >= 500 and endpoint == INTENTIONAL_ERROR_PATH:
                    outcome = "intentional_error"
                    ERRORS_TOTAL.labels(error_type="intentional_5xx").inc()
                else:
                    ERRORS_TOTAL.labels(error_type="http_5xx" if resp.status >= 500 else "http_4xx").inc()
        except asyncio.TimeoutError:
            status = "timeout"
            ERRORS_TOTAL.labels(error_type="timeout").inc()
        except aiohttp.ClientConnectorError:
            status = "conn_refused"
            ERRORS_TOTAL.labels(error_type="connection_refused").inc()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - any other client failure is a failed request
            ERRORS_TOTAL.labels(error_type="other").inc()
        finally:
            self.in_flight -= 1
            IN_FLIGHT.dec()
        if cfg.script_cpu_ms:
            # Busy CPU work on the event loop, exactly where a heavy script would run.
            end = time.perf_counter() + cfg.script_cpu_ms / 1000.0
            while time.perf_counter() < end:
                pass
        rt = time.perf_counter() - t0
        REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status_code=status, scenario=cfg.scenario_name).inc()
        REQUEST_DURATION.labels(endpoint=endpoint, status_code=status).observe(rt)
        self.counts["sent"] += 1
        self.counts[{"ok": "ok", "intentional_error": "intentional_errors"}.get(outcome, "failures")] += 1
        self.samples.append((time.monotonic(), rt, endpoint, outcome))

    # -- Fixed Users ---------------------------------------------------------
    async def _user_loop(self, user_id: int) -> None:
        while self.running and user_id < self.cur_users:
            await self._send_one()
            think = self.config.think_time_ms / 1000.0
            if think > 0:
                await asyncio.sleep(think * random.uniform(0.75, 1.25))
        self.user_tasks.pop(user_id, None)

    def _set_users(self, n: int) -> None:
        self.cur_users = n
        for uid in range(n):
            if uid not in self.user_tasks:
                self.user_tasks[uid] = self._spawn(self._user_loop(uid))
        # users with id >= n finish their current request and exit on their own

    # -- Fixed Rate ----------------------------------------------------------
    async def _dispatch_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while self.running:
            rate = self.cur_tps
            if rate <= 0:
                await asyncio.sleep(0.1)
                next_at = loop.time()
                continue
            next_at += random.expovariate(rate)          # Poisson arrivals, like real traffic
            delay = next_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
                self.dispatch_behind = 0.0
            else:
                self.dispatch_behind = -delay
                if -delay > 0.5:                          # behind schedule: skip ahead rather than burst
                    next_at = loop.time()
            if not self.running:
                break
            if self.in_flight >= self.config.max_users:
                self.counts["skipped"] += 1
                SKIPPED_TOTAL.inc()
                self.skipped_times.append(time.monotonic())
                continue
            self._spawn(self._send_one())

    # -- task bookkeeping ----------------------------------------------------
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # -- control loop: ramping, chaos, live stats, diagnosis -----------------
    async def _control_loop(self) -> None:
        cfg = self.config
        last_chaos = 0.0
        chaos_factor = 1.0
        while self.running:
            elapsed = time.monotonic() - self.started_at
            if cfg.duration_seconds and elapsed >= cfg.duration_seconds:
                asyncio.create_task(self.stop())
                return

            # ramp factor 0..1
            factor = 1.0
            self.state = "RUNNING"
            if cfg.ramp_up_seconds and elapsed < cfg.ramp_up_seconds:
                factor = max(elapsed / cfg.ramp_up_seconds, 0.02)
                self.state = "RAMP_UP"
            elif cfg.duration_seconds and cfg.ramp_down_seconds and elapsed > cfg.duration_seconds - cfg.ramp_down_seconds:
                factor = max((cfg.duration_seconds - elapsed) / cfg.ramp_down_seconds, 0.02)
                self.state = "RAMP_DOWN"
            if cfg.chaos:
                if elapsed - last_chaos >= 5:
                    chaos_factor = random.uniform(0.2, 1.0)
                    last_chaos = elapsed
                factor *= chaos_factor

            if cfg.mode == "fixed_users":
                users = max(1, round(cfg.virtual_users * factor))
                if users != self.cur_users:
                    self._set_users(users)
            else:
                self.cur_tps = max(0.5, cfg.target_tps * factor)

            self.in_flight_samples.append((time.monotonic(), self.in_flight))
            self._compute_snapshot()
            await asyncio.sleep(0.5)

    def _compute_snapshot(self) -> None:
        cfg = self.config
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        while self.samples and self.samples[0][0] < now - 60:
            self.samples.popleft()
        window = [s for s in self.samples if s[0] >= cutoff]
        span = min(WINDOW_SECONDS, max(now - self.started_at, 0.5))
        tps = len(window) / span
        rts = [s[1] for s in window]
        avg_rt = statistics.fmean(rts) if rts else 0.0
        srt = sorted(rts)

        def pct(p: float) -> float:
            return srt[min(len(srt) - 1, int(len(srt) * p))] if srt else 0.0

        flights = [n for t, n in self.in_flight_samples if t >= cutoff]
        avg_in_flight = statistics.fmean(flights) if flights else float(self.in_flight)
        skipped_window = sum(1 for t in self.skipped_times if t >= cutoff)
        failures_window = sum(1 for s in window if s[3] == "failure")
        think = cfg.think_time_ms / 1000.0

        # per-endpoint queueing signal: compare current median with the best seen this run
        inflation_num, inflation_den = 0.0, 0
        by_ep: Dict[str, List[float]] = {}
        for s in window:
            if s[3] != "failure":
                by_ep.setdefault(s[2], []).append(s[1])
        for ep, vals in by_ep.items():
            if len(vals) < 5:
                continue
            med = statistics.median(vals)
            best = self.baseline.get(ep)
            if best is None or med < best:
                self.baseline[ep] = best = med
            inflation_num += (med / max(best, 0.001)) * len(vals)
            inflation_den += len(vals)
        inflation = inflation_num / inflation_den if inflation_den else 1.0
        added_latency = avg_rt - statistics.fmean([self.baseline[ep] for ep in by_ep if ep in self.baseline] or [avg_rt])

        if cfg.mode == "fixed_users":
            users = self.cur_users
            predicted = users / (avg_rt + think) if (avg_rt + think) > 0 else 0.0
            required = 0.0
            target = 0.0
        else:
            users = self.in_flight
            predicted = cfg.max_users / avg_rt if avg_rt > 0 else 0.0
            required = self.cur_tps * avg_rt
            target = self.cur_tps

        diagnosis = self._diagnose(tps, target, avg_rt, inflation, added_latency, skipped_window,
                                   failures_window, len(window), required, predicted)

        ACHIEVED_TPS.set(round(tps, 2))
        TARGET_TPS.set(round(target, 2))
        USERS.set(users)
        THINK_TIME.set(think)
        MAX_USERS.set(cfg.max_users if cfg.mode == "fixed_rate" else 0)
        AVG_RESPONSE.set(round(avg_rt, 4))
        PREDICTED_MAX_TPS.set(round(predicted, 2))
        REQUIRED_USERS.set(round(required, 2))
        LITTLE_L.set(round(tps * avg_rt, 3))
        RT_INFLATION.set(round(inflation, 3))
        for s in DIAGNOSIS_STATES:
            DIAGNOSIS.labels(state=s).set(1 if s == diagnosis["state"] else 0)

        self.snapshot = {
            "tps": round(tps, 1),
            "target_tps": round(target, 1),
            "users": users,
            "in_flight": self.in_flight,
            "avg_in_flight": round(avg_in_flight, 1),
            "think_time_ms": cfg.think_time_ms,
            "avg_response_ms": round(avg_rt * 1000, 1),
            "p50_ms": round(pct(0.50) * 1000, 1),
            "p95_ms": round(pct(0.95) * 1000, 1),
            "p99_ms": round(pct(0.99) * 1000, 1),
            "predicted_max_tps": round(predicted, 1),
            "required_users": round(required, 1),
            "littles_law_expected_in_flight": round(tps * avg_rt, 1),
            "rt_inflation": round(inflation, 2),
            "skipped_last_5s": skipped_window,
            "failures_last_5s": failures_window,
            "diagnosis": diagnosis,
        }

    def _diagnose(self, tps, target, avg_rt, inflation, added_latency, skipped, failures, n,
                  required, predicted) -> Dict[str, str]:
        cfg = self.config
        elapsed = time.monotonic() - self.started_at

        def d(state, title, explain, action):
            return {"state": state, "title": title, "explain": explain, "action": action}

        if monitor.cpu_ratio >= 0.9 or monitor.loop_lag >= 0.1 or self.dispatch_behind >= 0.5:
            return d("generator_overloaded", "Load generator is overloaded",
                     f"The load generator is using {monitor.cpu_ratio:.0%} of its CPU limit "
                     f"({CPU_LIMIT:g} cores) and runs {monitor.loop_lag * 1000:.0f} ms late. "
                     "Its own slowness inflates response times and caps TPS — the numbers no longer describe the app.",
                     "Lower the load, raise the generator's CPU limit, or spread the test over more generators.")
        if n < 10 or elapsed < 3:
            return d("warming_up", "Warming up", "Collecting enough requests to measure.", "Give it a few seconds.")
        if failures / max(n, 1) >= 0.05:
            return d("failing", "Requests are failing",
                     f"{failures} of the last {n} requests timed out, were refused, or got an unexpected error.",
                     "Check that the app is running and reachable; lower the load if it is overwhelmed.")
        if inflation >= 1.6 and added_latency >= 0.05:
            return d("app_saturated", "App is saturated",
                     f"Response time is {inflation:.1f}x its best this run (+{added_latency * 1000:.0f} ms) — "
                     "requests are queueing inside the app. More load now adds waiting time, not TPS.",
                     "Reduce users/TPS to find the knee, or give the app more capacity (worker slots).")
        if cfg.mode == "fixed_rate" and skipped > 0:
            return d("not_enough_users", "Not enough virtual users",
                     f"To run {target:.0f} TPS at {avg_rt * 1000:.0f} ms per request you need about "
                     f"{required:.0f} requests in flight (Little's Law), but max users is {cfg.max_users}. "
                     f"{skipped} scheduled requests were skipped in the last 5 s.",
                     f"Raise max users to at least {required:.0f} (≈ {max(required * 1.3, required + 2):.0f} leaves headroom), "
                     "or make the app faster.")
        if cfg.mode == "fixed_rate":
            return d("healthy", "Healthy — keeping up with the target",
                     f"{tps:.1f} of {target:.0f} TPS with response time steady at {avg_rt * 1000:.0f} ms, "
                     f"using about {required:.0f} of {cfg.max_users} allowed users.",
                     "Raise the target TPS to look for the limit.")
        return d("healthy", "Healthy — TPS scales with users",
                 f"{self.cur_users} users ÷ ({avg_rt * 1000:.0f} ms response + {cfg.think_time_ms} ms think) "
                 f"≈ {predicted:.1f} TPS, and response time is steady.",
                 "Add users: while response time stays flat, TPS rises in proportion.")

    # -- demo app capacity ---------------------------------------------------
    async def _app_slots(self, value: Optional[int] = None) -> Optional[int]:
        url = f"{(self.config.target_url or DEFAULT_TARGET_URL).rstrip('/')}/api/admin/capacity"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                if value is None:
                    async with s.get(url) as r:
                        return (await r.json()).get("worker_slots")
                async with s.post(url, json={"worker_slots": value}) as r:
                    return (await r.json()).get("worker_slots")
        except Exception:  # noqa: BLE001 - optional feature; app may be an older version
            logger.warning("could not reach demo app capacity API at %s", url)
            return None

    # -- lifecycle -----------------------------------------------------------
    async def start(self, cfg: LoadConfig) -> None:
        if self.running:
            await self.stop()
        self.config = cfg
        self.traffic = self._parse_mix(cfg.traffic_mix)
        self.running = True
        self.started_at = time.monotonic()
        self.counts = self._zero_counts()
        self.samples.clear()
        self.in_flight_samples.clear()
        self.skipped_times.clear()
        self.baseline.clear()
        self.cur_users = 0
        self.cur_tps = 0.0
        self.state = "STARTING"
        self.snapshot = {}

        if cfg.app_worker_slots is not None:
            self.prev_app_slots = await self._app_slots()
            await self._app_slots(cfg.app_worker_slots)
        else:
            self.prev_app_slots = None

        limit = max(cfg.virtual_users, cfg.max_users) + 20
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=limit, ttl_dns_cache=300))
        POOL_SIZE.set(limit)
        for m in MODES:
            MODE.labels(mode=m).set(1 if m == cfg.mode else 0)
        for name in list_scenario_ids() + ["custom"]:
            SCENARIO_ACTIVE.labels(scenario_name=name).set(1 if name == cfg.scenario_name else 0)

        if cfg.mode == "fixed_users":
            self._set_users(1 if cfg.ramp_up_seconds else cfg.virtual_users)
        else:
            self.cur_tps = cfg.target_tps if not cfg.ramp_up_seconds else 0.5
            self.dispatch_task = self._spawn(self._dispatch_loop())
        self.control_task = asyncio.create_task(self._control_loop())
        logger.info("started %s (%s)", cfg.scenario_name, cfg.mode)

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.state = "STOPPING"
        snap = dict(self.snapshot)
        if self.control_task and self.control_task is not asyncio.current_task():
            self.control_task.cancel()
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.user_tasks.clear()
        if self.session and not self.session.closed:
            await self.session.close()
        if self.prev_app_slots is not None:
            await self._app_slots(self.prev_app_slots)
            self.prev_app_slots = None

        elapsed = time.monotonic() - self.started_at
        self.history.insert(0, {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scenario": self.config.scenario_name,
            "mode": self.config.mode,
            "duration_s": round(elapsed, 1),
            "requests": self.counts["sent"],
            "avg_tps": round(self.counts["sent"] / max(elapsed, 0.1), 1),
            "p95_ms": snap.get("p95_ms", 0),
            "intentional_errors": self.counts["intentional_errors"],
            "failures": self.counts["failures"],
            "skipped": self.counts["skipped"],
            "final_diagnosis": snap.get("diagnosis", {}).get("title", "—"),
        })
        del self.history[15:]
        self.state = "IDLE"
        self.snapshot = {}
        _reset_gauges()
        for name in list_scenario_ids() + ["custom"]:
            SCENARIO_ACTIVE.labels(scenario_name=name).set(0)
        logger.info("stopped after %d requests", self.counts["sent"])

    def update(self, upd: LoadUpdate) -> Dict[str, Any]:
        if not self.running or not self.config:
            raise HTTPException(status_code=400, detail="No load test is running")
        cfg = self.config
        if upd.think_time_ms is not None:
            cfg.think_time_ms = upd.think_time_ms
        if upd.script_cpu_ms is not None:
            cfg.script_cpu_ms = upd.script_cpu_ms
        if upd.max_users is not None:
            cfg.max_users = upd.max_users
        if upd.target_tps is not None:
            cfg.target_tps = upd.target_tps
            if cfg.mode == "fixed_rate":
                self.cur_tps = upd.target_tps
        if upd.virtual_users is not None:
            cfg.virtual_users = upd.virtual_users
            if cfg.mode == "fixed_users":
                self._set_users(upd.virtual_users)
        # ramp-up is over once the user takes manual control
        cfg.ramp_up_seconds = 0
        return {"config": cfg.model_dump()}

    def status(self) -> Dict[str, Any]:
        cfg = self.config
        return {
            "running": self.running,
            "state": self.state,
            "scenario": cfg.scenario_name if (cfg and self.running) else None,
            "mode": cfg.mode if cfg else None,
            "config": cfg.model_dump() if (cfg and self.running) else None,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 1) if self.running else 0,
            "totals": dict(self.counts),
            "live": self.snapshot if self.running else {},
            "generator": {
                "cpu_ratio": round(monitor.cpu_ratio, 3),
                "cpu_limit_cores": CPU_LIMIT,
                "loop_lag_ms": round(monitor.loop_lag * 1000, 1),
            },
            "target_url": (cfg.target_url if cfg and cfg.target_url else DEFAULT_TARGET_URL),
        }


def list_scenario_ids() -> List[str]:
    from scenarios import SCENARIOS
    return list(SCENARIOS)


engine = LoadEngine()
