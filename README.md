# ODYSEUS Python SDK

Async Python SDK for the Odyseus API.

The current API has two main modes:

- `single_image_infer(...)` for one image plus a prompt, returning projected 3D targets and reconstruction events.
- `connect_webrtc(...)` / `connect_video_session(...)` for live video over WebRTC, with SLAM events streamed from the runtime.

The default point cloud model is `depth-anything`.

## Install

```bash
python -m venv venv
source ./venv/bin/activate
pip install -e .
```

## Single Image

```python
import asyncio
import odyseus as od


async def main():
    client = od.Odyseus(api_key="sk_your_api_key_here")

    async def on_event(event: dict) -> None:
        print(event.get("type"), event.get("session_id"))

    image_bytes = open("frame.jpg", "rb").read()
    result = await client.single_image_infer(
        image_bytes,
        "find the coffee mug",
        on_event=on_event,
    )

    print(result["session_id"])
    print(result["objective_payload"])


asyncio.run(main())
```

`single_image_infer(...)` uses the existing runtime websocket flow and emits the same event types the backend already sends:

- `single_infer_ready`
- `single_infer_accepted`
- `single_infer_mesh_ready`
- `single_infer_objective`
- `slam_status`
- `slam_binary`
- `error`

The returned aggregate dict includes:

- `session_id`
- `session`
- `mesh_ready_payload`
- `objective_payload`
- `latest_slam_status`
- `latest_slam_binary`
- `events`

Backend field names are preserved exactly, including:

- `objective_points`
- `objective_world_point`
- `objective_image_point`
- `robot_pose`

## Live Video Over WebRTC

```python
import asyncio
from aiortc import RTCPeerConnection
from aiortc.contrib.media import MediaPlayer
import odyseus as od


async def main():
    client = od.Odyseus(api_key="sk_your_api_key_here")
    player = MediaPlayer("/dev/video0", format="v4l2", options={"video_size": "640x480", "framerate": "15"})

    pc = RTCPeerConnection()
    track = od.webrtc.LatestFrameTrack(player.video)
    pc.addTrack(track)

    session = await client.connect_video_session(pc)
    print(session)

    async for event in client.iter_slam_events(session["session_id"]):
        print(event.get("type"), event.get("status"))


asyncio.run(main())
```

`connect_video_session(...)` keeps video transport WebRTC-driven. Session inspection uses the existing runtime SLAM websocket and session APIs.

Available helpers:

- `await client.connect_webrtc(pc, unreal_fix=False)`
- `await client.connect_video_session(pc, unreal_fix=False)`
- `await client.get_live_session_state(session_id=None)`
- `await client.get_session_frame(session_id)`
- `client.iter_slam_events(session_id=None)`

`infer(...)` is still present for legacy compatibility, but the new SDK examples use `single_image_infer(...)` for image mode.

## Examples

### Raspberry Pi

Single image:

```bash
python ./examples/raspberry_pi/single_image.py \
  --api-key <YOUR_API_KEY> \
  --prompt "find the chair"
```

Live video:

```bash
python ./examples/raspberry_pi/live_video.py \
  --api-key <YOUR_API_KEY>
```

### Unreal

Install the extra requirements if you want a standalone example environment:

```bash
pip install -r ./examples/unreal_sim/requirements.txt
pip install -e .
```

Single image from the Unreal video stream:

```bash
python ./examples/unreal_sim/single_image.py \
  --api-key <YOUR_API_KEY> \
  --prompt "find the table"
```

Live video relay from Unreal:

```bash
python ./examples/unreal_sim/live_video.py \
  --api-key <YOUR_API_KEY>
```

### Static Files

Single image from a path:

```bash
python ./examples/static/single_image.py \
  --api-key <YOUR_API_KEY> \
  --path ./frame.jpg \
  --prompt "find the doorway"
```

Live video from a path over WebRTC:

```bash
python ./examples/static/live_video.py \
  --api-key <YOUR_API_KEY> \
  --path ./demo.mp4
```

## Notes

- Live video transport remains WebRTC end to end.
- Single-image mode defaults to `depth-anything` unless you pass `--point-cloud-model`.
- The frontend live prompt / infer flow is separate from this SDK refresh and is not changed here.
