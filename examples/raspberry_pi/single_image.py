import argparse
import asyncio
import io
import json
import logging

from aiortc.contrib.media import MediaPlayer

import odyseus as od


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("raspberry_pi.single_image")


def build_client(args) -> od.Odyseus:
    kwargs = {"api_key": args.api_key}
    if args.url:
        kwargs["base_url"] = args.url
    return od.Odyseus(**kwargs)


def summarize_event(event: dict) -> str:
    event_type = event.get("type", "unknown")
    if event_type == "single_infer_mesh_ready":
        payload = event.get("payload") or {}
        slam = payload.get("slam") or {}
        return f"{event_type}: status={payload.get('status')} has_mesh={slam.get('has_mesh')}"
    if event_type == "single_infer_objective":
        payload = event.get("payload") or {}
        points = len(payload.get("objective_points") or [])
        return f"{event_type}: status={payload.get('status')} objective_points={points}"
    if event_type == "slam_status":
        return f"{event_type}: status={event.get('status')} frames={event.get('frames_processed')}"
    if event_type == "slam_binary":
        return f"{event_type}: bytes={len(event.get('payload') or b'')}"
    return json.dumps(event)


async def capture_frame_bytes(device: str) -> bytes:
    player = MediaPlayer(device, format="v4l2", options={"video_size": "640x480", "framerate": "15"})
    if player.video is None:
        raise RuntimeError(f"No video track available from {device}")
    track = od.webrtc.LatestFrameTrack(player.video)
    frame = await track.recv()
    image = frame.to_image()
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    track.stop()
    return buf.getvalue()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run single-image Odyseus inference from a Raspberry Pi camera.")
    parser.add_argument("--api-key", required=True, help="Odyseus API key")
    parser.add_argument("--prompt", required=True, help="Prompt to project into 3D")
    parser.add_argument("--camera", default="/dev/video0", help="Video device path")
    parser.add_argument("--url", default=None, help="Optional custom Odyseus base URL")
    parser.add_argument("--point-cloud-model", default=od.DEFAULT_POINT_CLOUD_MODEL, help="Point cloud model")
    parser.add_argument("--max-dist", type=float, default=None, help="Optional reconstruction max distance in meters")
    args = parser.parse_args()

    client = build_client(args)
    image_bytes = await capture_frame_bytes(args.camera)

    async def on_event(event: dict) -> None:
        logger.info("%s", summarize_event(event))

    result = await client.single_image_infer(
        image_bytes,
        args.prompt,
        max_dist=args.max_dist,
        point_cloud_model=args.point_cloud_model,
        on_event=on_event,
    )

    objective = result.get("objective_payload") or {}
    mesh_ready = result.get("mesh_ready_payload") or {}
    logger.info("session_id=%s", result.get("session_id"))
    logger.info("mesh_status=%s", mesh_ready.get("status"))
    logger.info("objective_status=%s", objective.get("status"))
    print(json.dumps({
        "session_id": result.get("session_id"),
        "mesh_ready_payload": mesh_ready,
        "objective_payload": objective,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
