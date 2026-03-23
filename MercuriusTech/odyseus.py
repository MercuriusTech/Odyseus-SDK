import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription

class Odyseus:
    """Async client for the MercuriusTech Odyseus API."""
    
    def __init__(self, api_key: str, base_url: str = "https://odyseus.xyz"):
        if not api_key:
            raise ValueError("An API key is required to initialize the MercuriusTech client.")
            
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": self.api_key}

    async def infer(self, image_bytes: bytes) -> dict:
        """Sends an image to the VLM brain and returns the navigation command."""
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', image_bytes, filename='frame.jpg', content_type='image/jpeg')
            
            async with session.post(f"{self.base_url}/infer", data=data, headers=self.headers) as resp:
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
            payload = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

            async with session.post(f"{self.base_url}/offer-sim", json=payload, headers=self.headers) as resp:
                if resp.status != 200:
                    return False

                answer = await resp.json()
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
                )
                return True