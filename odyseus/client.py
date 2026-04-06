import asyncio
import aiohttp
import contextlib
import sys
import time
from aiortc import RTCPeerConnection, RTCSessionDescription


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


class Odyseus:
    """Async client for the Odyseus Odyseus API."""
    
    def __init__(self, api_key: str, base_url: str = "https://odyseus.xyz"):
        if not api_key:
            raise ValueError("An API key is required to initialize the Odyseus client.")
            
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": self.api_key}
        self._gpu_base_url = None
        self._session_limit_notice_emitted: set[int] = set()
        # Session resolution can legitimately block for several minutes while cloud-pub
        # wakes a stopped GPU and waits for the runtime to become ready.
        self._session_request_timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
        self._progress_estimate_seconds = 120.0

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
                    self._gpu_base_url = payload.get("gpu_base_url") or self._gpu_base_url
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
            self._gpu_base_url = gpu_base_url.rstrip("/")
            return self._gpu_base_url

        return self.base_url

    async def infer(self, image_bytes: bytes) -> dict:
        """Sends an image to the VLM brain and returns the navigation command."""
        target_base_url = await self.resolve_gpu_base_url()
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', image_bytes, filename='frame.jpg', content_type='image/jpeg')
            
            async with session.post(f"{target_base_url}/infer", data=data, headers=self.headers) as resp:
                if resp.status == 400:
                    error_detail = await resp.text()
                    raise Exception(f"Bad Request (400): {error_detail}")
                elif resp.status == 429:
                    try:
                        payload = await resp.json()
                    except Exception:
                        payload = {"error": await resp.text()}
                    raise _build_limit_exception(resp.status, payload)
                elif resp.status != 200:
                    raise Exception(f"Server Error ({resp.status})")
                
                return await resp.json()

    async def connect_webrtc(self, pc: RTCPeerConnection, unreal_fix: bool = False) -> bool:
        """Handles the WebRTC SDP handshake. Set unreal_fix=True for Unreal Engine streams."""
        offer = await pc.createOffer()
        sdp = offer.sdp

        # Apply the H.264 profile fix if requested
        if unreal_fix:
            from .unreal import strip_rtx_from_sdp
            sdp = strip_rtx_from_sdp(sdp)
    
        await pc.setLocalDescription(RTCSessionDescription(sdp=sdp, type=offer.type))
    
        async with aiohttp.ClientSession() as session:
            previous_status = None
            if self._gpu_base_url:
                previous_status = await _fetch_stream_slots(self._gpu_base_url, self.api_key)
            previous_last_forced_at = previous_status.get("last_forced_at") if previous_status else None

            # Note: We use pc.localDescription.sdp here to ensure we send the "cleaned" version
            session_info = await self.resolve_webrtc_session()
            if session_info:
                offer_url = session_info["offer_url"]
                self._gpu_base_url = session_info.get("gpu_base_url") or self._gpu_base_url
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
