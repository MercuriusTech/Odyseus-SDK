# ODYSEUS Python SDK

The official asynchronous Python SDK for Odyseus, developed and mantained by [Mercurius Technologies](https://mercuriustech.com). This SDK makes it easy to hook up physical hardware (like a Raspberry Pi) or virtual environments (like Unreal Engine) to the Odyseus VLM reasoning engine.

## General Overview

Generate an API key from your [odyseus.xyz](https://odyseus.xyz) Web Dashboard, then use the following structure to connect your robot's camera to the Odyseus VLM brain:

```python
import asyncio
import odyseus as od
from aiortc import RTCPeerConnection
from aiortc.contrib.media import MediaPlayer

async def main():
    # 1. Initialize the Odyseus client
    client = od.Odyseus(api_key="sk_your_api_key_here")

    # 2. Setup your camera source (e.g., Raspberry Pi Camera)
    player = MediaPlayer("/dev/video0", format="v4l2", options={"video_size": "640x480", "framerate": "15"})
    pc = RTCPeerConnection()
    
    # Use LatestFrameTrack to ensure the AI always sees the freshest frame
    camera_track = od.webrtc.LatestFrameTrack(player.video)
    pc.addTrack(camera_track)

    # 3. Establish the WebRTC stream to the server
    # This stream allows the server to build a map for long-term reasoning
    try:
        if await client.connect_webrtc(pc):
            print("Connected! Odyseus is now watching the stream.")
    except od.OdyseusStreamCapacityError as exc:
        print()
        print("STREAMING UNAVAILABLE")
        print(str(exc))
        print(exc.payload)
        return

    # 4. Continuous Inference Loop
    while True:
        # Get the latest frame from the stream
        frame = await camera_track.recv()
        img = frame.to_image()
        
        # Send frame to the VLM brain
        result = await client.infer(img)
        command = result.get("command", "HOLD")
        
        # Use these commands to drive your robot's motors:
        # Commands include: FORWARD, BACK, LEFT, RIGHT, FW_LEFT, FW_RIGHT
        print(f"Executing Robot Command: {command}")
        
        # Example: map 'FORWARD' to your motor driver
        # my_motors.move(command) 

asyncio.run(main())
```

### Why Streaming Matters
Unlike standalone inference, continuous streaming via `connect_webrtc` enables the Odyseus engine to maintain spatial awareness. The VLM processes these frames and returns high-level navigation strings that you can map directly to your hardware:

* **`FORWARD` / `BACK`**: Linear movement.
* **`LEFT` / `RIGHT`**: Pivot turns for scanning the environment.
* **`FW_LEFT` / `FW_RIGHT`**: Smooth arc turns to navigate around obstacles.

By using this streaming pattern, your robot moves from simple "see and react" behavior to intelligent, map-based navigation.

## SDK Modules

### `od.Odyseus`
The core client handling authentication and API interactions.
* `await client.infer(image_bytes)`: Evaluates a frame using the VLM and returns navigation commands.
* `await client.connect_webrtc(pc)`: Automates the SDP handshake to stream video to your dashboard.
  Raises `od.OdyseusStreamCapacityError` on HTTP `429` when robot streaming capacity is full.
  Raises `od.OdyseusWebRTCError` for other handshake failures.

### `od.webrtc`
Helpers for video streaming.
* `od.webrtc.LatestFrameTrack(track)`: A WebRTC wrapper that safely drains video buffers, ensuring the AI brain always receives the absolute freshest frame without latency build-up.

### `od.unreal`
Helpers specific to Unreal Engine's PixelStreaming.
* `od.unreal.format_ui_interaction(dict)`: Formats standard Python dicts into the exact byte-structure required by Unreal Engine's WebRTC DataChannels.
* `od.unreal.strip_rtx_from_sdp(sdp)`: Cleans Unreal's default SDP offers to force H.264 Constrained Baseline, ensuring cross-platform compatibility.


## Running Examples

### On Raspberry Pi
#### SETUP
Upload the contents of this to your pi by:
- either git cloning the repository in the pi directly **(recommended for quick testing)**
- Or uploading them via the `upload.ps1` script in this folder **(recommended for long-term development)**
>If using `upload.ps1` make sure to create an .env file with:
> ```bash
> RPI_IP={your raspberry pi IP e.g. my_pi.local}
> RPI_USER={your raspberry pi USERNAME e.g. me}
> RPI_PASS={your rasbperry pi PASSWORD}
> ```
>> ⚠️ Make sure not to delete the .gitignore in this repo to avoid accidentlly commiting that .env file to the public

Once uploaded connect to the pi via SSH or use the `connect.ps1` script on this folder.

In the `odyseus_sdk` folder run:
```bash
python -m venv venv
source ./venv/bin/activate
sudo apt-get update
sudo apt-get install swig python3-dev
sudo apt-get install liblgpio-dev
pip install gpiozero lgpio rpi-lgpio
```

And most important of all install the odyseus pip package:
```bash
pip install -e .
```
#### Running Example
The Raspberry Pi example was built using this [Robot Kit](https://www.amazon.com/dp/B0DJ7BT1V5) and a Rasbperry Pi 5, but it should also run for earlier Rasbperry Pi versions

To run the examples, make sure the `venv` is activated:
```bash
source ./venv/bin/activate
```

Once active then run:
```bash
python ./examples/raspberry_pi/pi_client.py --api-key <YOUR-API-KEY>
```

## Unreal Project

The current unreal example runs with an Unreal Simulation running Pixel Streaming. We are working on creating an executable so you can also test out the API with the Unreal Sim.

#### SETUP WITH UNREAL
Setup the venv:
```bash
python -m venv venv
./venv/Scripts/Activate.ps1
pip install -r examples/unreal_sim/requirements.txt
```

Install the pip package:
```bash
pip install -e .
```

#### RUNNING WITH UNREAL
Activate the virtual environment and run
```bash
python .\examples\unreal_sim\unreal_client.py --api-key <YOUR API KEY>
```
