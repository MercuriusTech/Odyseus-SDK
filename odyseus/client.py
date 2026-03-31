import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription


class Odyseus:
    """Async client for the Odyseus Odyseus API."""
    
    def __init__(self, api_key: str, base_url: str = "https://odyseus.xyz"):
        if not api_key:
            raise ValueError("An API key is required to initialize the Odyseus client.")
            
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": self.api_key}
        self._gpu_base_url = None

    async def resolve_webrtc_session(self) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/api/webrtc/sim-session", headers=self.headers) as resp:
                if resp.status == 404:
                    return {}
                if resp.status != 200:
                    detail = await resp.text()
                    raise Exception(f"Failed to resolve GPU session ({resp.status}): {detail}")
                payload = await resp.json()
                self._gpu_base_url = payload.get("gpu_base_url") or self._gpu_base_url
                return payload

    async def resolve_gpu_base_url(self) -> str:
        if self._gpu_base_url:
            return self._gpu_base_url

        session_info = await self.resolve_webrtc_session()
        gpu_base_url = session_info.get("gpu_base_url")
        if gpu_base_url:
            self._gpu_base_url = gpu_base_url.rstrip("/")
            return self._gpu_base_url

        return self.base_url

    async def infer(self, image_bytes: bytes) -> dict:
        """Sends an image to the VLM brain and returns the navigation command."""
        target_base_url = await self.resolve_gpu_base_url()
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', image_bytes, filename='frame.jpg', content_type='image/jpeg')
            
            async with session.post(f"{target_base_url}/infer", data=data, headers=self.headers) as resp:
                if resp.status == 400:
                    error_detail = await resp.text()
                    raise Exception(f"Bad Request (400): {error_detail}")
                elif resp.status != 200:
                    raise Exception(f"Server Error ({resp.status})")
                
                return await resp.json()

    async def connect_webrtc(self, pc: RTCPeerConnection, unreal_fix: bool = False) -> bool:
        """Handles the WebRTC SDP handshake. Set unreal_fix=True for Unreal Engine streams."""
        offer = await pc.createOffer()
        sdp = offer.sdp

        # Apply the H.264 profile fix if requested
        if unreal_fix:
            from .unreal import strip_rtx_from_sdp
            sdp = strip_rtx_from_sdp(sdp)
    
        await pc.setLocalDescription(RTCSessionDescription(sdp=sdp, type=offer.type))
    
        async with aiohttp.ClientSession() as session:
            # Note: We use pc.localDescription.sdp here to ensure we send the "cleaned" version
            session_info = await self.resolve_webrtc_session()
            if session_info:
                offer_url = session_info["offer_url"]
                self._gpu_base_url = session_info.get("gpu_base_url") or self._gpu_base_url
                payload = {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type,
                    "token": session_info["token"],
                }
                headers = {}
            else:
                offer_url = f"{self.base_url}/offer-sim"
                payload = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
                headers = self.headers

            async with session.post(offer_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    return False

                answer = await resp.json()
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
                )
                return True
