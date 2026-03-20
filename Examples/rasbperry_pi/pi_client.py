import argparse
import asyncio
import io
import logging
import time
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer

import motor_controller

# --- IMPORT MERCURIUSTECH SDK ---
import MercuriusTech as mt

parser = argparse.ArgumentParser(description="Odyseus Pi Client")
parser.add_argument("--api-key", type=str, required=True, help="MercuriusTech API Key")
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("pi_client")

client = mt.Odyseus(api_key=args.api_key)

async def inference_loop(camera_track: mt.webrtc.LatestFrameTrack):
    logger.info("Starting inference loop...")
    while True:
        try:
            motor_controller.stop_motors()
            await asyncio.sleep(0.3) # Settle time

            # Fetch freshest frame via SDK wrapper
            frame = await camera_track.recv()
            img = frame.to_image() 
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            
            # --- Call the Odyseus API ---
            t0 = time.monotonic()
            result = await client.infer(buf.getvalue())
            cmd = result.get("command", "HOLD")
            logger.info(f">>> VLM DECISION: {cmd} (Latency: {time.monotonic()-t0:.2f}s)")

            if cmd not in ("HOLD", "STOP"):
                # Execute your motor commands here...
                logger.info(f"Moving: {cmd}")
                await asyncio.sleep(0.8)
                motor_controller.stop_motors() 
            else:
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Inference Loop Error: {e}")
            await asyncio.sleep(1)

async def run_client():
    player = MediaPlayer("/dev/video0", format="v4l2", options={"video_size": "640x480", "framerate": "15"})
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]))

    # Wrap the native video track using the SDK
    camera_track = mt.webrtc.LatestFrameTrack(player.video)
    pc.addTrack(camera_track)
    
    # Handshake with dashboard using SDK
    if not await client.connect_webrtc(pc):
        logger.error("WebRTC Handshake failed. Check your API Key.")
        return
        
    logger.info("Stream established. Launching brain...")
    await inference_loop(camera_track)

if __name__ == "__main__":
    asyncio.run(run_client())