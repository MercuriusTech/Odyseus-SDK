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

    async def connect_webrtc(self, pc: RTCPeerConnection) -> bool:
        """Handles the WebRTC SDP handshake to connect a local video stream to the dashboard."""
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        
        async with aiohttp.ClientSession() as session:
            payload = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            
            async with session.post(f"{self.base_url}/offer-sim", json=payload, headers=self.headers) as resp:
                if resp.status != 200:
                    return False
                    
                answer = await resp.json()
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
                )
                return True