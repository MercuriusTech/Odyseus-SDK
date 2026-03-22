import argparse
import asyncio
import io
import logging
import time
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer

# Import your local motor controller script
import motor_controller

# --- IMPORT MERCURIUSTECH SDK ---
import MercuriusTech as mt

# ============================================================
# ARGUMENT PARSING
# ============================================================
parser = argparse.ArgumentParser(description="Odyseus Pi Client")
parser.add_argument("--api-key", type=str, required=True, help="MercuriusTech API Key")
# Added optional base-url argument. default=None ensures we can check if it was used.
parser.add_argument("--url", type=str, default=None, help="Optional custom Base URL for the API")
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("pi_client")

# ============================================================
# CONFIGURATION & TUNING
# ============================================================
STRAIGHT_SPEED = 0.35  
TURN_SPEED = 0.5      
SEARCH_SPEED = 0.5    

MOVE_DURATION = 0.8    
TURN_DURATION = 0.4    
SETTLE_TIME = 0.3      

# Initialize the client. 
# If args.base_url is None, the SDK class will use its own internal default.
# Create a dictionary for the constructor arguments
client_kwargs = {"api_key": args.api_key}

# Only add base_url if the user actually provided one via CLI
if args.url:
    client_kwargs["base_url"] = args.url

# Unpack the dictionary into the class
client = mt.Odyseus(**client_kwargs)

# ... [The rest of your motor mapping and inference loop remains the same] ...

# ============================================================
# MOTOR EXECUTION MAPPING
# ============================================================
def execute_motor_command(cmd_string: str):
    """Maps VLM string commands to motor_controller functions."""
    cmd = cmd_string.upper().strip()

    if cmd == "FORWARD":
        motor_controller.move_forward(STRAIGHT_SPEED)
    elif cmd in ("BACKWARD", "BACK"):
        motor_controller.move_back(STRAIGHT_SPEED)
    elif cmd == "LEFT" or cmd=="SEARCH_LEFT":
        motor_controller.rotate_left(TURN_SPEED)
    elif cmd == "FW_LEFT":
        motor_controller.move_fw_left(STRAIGHT_SPEED * 2)
    elif cmd == "FW_RIGHT":
        motor_controller.move_fw_right(STRAIGHT_SPEED * 2)
    elif cmd == "RIGHT" or cmd=="SEARCH_RIGHT":
        motor_controller.rotate_right(TURN_SPEED)
    elif cmd in ("STOP", "HOLD"):
        motor_controller.stop_motors()
    else:
        logger.warning(f"Unknown command '{cmd}'. Stopping motors.")
        motor_controller.stop_motors()

async def inference_loop(camera_track: mt.webrtc.LatestFrameTrack):
    logger.info("Starting inference loop...")
    while True:
        try:
            motor_controller.stop_motors()
            await asyncio.sleep(SETTLE_TIME) 

            frame = await camera_track.recv()
            img = frame.to_image() 
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            
            t0 = time.monotonic()
            result = await client.infer(buf.getvalue())
            cmd = result.get("command", "HOLD")
            logger.info(f">>> VLM DECISION: {cmd} (Latency: {time.monotonic()-t0:.2f}s)")

            if cmd not in ("HOLD", "STOP"):
                is_turn = any(x in cmd for x in ["LEFT", "RIGHT"])
                pulse_time = TURN_DURATION if is_turn else MOVE_DURATION
                
                logger.info(f"Executing: {cmd} for {pulse_time}s")
                execute_motor_command(cmd)
                
                await asyncio.sleep(pulse_time)
                motor_controller.stop_motors() 
            else:
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Inference Loop Error: {e}")
            await asyncio.sleep(1)

async def run_client():
    player = MediaPlayer("/dev/video0", format="v4l2", options={"video_size": "640x480", "framerate": "15"})
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]))

    camera_track = mt.webrtc.LatestFrameTrack(player.video)
    pc.addTrack(camera_track)
    
    if not await client.connect_webrtc(pc):
        logger.error("WebRTC Handshake failed. Check your API Key.")
        return
        
    logger.info("Stream established. Launching brain...")
    await inference_loop(camera_track)

if __name__ == "__main__":
    try:
        motor_controller.stop_motors()
        asyncio.run(run_client())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    finally:
        motor_controller.stop_motors()