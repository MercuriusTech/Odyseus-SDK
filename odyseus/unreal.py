import json

def format_ui_interaction(command_dict: dict) -> bytes:
    """Formats a JSON dictionary into the byte structure required by Unreal Engine DataChannels."""
    json_str = json.dumps(command_dict)
    payload = bytearray([50])
    payload.extend(len(json_str).to_bytes(2, byteorder="little"))
    payload.extend(json_str.encode("utf-16-le"))
    return bytes(payload)

def strip_rtx_from_sdp(sdp: str) -> str:
    """Forces H.264 Constrained Baseline to ensure compatibility with Unreal Engine streams."""
    lines = sdp.splitlines()
    bad_pts = {line.split(":")[1].split()[0] for line in lines if "a=rtpmap:" in line and ("rtx/" in line.lower() or "red/" in line.lower() or "ulpfec" in line.lower())}
    
    filtered = []
    h264_params = "packetization-mode=1;profile-level-id=42e01f"

    for line in lines:
        if any(f":{pt}" in line for pt in bad_pts) and ("a=rtpmap:" in line or "a=fmtp:" in line or "a=rtcp-fb:" in line):
            continue
        if "a=fmtp:" in line and "H264" in line:
            parts = line.split(" ", 1)
            line = f"{parts[0]} {h264_params}"
        if line.startswith("m=video"):
            parts = line.split()
            filtered.append(" ".join(parts[:4] + [p for p in parts[4:] if p not in bad_pts]))
            continue
        filtered.append(line)
        
    return "\r\n".join(filtered)