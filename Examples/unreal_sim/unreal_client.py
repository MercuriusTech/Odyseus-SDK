import argparse
import asyncio
import json
import logging
import time
import aiohttp
import av
import io
import sys
import websockets
import signal
from PIL import Image
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer
)
from aiortc.sdp import candidate_from_sdp

# --- IMPORT MERCURIUSTECH SDK ---
import MercuriusTech as mt

# ============================================================
# ARGUMENT PARSING
# ============================================================
parser = argparse.ArgumentParser(description="Odyseus Unreal Sim Client")
parser.add_argument("--api-key", type=str, required=True, help="Odyseus API Key")
# Added optional --url in case you need to bypass the default https://odyseus.xyz
parser.add_argument("--url", type=str, default=None, help="Optional custom Base URL")
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("sim_client")
av.logging.set_level(av.logging.PANIC)

# ============================================================
# CONFIGURATION
# ============================================================
UNREAL_WS_URL = "ws://127.0.0.1:80"
STUN_SERVERS = ["stun:stun.l.google.com:19302"]

# Timing Control
MOVE_DURATION = 1.5   
TURN_DURATION = 1.5

# Initialize the SDK Client
client_kwargs = {"api_key": args.api_key}
if args.url:
    client_kwargs["base_url"] = args.url
client = mt.Odyseus(**client_kwargs)

# ============================================================
# SIMULATION BRIDGE
# ============================================================
class OdyseusSimBridge:
    def __init__(self):
        self.unreal_dc = None
        self.unreal_pc = None
        self.relay_pc = None
        self.brain_task = None
        self.ws = None

    async def pi_brain_loop(self, camera_track: mt.webrtc.LatestFrameTrack):
        """Processes frames and sends navigation commands back to Unreal."""
        logger.info("Brain active. Running inference loop...")
        while True:
            try:
                # 1. Capture the freshest frame via SDK wrapper
                frame = await camera_track.recv()
                img = frame.to_image()
                
                # 2. Resize to VLM standard if necessary
                if img.size != (640, 480):
                    img = img.resize((640, 480), Image.Resampling.LANCZOS)
                
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                
                # 3. Call the Odyseus SDK Inference API
                t0 = time.monotonic()
                result = await client.infer(buf.getvalue())
                cmd = result.get("command", "HOLD").upper()
                logger.info(f">>> VLM DECISION: {cmd} ({time.monotonic()-t0:.2f}s)")

                # 4. Execute command on Unreal via DataChannel
                if self.unreal_dc and self.unreal_dc.readyState == "open":
                    if cmd not in ("HOLD", "STOP"):
                        # SDK Helper: Formats dict for Unreal's byte-protocol
                        unreal_cmd = "BACK" if cmd == "BACKWARD" else cmd
                        if "SEARCH_LEFT" in cmd: unreal_cmd = "LEFT"
                        if "SEARCH_RIGHT" in cmd: unreal_cmd = "RIGHT"

                        payload = mt.unreal.format_ui_interaction({"command": unreal_cmd})
                        self.unreal_dc.send(payload)
                        
                        is_turn = any(x in unreal_cmd for x in ["LEFT", "RIGHT"])
                        await asyncio.sleep(TURN_DURATION if is_turn else MOVE_DURATION)
                        
                        self.unreal_dc.send(mt.unreal.format_ui_interaction({"command": "STOP"}))
                    else:
                        self.unreal_dc.send(mt.unreal.format_ui_interaction({"command": "STOP"}))
                        await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Brain Loop Error: {e}")
                await asyncio.sleep(1)

    async def setup_relay(self, track: mt.webrtc.LatestFrameTrack):
        """Relays the Unreal video track to the Cloud Dashboard via the SDK."""
        logger.info("Connecting to Odyseus WebRTC Relay...")
        self.relay_pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=STUN_SERVERS)]))
        self.relay_pc.addTrack(track)
        
        # SDK Helper: Automates the entire SDP handshake with the EC2 server
        if await client.connect_webrtc(self.relay_pc):
            logger.info("Dashboard relay established.")
        else:
            logger.error("Failed to establish Dashboard relay.")

    async def stop(self):
        if self.brain_task: self.brain_task.cancel()
        if self.relay_pc: await self.relay_pc.close()
        if self.unreal_pc: await self.unreal_pc.close()
        if self.ws: await self.ws.close()
        logger.info("Shutdown complete.")

    async def run(self):
        self.unreal_pc = RTCPeerConnection()

        @self.unreal_pc.on("datachannel")
        def _on_dc(channel):
            self.unreal_dc = channel
            logger.info(f"Unreal Data Channel '{channel.label}' ready.")

        @self.unreal_pc.on("track")
        def _on_track(track):
            if track.kind == "video":
                logger.info("Received video track from Unreal.")
                # SDK Helper: Wrap the track to ensure no latency buildup
                latest = mt.webrtc.LatestFrameTrack(track)
                asyncio.create_task(self.setup_relay(latest))
                self.brain_task = asyncio.create_task(self.pi_brain_loop(latest))

        # Standard Unreal PixelStreaming Signaling Handshake
        async with websockets.connect(UNREAL_WS_URL) as self.ws:
            await self.ws.send(json.dumps({"type": "request_stream"}))
            async for raw in self.ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "offer":
                    # SDK Helper: Forces Unreal into H.264 Constrained Baseline
                    fixed_sdp = mt.unreal.strip_rtx_from_sdp(msg["sdp"])
                    await self.unreal_pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_sdp, type="offer"))
                    ans = await self.unreal_pc.createAnswer()
                    await self.unreal_pc.setLocalDescription(ans)
                    await self.ws.send(json.dumps({"type": "answer", "sdp": self.unreal_pc.localDescription.sdp}))
                
                elif msg_type == "iceCandidate":
                    c = msg["candidate"]
                    if c and "candidate" in c:
                        cand_str = c["candidate"].split(":", 1)[1] if c["candidate"].startswith("candidate:") else c["candidate"]
                        rtc_cand = candidate_from_sdp(cand_str)
                        rtc_cand.sdpMid, rtc_cand.sdpMLineIndex = c["sdpMid"], c["sdpMLineIndex"]
                        await self.unreal_pc.addIceCandidate(rtc_cand)

# ============================================================
# RUNNER
# ============================================================
if __name__ == "__main__":
    # Required for WebRTC/Websockets on Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    bridge = OdyseusSimBridge()
    loop = asyncio.get_event_loop()

    # Signal handlers for clean shutdown (Linux/Mac)
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bridge.stop()))

    try:
        logger.info("--- ODYSEUS UNREAL CLIENT STARTED ---")
        loop.run_until_complete(bridge.run())
    except KeyboardInterrupt:
        logger.info("Stop requested.")
    finally:
        loop.run_until_complete(bridge.stop())
        loop.close()