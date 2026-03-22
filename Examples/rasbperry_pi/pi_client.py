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
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("pi_client")

# ============================================================
# CONFIGURATION & TUNING
# ============================================================
# These constants are brought over from your original script
STRAIGHT_SPEED = 0.35  
TURN_SPEED = 0.5      
SEARCH_SPEED = 0.5    

MOVE_DURATION = 0.8    
TURN_DURATION = 0.4    
SETTLE_TIME = 0.3      

client = mt.Odyseus(api_key=args.api_key)

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
    elif cmd == "LEFT":
        motor_controller.rotate_left(TURN_SPEED)
    elif cmd == "FW_LEFT":
        motor_controller.move_fw_left(STRAIGHT_SPEED * 2)
    elif cmd == "FW_RIGHT":
        motor_controller.move_fw_right(STRAIGHT_SPEED * 2)
    elif cmd == "RIGHT":
        motor_controller.rotate_right(TURN_SPEED)
    elif cmd == "SEARCH_LEFT":
        motor_controller.rotate_left(SEARCH_SPEED)
    elif cmd == "SEARCH_RIGHT":
        motor_controller.rotate_right(SEARCH_SPEED)
    elif cmd in ("STOP", "HOLD"):
        motor_controller.stop_motors()
    else:
        logger.warning(f"Unknown command '{cmd}'. Stopping motors.")
        motor_controller.stop_motors()

# ============================================================
# INFERENCE LOOP (The Brain)
# ============================================================
async def inference_loop(camera_track: mt.webrtc.LatestFrameTrack):
    logger.info("Starting inference loop...")
    while True:
        try:
            # Ensure motors are stopped while settling for a clear photo
            motor_controller.stop_motors()
            await asyncio.sleep(SETTLE_TIME) 

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
                # Determine if we should use turn or move duration
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

# ============================================================
# MAIN CLIENT RUNNER
# ============================================================
async def run_client():
    player = MediaPlayer("/dev/video0", format="v4l2", options={"video_size": "640x480", "framerate": "15"})
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]))

    # Wrap the native video track using the SDK's specialized track
    camera_track = mt.webrtc.LatestFrameTrack(player.video)
    pc.addTrack(camera_track)
    
    # Handshake with dashboard using SDK
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