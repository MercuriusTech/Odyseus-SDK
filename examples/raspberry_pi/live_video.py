import argparse
import asyncio
import json
import logging

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection
from aiortc.contrib.media import MediaPlayer

import odyseus as od


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("raspberry_pi.live_video")


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
    parser = argparse.ArgumentParser(description="Stream Raspberry Pi camera video to Odyseus over WebRTC.")
    parser.add_argument("--api-key", required=True, help="Odyseus API key")
    parser.add_argument("--camera", default="/dev/video0", help="Video device path")
    parser.add_argument("--url", default=None, help="Optional custom Odyseus base URL")
    args = parser.parse_args()

    client = build_client(args)
    player = MediaPlayer(args.camera, format="v4l2", options={"video_size": "640x480", "framerate": "15"})
    if player.video is None:
        raise RuntimeError(f"No video track available from {args.camera}")

    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]))
    track = od.webrtc.LatestFrameTrack(player.video)
    pc.addTrack(track)

    session = await client.connect_video_session(pc)
    logger.info("Connected live session %s", session.get("session_id"))
    print(json.dumps(session, indent=2))

    try:
        async for event in client.iter_slam_events(session.get("session_id")):
            logger.info("%s", summarize_event(event))
    finally:
        track.stop()
        await pc.close()


if __name__ == "__main__":
    asyncio.run(main())
