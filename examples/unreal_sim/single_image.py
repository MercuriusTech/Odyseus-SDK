import argparse
import asyncio
import contextlib
import io
import json
import logging
import sys

import odyseus as od

from _bridge import UnrealVideoBridge


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("unreal.single_image")


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
        return f"{event_type}: status={payload.get('status')} objective_points={len(payload.get('objective_points') or [])}"
    if event_type == "slam_status":
        return f"{event_type}: status={event.get('status')} frames={event.get('frames_processed')}"
    if event_type == "slam_binary":
        return f"{event_type}: bytes={len(event.get('payload') or b'')}"
    return json.dumps(event)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run single-image Odyseus inference from an Unreal video stream.")
    parser.add_argument("--api-key", required=True, help="Odyseus API key")
    parser.add_argument("--prompt", required=True, help="Prompt to project into 3D")
    parser.add_argument("--url", default=None, help="Optional custom Odyseus base URL")
    parser.add_argument("--unreal-ws", default="ws://127.0.0.1:80", help="Unreal signaling websocket URL")
    parser.add_argument("--point-cloud-model", default=od.DEFAULT_POINT_CLOUD_MODEL, help="Point cloud model")
    parser.add_argument("--max-dist", type=float, default=None, help="Optional reconstruction max distance in meters")
    args = parser.parse_args()

    client = build_client(args)
    bridge = UnrealVideoBridge(args.unreal_ws)
    track = await bridge.connect()
    try:
        frame = await track.recv()
        image = frame.to_image()
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=90)

        async def on_event(event: dict) -> None:
            logger.info("%s", summarize_event(event))

        result = await client.single_image_infer(
            buf.getvalue(),
            args.prompt,
            max_dist=args.max_dist,
            point_cloud_model=args.point_cloud_model,
            on_event=on_event,
        )
        print(json.dumps({
            "session_id": result.get("session_id"),
            "mesh_ready_payload": result.get("mesh_ready_payload"),
            "objective_payload": result.get("objective_payload"),
        }, indent=2))
    finally:
        track.stop()
        await bridge.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
