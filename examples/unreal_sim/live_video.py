import argparse
import asyncio
import contextlib
import json
import logging
import sys

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

import odyseus as od

from _bridge import UnrealVideoBridge


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("unreal.live_video")


def build_client(args) -> od.Odyseus:
    kwargs = {"api_key": args.api_key}
    if args.url:
        kwargs["base_url"] = args.url
    return od.Odyseus(**kwargs)


def summarize_event(event: dict) -> str:
    event_type = event.get("type", "unknown")
    if event_type == "slam_stream_ready":
        return f"{event_type}: session_id={event.get('session_id')}"
    if event_type == "slam_status":
        return (
            f"{event_type}: status={event.get('status')} "
            f"frames={event.get('frames_processed')} "
            f"trajectory_count={event.get('trajectory_count')}"
        )
    if event_type == "slam_binary":
        return f"{event_type}: bytes={len(event.get('payload') or b'')}"
    return json.dumps(event)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Relay Unreal video to Odyseus over WebRTC and inspect SLAM events.")
    parser.add_argument("--api-key", required=True, help="Odyseus API key")
    parser.add_argument("--url", default=None, help="Optional custom Odyseus base URL")
    parser.add_argument("--unreal-ws", default="ws://127.0.0.1:80", help="Unreal signaling websocket URL")
    args = parser.parse_args()

    client = build_client(args)
    bridge = UnrealVideoBridge(args.unreal_ws)
    source_track = await bridge.connect()

    relay_pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]))
    relay_pc.addTrack(source_track)

    try:
        session = await client.connect_video_session(relay_pc, unreal_fix=True)
        logger.info("Connected live session %s", session.get("session_id"))
        print(json.dumps(session, indent=2))
        async for event in client.iter_slam_events(session.get("session_id")):
            logger.info("%s", summarize_event(event))
    finally:
        source_track.stop()
        await relay_pc.close()
        await bridge.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
