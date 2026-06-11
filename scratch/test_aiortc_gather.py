import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer

async def test_gather():
    config = RTCConfiguration(iceServers=[
        RTCIceServer(urls="stun:stun.l.google.com:19302")
    ])
    pc = RTCPeerConnection(configuration=config)
    
    # We need to add at least one track or data channel to trigger ICE gathering
    pc.createDataChannel("chat")
    
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    print("Gathering ICE candidates...")
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
        
    print("ICE gathering complete!")
    print("SDP Offer length:", len(pc.localDescription.sdp))
    print("Contains candidates:", "candidate" in pc.localDescription.sdp)
    
    await pc.close()

asyncio.run(test_gather())
