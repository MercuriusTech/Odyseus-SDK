import argparse
import asyncio
import json
import logging
from pathlib import Path

import odyseus as od


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("static.single_image")


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
    parser = argparse.ArgumentParser(description="Run single-image Odyseus inference from a local image path.")
    parser.add_argument("--api-key", required=True, help="Odyseus API key")
    parser.add_argument("--path", required=True, help="Path to an input image")
    parser.add_argument("--prompt", required=True, help="Prompt to project into 3D")
    parser.add_argument("--url", default=None, help="Optional custom Odyseus base URL")
    parser.add_argument("--point-cloud-model", default=od.DEFAULT_POINT_CLOUD_MODEL, help="Point cloud model")
    parser.add_argument("--max-dist", type=float, default=None, help="Optional reconstruction max distance in meters")
    args = parser.parse_args()

    image_bytes = Path(args.path).read_bytes()
    client = build_client(args)

    async def on_event(event: dict) -> None:
        logger.info("%s", summarize_event(event))

    result = await client.single_image_infer(
        image_bytes,
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


if __name__ == "__main__":
    asyncio.run(main())
