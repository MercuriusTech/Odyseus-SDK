import asyncio
import contextlib
import json

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

import odyseus as od


class UnrealVideoBridge:
    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self.ws = None
        self.pc = RTCPeerConnection()
        self._reader_task = None
        self._track_ready = asyncio.get_running_loop().create_future()

        @self.pc.on("datachannel")
        def _on_datachannel(_channel):
            return None

        @self.pc.on("track")
        def _on_track(track):
            if track.kind == "video" and not self._track_ready.done():
                self._track_ready.set_result(od.webrtc.LatestFrameTrack(track))

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, open_timeout=10)
        self._reader_task = asyncio.create_task(self._reader())
        await self.ws.send(json.dumps({"type": "request_stream"}))
        return await asyncio.wait_for(self._track_ready, timeout=30)

    async def _reader(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            msg_type = msg.get("type")
            if msg_type == "offer":
                fixed_sdp = od.unreal.strip_rtx_from_sdp(msg["sdp"])
                await self.pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_sdp, type="offer"))
                answer = await self.pc.createAnswer()
                await self.pc.setLocalDescription(answer)
                await self.ws.send(json.dumps({"type": "answer", "sdp": self.pc.localDescription.sdp}))
            elif msg_type == "iceCandidate":
                candidate = msg.get("candidate")
                if candidate and "candidate" in candidate:
                    cand_str = candidate["candidate"]
                    if cand_str.startswith("candidate:"):
                        cand_str = cand_str.split(":", 1)[1]
                    rtc_cand = candidate_from_sdp(cand_str)
                    rtc_cand.sdpMid = candidate["sdpMid"]
                    rtc_cand.sdpMLineIndex = candidate["sdpMLineIndex"]
                    await self.pc.addIceCandidate(rtc_cand)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        if self.ws is not None:
            await self.ws.close()
        await self.pc.close()
