"""Resolve handoff choices using the installed music service bridges."""
import json
import re
from urllib.parse import urlencode
from urllib.request import urlopen

SERVICES = {
    "Pandora": ("pandora", 18765),
    "TuneIn Favorites": ("tunein", 18766),
    "iHeartRadio Favorites": ("iheart", 18767),
    "SomaFM": ("somafm", 18768),
    "iBroadcast": ("ibroadcast", 18769),
}


def request(provider, path):
    """Fetch a bounded JSON response from a configured local bridge."""
    port = next(port for name, port in SERVICES.values() if name == provider)
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=12) as response:
        return json.loads(response.read(2_000_000))


def choices(provider, album="", offset=0):
    """List stations, or a page of iBroadcast albums or tracks."""
    if provider != "ibroadcast":
        return {"entries": request(provider, "/stations")}
    if album and not re.fullmatch(r"[0-9]+", album):
        raise ValueError("Invalid iBroadcast album")
    if offset < 0:
        raise ValueError("Invalid page")
    path = f"/tracks/{album}" if album else "/albums"
    return request(provider, f"{path}?offset={offset}")


def resolve(provider, item, album=""):
    """Resolve the selected source to MPD URLs with picker metadata."""
    if provider == "direct":
        return [item]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item):
        raise ValueError("Invalid selection")
    if provider == "ibroadcast":
        if item == "whole" and re.fullmatch(r"[0-9]+", album):
            tracks = request(provider, f"/tracks/{album}")
            return [resolve(provider, track["id"])[0] for track in tracks]
        if not re.fullmatch(r"[0-9]+", item):
            raise ValueError("Invalid iBroadcast track")
        return [f"http://127.0.0.1:18769/stream/{item}"]
    if provider == "pandora":
        return [f"http://127.0.0.1:18765/stream/{item}"]
    stream = request(provider, f"/stations/{item}")
    uri = stream["url"]
    if provider in {"tunein", "iheart"}:
        metadata = urlencode({
            f"mympd-{provider}": item, "name": stream.get("name") or "",
            "art": stream.get("artFile") or "",
        })
        uri = uri.split("#", 1)[0] + "#" + metadata.replace("+", "%20")
    return [uri]
