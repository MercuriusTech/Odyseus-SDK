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

# --- IMPORT Odyseus SDK ---
try:
    import odyseus as od
except ImportError:
    print("CRITICAL: Odyseus SDK not found. Did you run 'pip install -e .'?")
    sys.exit(1)

# ============================================================
# ARGUMENT PARSING
# ============================================================
parser = argparse.ArgumentParser(description="Odyseus Unreal Sim Client")
parser.add_argument("--api-key", type=str, required=True, help="Odyseus API Key")
parser.add_argument("--url", type=str, default=None, help="Optional custom Base URL")
args = parser.parse_args()

# Setup logging to be more verbose
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("sim_client")
av.logging.set_level(av.logging.PANIC)

# ============================================================
# CONFIGURATION
# ============================================================
UNREAL_WS_URL = "ws://127.0.0.1:80" 
STUN_SERVERS = ["stun:stun.l.google.com:19302"]

MOVE_DURATION = 1.5   
TURN_DURATION = 1.5

# Initialize the SDK Client
client_kwargs = {"api_key": args.api_key}
if args.url:
    client_kwargs["base_url"] = args.url
client = od.Odyseus(**client_kwargs)

# ============================================================
# SIMULATION BRIDGE
# ============================================================
class OdyseusSimBridge:
    def __init__(self):
        self.unreal_dc = None
        self.unreal_pc = None
        self.relay_pc = None
        self.ws = None
        self.tasks = set()  # Track background tasks to prevent hangs

    def _create_task(self, coro):
        """Helper to track tasks so they can be cancelled during shutdown."""
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def pi_brain_loop(self, camera_track: od.webrtc.LatestFrameTrack):
        """Processes frames and sends navigation commands back to Unreal."""
        logger.info("Brain active. Running inference loop...")
        while True:
            try:
                frame = await camera_track.recv()
                img = frame.to_image()
                
                if img.size != (640, 480):
                    img = img.resize((640, 480), Image.Resampling.LANCZOS)
                
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                
                t0 = time.monotonic()
                result = await client.infer(buf.getvalue())
                cmd = result.get("command", "HOLD").upper()
                logger.info(f">>> VLM DECISION: {cmd} ({time.monotonic()-t0:.2f}s)")

                if self.unreal_dc and self.unreal_dc.readyState == "open":
                    if cmd not in ("HOLD", "STOP"):
                        unreal_cmd = "BACK" if cmd == "BACKWARD" else cmd
                        if "SEARCH_LEFT" in cmd: unreal_cmd = "LEFT"
                        if "SEARCH_RIGHT" in cmd: unreal_cmd = "RIGHT"

                        payload = od.unreal.format_ui_interaction({"command": unreal_cmd})
                        self.unreal_dc.send(payload)
                        
                        is_turn = any(x in unreal_cmd for x in ["LEFT", "RIGHT"])
                        await asyncio.sleep(TURN_DURATION if is_turn else MOVE_DURATION)
                        
                        self.unreal_dc.send(od.unreal.format_ui_interaction({"command": "STOP"}))
                    else:
                        self.unreal_dc.send(od.unreal.format_ui_interaction({"command": "STOP"}))
                        await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Brain Loop Error: {e}")
                await asyncio.sleep(1)

    async def setup_relay(self, track: od.webrtc.LatestFrameTrack):
        """Relays the Unreal video track to the Cloud Dashboard via the SDK."""
        logger.info("Connecting to Odyseus WebRTC Relay...")
        self.relay_pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=STUN_SERVERS)]))
        self.relay_pc.addTrack(track)
        
        if await client.connect_webrtc(self.relay_pc, unreal_fix=True):
            logger.info("Dashboard relay established.")
        else:
            logger.error("Failed to establish Dashboard relay. Check your API Key.")

    async def stop(self):
        """Clean shutdown of all connections and tracked tasks."""
        logger.info("Shutting down bridge...")
        
        # 1. Cancel tracked tasks (Brain Loop and Relay setup)
        for task in list(self.tasks):
            task.cancel()
        
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        # 2. Close WebRTC and Signaling connections
        if self.relay_pc: await self.relay_pc.close()
        if self.unreal_pc: await self.unreal_pc.close()
        if self.ws: await self.ws.close()
        logger.info("Shutdown complete.")

    async def run(self):
        logger.info("Initializing Unreal PeerConnection...")
        self.unreal_pc = RTCPeerConnection()

        @self.unreal_pc.on("datachannel")
        def _on_dc(channel):
            self.unreal_dc = channel
            logger.info(f"Unreal Data Channel '{channel.label}' ready.")

        @self.unreal_pc.on("track")
        def _on_track(track):
            if track.kind == "video":
                logger.info("Received video track from Unreal.")
                latest = od.webrtc.LatestFrameTrack(track)
                # Use _create_task to ensure these are tracked for shutdown
                self._create_task(self.setup_relay(latest))
                self._create_task(self.pi_brain_loop(latest))

        logger.info(f"Connecting to Unreal signaling server at {UNREAL_WS_URL}...")
        try:
            async with websockets.connect(UNREAL_WS_URL, open_timeout=10) as self.ws:
                logger.info("Signaling connection established.")
                await self.ws.send(json.dumps({"type": "request_stream"}))
                
                async for raw in self.ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type")

                    if msg_type == "offer":
                        logger.info("Received WebRTC Offer from Unreal.")
                        fixed_sdp = od.unreal.strip_rtx_from_sdp(msg["sdp"])
                        await self.unreal_pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_sdp, type="offer"))
                        ans = await self.unreal_pc.createAnswer()
                        await self.unreal_pc.setLocalDescription(ans)
                        await self.ws.send(json.dumps({"type": "answer", "sdp": self.unreal_pc.localDescription.sdp}))
                        logger.info("Sent WebRTC Answer to Unreal.")
                    
                    elif msg_type == "iceCandidate":
                        c = msg["candidate"]
                        if c and "candidate" in c:
                            cand_str = c["candidate"].split(":", 1)[1] if c["candidate"].startswith("candidate:") else c["candidate"]
                            rtc_cand = candidate_from_sdp(cand_str)
                            rtc_cand.sdpMid, rtc_cand.sdpMLineIndex = c["sdpMid"], c["sdpMLineIndex"]
                            await self.unreal_pc.addIceCandidate(rtc_cand)

        except (websockets.exceptions.ConnectionClosedError, ConnectionRefusedError) as e:
            logger.error(f"Connection to Unreal failed: {e}.")
        except asyncio.TimeoutError:
            logger.error(f"Signaling connection timed out.")

# ============================================================
# RUNNER
# ============================================================
if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    bridge = OdyseusSimBridge()
    loop = asyncio.get_event_loop()

    try:
        logger.info("--- ODYSEUS UNREAL CLIENT STARTED ---")
        loop.run_until_complete(bridge.run())
    except KeyboardInterrupt:
        logger.info("Stop requested by user.")
    except Exception as e:
        logger.exception(f"FATAL SCRIPT ERROR: {e}")
    finally:
        # Run the explicit bridge stop logic
        loop.run_until_complete(bridge.stop())
        
        # Force-cancel any remaining loop tasks (e.g., SDK internal workers)
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        
        loop.close()
        logger.info("Terminal control restored.")