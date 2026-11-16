#!/usr/bin/env python3
"""Save listening sessions, take radio breaks, and build whole-album mixes."""
import argparse
import json
import os
import random
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


class MPD:
    """A single MPD connection, optionally scoped to a partition."""

    def __init__(self, host, port, partition):
        self.connection = socket.create_connection((host, port), timeout=10)
        self.reader = self.connection.makefile(encoding="utf-8")
        if not self.reader.readline().startswith("OK MPD "):
            raise OSError("Unexpected MPD greeting")
        if partition != "default":
            self.call("partition", partition)

    def call(self, command, *args):
        """Send an MPD command and collect its ordered fields."""
        def quote(value):
            value = str(value)
            if any(char in value for char in "\r\n\x00"):
                raise ValueError("Invalid MPD argument")
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        line = " ".join([command, *(quote(arg) for arg in args)])
        self.connection.sendall((line + "\n").encode())
        fields = []
        for reply in self.reader:
            if reply == "OK\n":
                return fields
            if reply.startswith("ACK"):
                raise OSError(reply.strip())
            key, _, value = reply.rstrip("\n").partition(": ")
            fields.append((key, value))
        raise OSError("MPD disconnected")

    def close(self):
        """Release the connection."""
        self.reader.close()
        self.connection.close()


def records(fields):
    """Group MPD song records by their opening file field."""
    rows = []
    for key, value in fields:
        if key == "file":
            rows.append({})
        if rows:
            rows[-1].setdefault(key, value)
    return rows


def snapshot(mpd):
    """Read queue and position, rejecting concurrent queue or song changes."""
    before = dict(mpd.call("status"))
    queue = records(mpd.call("playlistinfo"))
    after = dict(mpd.call("status"))
    if any(before.get(k) != after.get(k) for k in ("playlist", "songid")):
        raise ValueError("Queue or current track changed. Try again.")
    return {"uris": [row["file"] for row in queue],
            "position": int(after.get("song", 0)),
            "elapsed": float(after.get("elapsed", 0)),
            "state": after["state"]}


def write_json(path, data, replace=False):
    """Write a JSON document atomically without clobbering named saves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as out:
            temp = Path(out.name)
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write("\n")
        if replace:
            temp.replace(path)
        else:
            os.link(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def replace_queue(mpd, uris):
    """Stage new songs before deleting the existing queue entries."""
    original = records(mpd.call("playlistinfo"))
    added = []
    try:
        for uri in uris:
            added.append(dict(mpd.call("addid", uri))["Id"])
    except (OSError, ValueError):
        for identifier in reversed(added):
            mpd.call("deleteid", identifier)
        raise
    mpd.call("stop")
    for song in original:
        mpd.call("deleteid", song["Id"])


def restore(mpd, saved):
    """Restore the queue, position, and play or pause state."""
    uris, position = saved["uris"], saved["position"]
    elapsed, state = saved["elapsed"], saved["state"]
    if (not isinstance(uris, list) or not all(isinstance(uri, str) for uri in uris)
            or type(position) is not int or not 0 <= position < max(1, len(uris))
            or type(elapsed) not in (int, float) or not 0 <= elapsed < float("inf")
            or state not in {"play", "pause", "stop"}):
        raise ValueError("Invalid session file")
    replace_queue(mpd, uris)
    if uris and state != "stop":
        mpd.call("play", position)
        if elapsed and not urlsplit(uris[position]).scheme:
            mpd.call("seek", position, elapsed)
        if state == "pause":
            mpd.call("pause", 1)


def handoff(mpd, path, uri):
    """Wait for a finite song to finish and start a radio stream."""
    uris = [uri] if isinstance(uri, str) else uri
    if not uris:
        raise ValueError("No tracks to hand off to")
    for item in uris:
        parsed = urlsplit(item)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Enter a station's HTTP(S) stream URL")
    status = dict(mpd.call("status"))
    if status.get("state") != "play" or float(status.get("duration", 0)) <= 0:
        raise ValueError("Handoff needs a playing track with a known duration")
    print("Waiting for the current song to finish. Ctrl+C cancels.", flush=True)
    while True:
        time.sleep(0.5)
        current = dict(mpd.call("status"))
        if current.get("playlist") != status.get("playlist"):
            raise ValueError("Queue changed while waiting")
        if current.get("songid") != status.get("songid"):
            break
        if current["state"] != "play":
            if (current["state"] == "stop"
                    and float(status.get("duration", 0)) -
                    float(status.get("elapsed", 0)) <= 1):
                break
            raise ValueError("Playback stopped or paused before the handoff")
        status = current
    saved = snapshot(mpd)
    write_json(path, saved)
    replace_queue(mpd, uris)
    mpd.call("play", 0)


def track_number(value):
    """Sort numeric disc and track tags, including values like 2/10."""
    return int(value.split("/", 1)[0]) if value.split("/", 1)[0].isdigit() else 0


def albums(mpd, genre, decade, deep):
    """Group complete local albums, then apply filters at album level."""
    groups = {}
    for row in records(mpd.call("listallinfo")):
        if row.get("Album") and row.get("duration"):
            artist = row.get("AlbumArtist") or row.get("Artist", "")
            identifier = row.get("MUSICBRAINZ_ALBUMID") or str(
                Path(row["file"]).parent.parent
                if track_number(row.get("Disc", "")) and
                Path(row["file"]).parent.name.lower().startswith(("disc", "cd"))
                else Path(row["file"]).parent
            )
            groups.setdefault((artist, row["Album"], identifier), []).append(row)
    counts = {}
    if deep:
        uri = None
        for key, value in mpd.call("sticker", "find", "song", "", "playCount"):
            if key == "file":
                uri = value
            elif key == "sticker" and uri and value.startswith("playCount="):
                count = value.partition("=")[2]
                if count.isdecimal():
                    counts[uri] = int(count)
    result = []
    for (artist, name, identifier), tracks in groups.items():
        if genre and not any(genre.casefold() == t.get("Genre", "").casefold() for t in tracks):
            continue
        if decade and not any(t.get("Date", "")[:4].isdigit() and
                              int(t["Date"][:4]) // 10 * 10 == decade for t in tracks):
            continue
        tracks.sort(key=lambda t: (track_number(t.get("Disc", "")),
                                   track_number(t.get("Track", "")), t["file"]))
        duration = sum(float(t["duration"]) for t in tracks)
        if duration <= 0:
            continue
        uris = [t["file"] for t in tracks]
        result.append({"id": json.dumps([artist, name, identifier]), "artist": artist,
                       "album": name, "seconds": duration, "uris": uris,
                       "plays": sum(counts.get(uri, 0) for uri in uris) / len(uris)})
    return result


def mix(candidates, budget, locked, deep):
    """Sample mixes and select a close fit, favoring rare albums in Deep Shelf."""
    remaining = budget - sum(item["seconds"] for item in locked)
    if remaining < 0:
        raise ValueError("Locked albums exceed the time budget")
    options = [item for item in candidates if item["id"] not in {x["id"] for x in locked}
               and item["seconds"] <= remaining]
    best, best_score = [], (-1, -float("inf"), -1)
    for _ in range(64):
        order = random.sample(options, len(options))
        if deep:
            order.sort(key=lambda item: random.random() * (1 + item["plays"]))
        chosen, total = [], 0
        for item in order:
            if total + item["seconds"] <= remaining:
                chosen.append(item)
                total += item["seconds"]
        # Within two minutes of fit, prefer the less-played set in Deep Shelf.
        score = ((int(total // 120), -sum(x["plays"] for x in chosen), total)
                 if deep else (total, 0, 0))
        if score > best_score:
            best, best_score = chosen, score
    return locked + best


def main():
    """Dispatch the requested operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6600)
    parser.add_argument("--partition", default="default")
    commands = parser.add_subparsers(dest="operation")
    for name in ("save", "resume"):
        commands.add_parser(name).add_argument("session", type=Path)
    radio = commands.add_parser("handoff")
    radio.add_argument("session", type=Path)
    radio.add_argument("url")
    build = commands.add_parser("mix")
    build.add_argument("preview", type=Path)
    build.add_argument("--minutes", "-m", required=True, type=int)
    build.add_argument("--genre", "-g")
    build.add_argument("--decade", "-d", type=int)
    build.add_argument("--deep-shelf", action="store_true")
    build.add_argument("--lock", "-l", type=int, action="append", default=[])
    commands.add_parser("queue-mix").add_argument("preview", type=Path)
    args = parser.parse_args()
    if not args.operation:
        parser.print_help()
        return
    mpd = None
    try:
        mpd = MPD(args.host, args.port, args.partition)
        if args.operation == "save":
            write_json(args.session, snapshot(mpd))
        elif args.operation == "resume":
            restore(mpd, json.loads(args.session.read_text(encoding="utf-8")))
        elif args.operation == "handoff":
            if args.session.exists():
                raise ValueError("Choose a new session filename")
            handoff(mpd, args.session, args.url)
        elif args.operation == "queue-mix":
            items = json.loads(args.preview.read_text(encoding="utf-8"))["albums"]
            for item in items:
                for uri in item["uris"]:
                    mpd.call("addid", uri)
        else:
            if args.minutes <= 0 or (args.decade and args.decade % 10):
                raise ValueError("Use a positive time budget and a decade such as 1980")
            old = json.loads(args.preview.read_text(encoding="utf-8"))["albums"] if args.lock else []
            if any(not 1 <= n <= len(old) for n in args.lock):
                raise ValueError("Lock numbers must refer to albums in the existing preview")
            locked = [item for n, item in enumerate(old, 1) if n in args.lock]
            result = mix(albums(mpd, args.genre, args.decade, args.deep_shelf),
                         args.minutes * 60, locked, args.deep_shelf)
            if not result:
                raise ValueError("No whole albums fit the time budget and filters")
            write_json(args.preview, {"albums": result}, replace=True)
            for n, item in enumerate(result, 1):
                print(f"{n}. {item['artist']} — {item['album']} ({item['seconds']/60:.1f} min)")
            print(f"Total: {sum(item['seconds'] for item in result)/60:.1f} min")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")
    finally:
        if mpd:
            mpd.close()


if __name__ == "__main__":
    main()
