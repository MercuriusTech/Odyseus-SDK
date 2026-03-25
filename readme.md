# ODYSEUS Python SDK

The official asynchronous Python SDK for Odyseus, developed and mantained by MercuriusTech. This SDK makes it easy to hook up physical hardware (like a Raspberry Pi) or virtual environments (like Unreal Engine) to the Odyseus VLM reasoning engine.

## Installation

Clone the repository and ensure your environment is set up:

```bash
git clone https://github.com/MercuriusTech/Odyseus-SDK.git
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
