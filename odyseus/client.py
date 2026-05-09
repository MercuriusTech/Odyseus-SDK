import asyncio
import contextlib
import inspect
import json
import sys
import time
from urllib.parse import urlparse, urlunparse

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription


DEFAULT_POINT_CLOUD_MODEL = "depth-anything"


class OdyseusError(Exception):
    """Base exception for SDK errors."""


class OdyseusWebRTCError(OdyseusError):
    """Raised when the WebRTC handshake fails."""

    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class OdyseusStreamCapacityError(OdyseusWebRTCError):
    """Raised when the server rejects a robot stream due to capacity limits."""

    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None):
        payload = payload or {}
        total_slots = payload.get("total_slots")
        used_slots = payload.get("used_slots")
        available_slots = payload.get("available_slots")

        details = ["Robot streaming is currently at capacity."]
        if total_slots is not None and used_slots is not None:
            details.append(f"Active robot streams: {used_slots}/{total_slots}.")
        elif available_slots is not None:
            details.append(f"Available robot stream slots: {available_slots}.")

        details.append("This is not a bug in your client.")
        details.append("Please wait a few seconds and try again.")

        if message and message not in details:
            details.insert(1, str(message).rstrip(".") + ".")

        banner = "=" * 72
        formatted = (
            f"{banner}\n"
            f"ODYSEUS STREAMING UNAVAILABLE\n"
            f"{banner}\n"
            f"{chr(10).join(details)}\n"
            f"{banner}"
        )

        super().__init__(formatted, status=status, payload=payload)


class OdyseusSessionLimitError(OdyseusError):
    """Raised when a robot stream is blocked by a session time limit or cooldown."""

    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None):
        payload = payload or {}
        error_code = payload.get("error_code")
        retry_after = payload.get("retry_after_seconds")
        limit_seconds = payload.get("session_limit_seconds")
        last_reason = payload.get("last_forced_reason")

        details = []
        if error_code == "session_cooldown_active":
            details.append("Robot streaming is cooling down after your last session.")
            if retry_after is not None:
                details.append(f"Time remaining before reconnect: {retry_after} seconds.")
        else:
            details.append("Robot streaming was interrupted by the session time limit.")
            if limit_seconds is not None:
                details.append(f"Per-session robot stream limit: {limit_seconds} seconds.")
            if retry_after is not None and retry_after > 0:
                details.append(f"Cooldown remaining before reconnect: {retry_after} seconds.")

        if last_reason == "session_limit" and error_code != "session_cooldown_active":
            details.append("Your previous robot stream hit the maximum allowed runtime.")

        details.append("This is not a bug in your client.")

        if message and message not in details:
            details.insert(1, str(message).rstrip(".") + ".")

        banner = "=" * 72
        formatted = (
            f"{banner}\n"
            f"ODYSEUS SESSION LIMIT REACHED\n"
            f"{banner}\n"
            f"{chr(10).join(details)}\n"
            f"{banner}"
        )

        super().__init__(formatted)
        self.status = status
        self.payload = payload


class OdyseusGPUStartupError(OdyseusWebRTCError):
    """Raised when cloud-pub cannot start the assigned GPU instance."""

    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None):
        payload = payload or {}
        detail = payload.get("detail")

        details = ["The assigned GPU failed to start on the server side."]
        if message:
            details.append(str(message).rstrip(".") + ".")
        if detail:
            details.append(f"Server detail: {str(detail).strip()}")
        details.append("This is usually an infrastructure issue (Lambda/EC2 start path), not a client bug.")
        details.append("Please retry after a short wait and check server logs if it persists.")

        banner = "=" * 72
        formatted = (
            f"{banner}\n"
            f"ODYSEUS GPU STARTUP FAILED\n"
            f"{banner}\n"
            f"{chr(10).join(details)}\n"
            f"{banner}"
        )

        super().__init__(formatted, status=status, payload=payload)


def _build_limit_exception(status: int, payload: dict) -> OdyseusError:
    error_code = payload.get("error_code")
    if error_code in {"session_limit_exceeded", "session_cooldown_active"}:
        message = payload.get("message") or payload.get("error") or "Robot stream session unavailable."
        retry_after = payload.get("retry_after_seconds")
        if error_code == "session_cooldown_active" and retry_after is not None:
            message = f"{message} Retry in {retry_after}s."
        return OdyseusSessionLimitError(message, status=status, payload=payload)

    if status == 429:
        return OdyseusStreamCapacityError(
            payload.get("error", "Robot streaming capacity reached."),
            status=status,
            payload=payload,
        )

    if status == 503:
        error_text = str(payload.get("error", "")).lower()
        if "gpu failed to start" in error_text or payload.get("detail"):
            return OdyseusGPUStartupError(
                payload.get("error", "GPU failed to start."),
                status=status,
                payload=payload,
            )

    return OdyseusWebRTCError(
        payload.get("error", f"Request failed ({status})."),
        status=status,
        payload=payload,
    )


async def _fetch_stream_slots(gpu_base_url: str, api_key: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{gpu_base_url.rstrip('/')}/stream-slots",
                headers={"X-API-Key": api_key},
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        return None


def _forced_state_is_new(status_payload: dict, previous_last_forced_at: str | None) -> bool:
    last_forced_at = status_payload.get("last_forced_at")
    if not last_forced_at:
        return False
    if not previous_last_forced_at:
        return True
    return last_forced_at != previous_last_forced_at


def _http_to_ws(url: str) -> str:
    parsed = urlparse(url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme))


async def _maybe_await(value) -> None:
    if inspect.isawaitable(value):
        await value


class Odyseus:
    """Async client for the Odyseus API."""

    def __init__(self, api_key: str, base_url: str = "https://odyseus.xyz"):
        if not api_key:
            raise ValueError("An API key is required to initialize the Odyseus client.")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": self.api_key}
        self._gpu_base_url: str | None = None
        self._live_session_id: str | None = None
        self._live_session_info: dict = {}
        self._last_single_infer_session_id: str | None = None
        self._session_limit_notice_emitted: set[int] = set()
        self._session_request_timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
        self._progress_estimate_seconds = 120.0

    def _remember_live_session(self, payload: dict | None) -> None:
        if not payload:
            return
        gpu_base_url = payload.get("gpu_base_url") or self._gpu_base_url
        if gpu_base_url:
            self._gpu_base_url = str(gpu_base_url).rstrip("/")
        session_id = payload.get("session_id")
        if session_id:
            self._live_session_id = str(session_id)
        merged = dict(self._live_session_info)
        merged.update(payload)
        if self._gpu_base_url:
            merged["gpu_base_url"] = self._gpu_base_url
        if self._live_session_id:
            merged["session_id"] = self._live_session_id
        self._live_session_info = merged

    def _render_progress_line(self, elapsed_seconds: float, *, done: bool = False) -> None:
        percent = 100 if done else min(95, int((elapsed_seconds / self._progress_estimate_seconds) * 100))
        filled = int(percent / 5)
        bar = "#" * filled + "-" * (20 - filled)
        status = "GPU ready" if done else "Preparing GPU"
        line = f"\r{status} [{bar}] {percent:>3}%  {elapsed_seconds:>5.1f}s elapsed"
        end = "\n" if done else ""
        print(line, end=end, flush=True)

    async def _run_with_progress(self, coro, start_message: str, wait_message: str):
        started_at = time.monotonic()
        if sys.stdout.isatty():
            self._render_progress_line(0.0)
        else:
            print(start_message, flush=True)

        task = asyncio.create_task(coro)

        async def _reporter() -> None:
            while not task.done():
                await asyncio.sleep(1.0)
                if task.done():
                    return
                elapsed = int(time.monotonic() - started_at)
                if sys.stdout.isatty():
                    self._render_progress_line(float(elapsed))
                else:
                    print(f"{wait_message} ({elapsed}s elapsed)", flush=True)

        reporter = asyncio.create_task(_reporter())
        try:
            result = await task
        except Exception:
            if sys.stdout.isatty():
                print("", flush=True)
            raise
        finally:
            reporter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reporter

        elapsed = round(time.monotonic() - started_at, 1)
        if sys.stdout.isatty():
            self._render_progress_line(elapsed, done=True)
        else:
            print(f"GPU session ready after {elapsed}s.", flush=True)
        return result

    def _runtime_http_url(self, path: str) -> str:
        if not self._gpu_base_url:
            raise OdyseusError("GPU runtime URL is unknown. Connect a video session or resolve a GPU first.")
        return f"{self._gpu_base_url.rstrip('/')}{path}"

    def _runtime_ws_url(self, path: str) -> str:
        return f"{_http_to_ws(self._runtime_http_url(''))}{path}"

    async def _emit_event(self, on_event, payload: dict) -> None:
        if on_event is None:
            return
        await _maybe_await(on_event(payload))

    async def _request_json(self, method: str, url: str, **kwargs) -> dict:
        async with aiohttp.ClientSession(timeout=self._session_request_timeout) as session:
            async with session.request(method, url, headers=self.headers, **kwargs) as resp:
                try:
                    payload = await resp.json(content_type=None)
                except Exception:
                    payload = {"error": await resp.text()}
                if resp.status == 429:
                    raise _build_limit_exception(resp.status, payload)
                if resp.status >= 400:
                    raise OdyseusError(payload.get("error") or payload.get("message") or f"Request failed ({resp.status})")
                return payload

    async def resolve_webrtc_session(self) -> dict:
        async def _request_session() -> dict:
            async with aiohttp.ClientSession(timeout=self._session_request_timeout) as session:
                async with session.post(f"{self.base_url}/api/webrtc/sim-session", headers=self.headers) as resp:
                    if resp.status == 404:
                        return {}
                    if resp.status != 200:
                        try:
                            payload = await resp.json()
                        except Exception:
                            payload = {"error": await resp.text()}
                        raise _build_limit_exception(resp.status, payload)
                    payload = await resp.json()
                    self._remember_live_session(payload)
                    return payload

        return await self._run_with_progress(
            _request_session(),
            "Requesting GPU session from cloud-pub. The server may start a GPU if needed.",
            "Still waiting for the assigned GPU to finish starting",
        )

    async def resolve_gpu_base_url(self) -> str:
        if self._gpu_base_url:
            return self._gpu_base_url

        session_info = await self.resolve_webrtc_session()
        gpu_base_url = session_info.get("gpu_base_url")
        if gpu_base_url:
            self._gpu_base_url = str(gpu_base_url).rstrip("/")
            return self._gpu_base_url

        return self.base_url

    async def infer(self, image_bytes: bytes) -> dict:
        """Legacy infer API retained for compatibility with older clients."""
        target_base_url = await self.resolve_gpu_base_url()
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("file", image_bytes, filename="frame.jpg", content_type="image/jpeg")

            async with session.post(f"{target_base_url}/infer", data=data, headers=self.headers) as resp:
                if resp.status == 400:
                    error_detail = await resp.text()
                    raise Exception(f"Bad Request (400): {error_detail}")
                if resp.status == 429:
                    try:
                        payload = await resp.json()
                    except Exception:
                        payload = {"error": await resp.text()}
                    raise _build_limit_exception(resp.status, payload)
                if resp.status != 200:
                    raise Exception(f"Server Error ({resp.status})")

                return await resp.json()

    async def connect_webrtc(self, pc: RTCPeerConnection, unreal_fix: bool = False) -> bool:
        """Handles the WebRTC SDP handshake. Set unreal_fix=True for Unreal Engine streams."""
        offer = await pc.createOffer()
        sdp = offer.sdp

        if unreal_fix:
            from .unreal import strip_rtx_from_sdp

            sdp = strip_rtx_from_sdp(sdp)

        await pc.setLocalDescription(RTCSessionDescription(sdp=sdp, type=offer.type))

        async with aiohttp.ClientSession() as session:
            previous_status = None
            if self._gpu_base_url:
                previous_status = await _fetch_stream_slots(self._gpu_base_url, self.api_key)
            previous_last_forced_at = previous_status.get("last_forced_at") if previous_status else None

            session_info = await self.resolve_webrtc_session()
            if session_info:
                offer_url = session_info["offer_url"]
                self._remember_live_session(session_info)
                payload = {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type,
                    "token": session_info["token"],
                }
                headers = {}
            else:
                offer_url = f"{self.base_url}/offer-sim"
                payload = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
                headers = self.headers

            async with session.post(offer_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    try:
                        error_payload = await resp.json()
                    except Exception:
                        error_payload = {"raw": text}
                    raise _build_limit_exception(resp.status, error_payload)

                answer = await resp.json()
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
                )

                pc_id = id(pc)

                @pc.on("connectionstatechange")
                async def _emit_limit_banner_on_forced_close() -> None:
                    if pc.connectionState not in {"closed", "failed"}:
                        return
                    if pc_id in self._session_limit_notice_emitted:
                        return
                    if not self._gpu_base_url:
                        return

                    for _ in range(6):
                        status_payload = await _fetch_stream_slots(self._gpu_base_url, self.api_key)
                        if status_payload:
                            error_code = status_payload.get("error_code")
                            last_forced_reason = status_payload.get("last_forced_reason")
                            if (
                                error_code == "session_cooldown_active"
                                or (
                                    last_forced_reason == "session_limit"
                                    and _forced_state_is_new(status_payload, previous_last_forced_at)
                                )
                            ):
                                self._session_limit_notice_emitted.add(pc_id)
                                print(
                                    OdyseusSessionLimitError(
                                        status_payload.get("message") or status_payload.get("error") or "Robot stream session ended.",
                                        status=429,
                                        payload=status_payload,
                                    ),
                                    flush=True,
                                )
                                return
                        await asyncio.sleep(1.0)

                return True

    async def connect_video_session(self, pc: RTCPeerConnection, *, unreal_fix: bool = False) -> dict:
        """Connects a live WebRTC session and returns the resolved runtime session metadata."""
        await self.connect_webrtc(pc, unreal_fix=unreal_fix)
        if not self._live_session_info:
            raise OdyseusError("WebRTC connected but no live session metadata was returned.")
        return dict(self._live_session_info)

    async def _create_single_infer_session(
        self,
        *,
        max_dist: float | None = None,
        point_cloud_model: str = DEFAULT_POINT_CLOUD_MODEL,
    ) -> dict:
        await self.resolve_gpu_base_url()
        payload = {"point_cloud_model": point_cloud_model}
        if max_dist is not None:
            payload["max_dist"] = float(max_dist)
        session_info = await self._request_json(
            "POST",
            self._runtime_http_url("/api/single-infer/session"),
            json=payload,
        )
        self._last_single_infer_session_id = str(session_info.get("session_id") or "")
        return session_info

    async def single_image_infer(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        max_dist: float | None = None,
        point_cloud_model: str = DEFAULT_POINT_CLOUD_MODEL,
        on_event=None,
        timeout_s: float = 180.0,
    ) -> dict:
        """Runs the single-image pipeline and emits raw backend events as they arrive."""
        if not prompt.strip():
            raise ValueError("prompt is required for single_image_infer")
        if not image_bytes:
            raise ValueError("image_bytes must not be empty")

        session_info = await self._create_single_infer_session(
            max_dist=max_dist,
            point_cloud_model=point_cloud_model,
        )
        session_id = str(session_info.get("session_id") or "").strip()
        if not session_id:
            raise OdyseusError("Single-image session creation did not return a session_id.")

        aggregate = {
            "session_id": session_id,
            "session": session_info,
            "mesh_ready_payload": None,
            "objective_payload": None,
            "latest_slam_status": None,
            "latest_slam_binary": None,
            "events": [],
        }
        completion = asyncio.get_running_loop().create_future()
        task_errors: list[BaseException] = []

        async def _consume_single(ws: aiohttp.ClientWebSocketResponse) -> None:
            try:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data or "{}")
                    aggregate["events"].append(payload)
                    await self._emit_event(on_event, payload)
                    msg_type = payload.get("type")
                    if msg_type == "single_infer_mesh_ready":
                        aggregate["mesh_ready_payload"] = payload.get("payload")
                    elif msg_type == "single_infer_objective":
                        aggregate["objective_payload"] = payload.get("payload")
                        if not completion.done():
                            completion.set_result(payload.get("payload"))
                    elif msg_type == "error":
                        if not completion.done():
                            completion.set_exception(OdyseusError(payload.get("error") or "single-image inference failed"))
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                task_errors.append(exc)
                if not completion.done():
                    completion.set_exception(exc)

        async def _consume_slam(ws: aiohttp.ClientWebSocketResponse) -> None:
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        payload = {
                            "type": "slam_binary",
                            "session_id": session_id,
                            "payload": bytes(msg.data),
                        }
                        aggregate["latest_slam_binary"] = payload["payload"]
                        aggregate["events"].append(payload)
                        await self._emit_event(on_event, payload)
                        continue
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data or "{}")
                    if payload.get("type") == "slam_status":
                        aggregate["latest_slam_status"] = payload
                    aggregate["events"].append(payload)
                    await self._emit_event(on_event, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                task_errors.append(exc)

        single_ws_url = self._runtime_ws_url(f"/api/single-infer/ws/{session_id}")
        slam_ws_url = self._runtime_ws_url(f"/api/slam/ws/{session_id}")

        async with aiohttp.ClientSession(headers=self.headers, timeout=self._session_request_timeout) as session:
            single_ws = await session.ws_connect(single_ws_url, heartbeat=20, max_msg_size=32 * 1024 * 1024)
            slam_ws = await session.ws_connect(slam_ws_url, heartbeat=20, max_msg_size=64 * 1024 * 1024)
            tasks = [
                asyncio.create_task(_consume_single(single_ws)),
                asyncio.create_task(_consume_slam(slam_ws)),
            ]
            try:
                start_payload = {
                    "type": "start",
                    "prompt": prompt,
                    "point_cloud_model": point_cloud_model,
                }
                if max_dist is not None:
                    start_payload["max_dist"] = float(max_dist)
                await single_ws.send_json(start_payload)
                await single_ws.send_bytes(image_bytes)
                await asyncio.wait_for(completion, timeout=timeout_s)
            finally:
                for ws in (single_ws, slam_ws):
                    with contextlib.suppress(Exception):
                        await ws.close()
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

        if task_errors and aggregate["objective_payload"] is None:
            raise OdyseusError(str(task_errors[0]))
        return aggregate

    async def get_live_session_state(self, session_id: str | None = None) -> dict:
        """Fetches the latest live session state from the assigned GPU runtime."""
        await self.resolve_gpu_base_url()
        target_session_id = (session_id or self._live_session_id or "").strip()
        if target_session_id:
            return await self._request_json(
                "GET",
                self._runtime_http_url(f"/api/stream/session/{target_session_id}"),
            )

        payload = await self._request_json("GET", self._runtime_http_url("/api/stream/session"))
        active_session_id = payload.get("active_session_id") or ""
        if active_session_id:
            self._live_session_id = str(active_session_id)
        return payload

    async def get_session_frame(self, session_id: str) -> bytes:
        """Fetches the latest rendered session frame from the GPU runtime."""
        await self.resolve_gpu_base_url()
        async with aiohttp.ClientSession(timeout=self._session_request_timeout) as session:
            async with session.get(
                self._runtime_http_url(f"/api/slam/frame/{session_id}"),
                headers=self.headers,
            ) as resp:
                body = await resp.read()
                if resp.status >= 400:
                    raise OdyseusError(body.decode("utf-8", errors="replace") or f"Failed to fetch session frame ({resp.status})")
                return body

    async def iter_slam_events(self, session_id: str | None = None):
        """Yields JSON and binary SLAM events for a live or single-image session."""
        await self.resolve_gpu_base_url()
        target_session_id = (session_id or self._live_session_id or self._last_single_infer_session_id or "").strip()
        if not target_session_id:
            raise OdyseusError("session_id is required before opening the SLAM event stream.")

        async with aiohttp.ClientSession(headers=self.headers, timeout=self._session_request_timeout) as session:
            async with session.ws_connect(
                self._runtime_ws_url(f"/api/slam/ws/{target_session_id}"),
                heartbeat=20,
                max_msg_size=64 * 1024 * 1024,
            ) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        yield {
                            "type": "slam_binary",
                            "session_id": target_session_id,
                            "payload": bytes(msg.data),
                        }
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        yield json.loads(msg.data or "{}")
