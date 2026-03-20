# ODYSEUS Python SDK

The official asynchronous Python SDK for Odyseus, developed and mantained by MercuriusTech. This SDK makes it easy to hook up physical hardware (like a Raspberry Pi) or virtual environments (like Unreal Engine) to the Odyseus VLM reasoning engine.

## Installation

Clone the repository and ensure your environment is set up:

```bash
git clone [https://github.com/yourusername/mercuriustech-python.git](https://github.com/yourusername/mercuriustech-python.git)
cd mercuriustech-python
pip install aiohttp aiortc pillow av websockets
```

## Quickstart

Generate an API key from your `odyseus.xyz` Web Dashboard, then import `MercuriusTech` into your scripts:

```python
import asyncio
import MercuriusTech as mt

async def main():
    # Initialize the client. By default, it targets [https://odyseus.xyz](https://odyseus.xyz)
    client = mt.Odyseus(api_key="sk_your_api_key_here")

    # Send an image for inference
    with open("my_robot_view.jpg", "rb") as f:
        image_bytes = f.read()
        
    result = await client.infer(image_bytes)
    print(f"Odyseus Brain commands: {result['command']}")

asyncio.run(main())
```

## SDK Modules

### `mt.Odyseus`
The core client handling authentication and API interactions.
* `await client.infer(image_bytes)`: Evaluates a frame using the VLM and returns navigation commands.
* `await client.connect_webrtc(pc)`: Automates the SDP handshake to stream video to your dashboard.

### `mt.webrtc`
Helpers for video streaming.
* `mt.webrtc.LatestFrameTrack(track)`: A WebRTC wrapper that safely drains video buffers, ensuring the AI brain always receives the absolute freshest frame without latency build-up.

### `mt.unreal`
Helpers specific to Unreal Engine's PixelStreaming.
* `mt.unreal.format_ui_interaction(dict)`: Formats standard Python dicts into the exact byte-structure required by Unreal Engine's WebRTC DataChannels.
* `mt.unreal.strip_rtx_from_sdp(sdp)`: Cleans Unreal's default SDP offers to force H.264 Constrained Baseline, ensuring cross-platform compatibility.