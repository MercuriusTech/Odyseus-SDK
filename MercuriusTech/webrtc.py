import asyncio
import logging
from aiortc import MediaStreamTrack
import av

logger = logging.getLogger(__name__)

class LatestFrameTrack(MediaStreamTrack):
    """A WebRTC track wrapper that drains the buffer to always provide the freshest frame."""
    kind = "video"
    
    def __init__(self, source: MediaStreamTrack) -> None:
        super().__init__()
        self._source = source
        self._latest = None
        self._event = asyncio.Event()
        self._task = asyncio.create_task(self._consume_and_drain())

    async def _consume_and_drain(self) -> None:
        first_keyframe = False
        while True:
            try:
                frame = await self._source.recv()
                if not first_keyframe:
                    if frame.key_frame:
                        first_keyframe = True
                    else:
                        continue
                self._latest = frame
                self._event.set()
            except (av.error.InvalidDataError, av.error.ValueError):
                continue
            except Exception as e:
                logger.error(f"Stream consumer died: {e}")
                self.stop()
                break

    async def recv(self) -> av.VideoFrame:
        """Returns the absolute freshest frame available at this moment."""
        await self._event.wait()
        self._event.clear()
        if self._latest is None: 
            raise Exception("Track ended")
        return self._latest