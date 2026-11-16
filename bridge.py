#!/usr/bin/env python3
"""Serve myMPD listening dialogs from a loopback-only bridge."""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import listening
import services


class Handler(BaseHTTPRequestHandler):
    """Translate myMPD form submissions into listening operations."""

    def do_POST(self):
        """Accept JSON only from the local myMPD script."""
        if self.path != "/" or self.headers.get("Origin"):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 8192 or self.headers.get_content_type() != "application/json":
                raise ValueError("Invalid request")
            reply = self.operation(json.loads(self.rfile.read(size)))
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
            reply = {"message": str(error)}
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def operation(self, args):
        """Run a session action or generate a reviewable mix."""
        root = self.server.state
        action = args["action"]
        if action == "handoff-status":
            job = self.server.handoff
            return {"message": ("No handoff requested" if job is None else
                                job.result() if job.done() else "Waiting for the current song")}
        if action == "sessions":
            return {"sessions": sorted(
                path.stem for path in (root / "sessions").glob("*.json")
                if re.fullmatch(r"[A-Za-z0-9 _-]{1,64}", path.stem)
            )}
        if action == "sources":
            installed = set(args.get("installed", []))
            return {"entries": [
                {"id": provider, "name": script.removesuffix(" Favorites")}
                for script, (provider, _) in services.SERVICES.items()
                if script in installed
            ]}
        if action == "choices":
            return services.choices(args["provider"], args.get("album", ""),
                                    int(args.get("offset", 0)))
        name = args.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9 _-]{1,64}", name) or not name.strip():
            raise ValueError("Use a name of up to 64 letters, numbers, spaces, underscores or dashes")
        directory = root / ("mixes" if action in {"mix", "queue", "replace"} else "sessions")
        path = directory / (name + ".json")
        if action == "handoff":
            if path.exists():
                raise ValueError("Choose a new session name")
            if self.server.handoff and not self.server.handoff.done():
                raise ValueError("A radio handoff is already waiting")
            provider = args.get("provider", "direct")
            uris = services.resolve(provider, args.get("item") or args.get("url", ""),
                                    args.get("album", ""))
            self.server.handoff = self.server.worker.submit(self.run_handoff, path, uris)
            return {"message": "Handoff requested. Check Handoff status for the result."}
        mpd = self.connect()
        try:
            if action == "save":
                listening.write_json(path, listening.snapshot(mpd))
            elif action == "resume":
                listening.restore(mpd, json.loads(path.read_text(encoding="utf-8")))
            elif action == "mix":
                minutes = int(args.get("minutes", "120"))
                decade = int(args.get("decade") or 0)
                if not 1 <= minutes <= 1440 or decade < 0 or decade % 10:
                    raise ValueError("Enter 1–1440 minutes and a decade such as 1980")
                locks = {int(value) for value in args.get("locks", "").split()}
                locked = []
                if locks:
                    old = json.loads(path.read_text(encoding="utf-8"))["albums"]
                    if min(locks) < 1 or max(locks) > len(old):
                        raise ValueError("Lock numbers must refer to the current preview")
                    locked = [album for index, album in enumerate(old, 1) if index in locks]
                deep = args.get("mode") == "deep"
                candidates = listening.albums(mpd, args.get("genre", "").strip(), decade, deep)
                result = listening.mix(candidates, minutes * 60, locked, deep)
                if not result:
                    raise ValueError("No albums fit the time budget and filters")
                listening.write_json(path, {"albums": result}, replace=True)
                return {"albums": [
                    f"{index}. {album['artist']} — {album['album']} ({album['seconds']/60:.1f} min)"
                    for index, album in enumerate(result, 1)
                ], "minutes": round(sum(album["seconds"] for album in result) / 60, 1)}
            elif action == "replace":
                preview = json.loads(path.read_text(encoding="utf-8"))
                uris = [uri for album in preview["albums"] for uri in album["uris"]]
                if not uris:
                    raise ValueError("The album mix is empty")
                listening.replace_queue(mpd, uris)
                mpd.call("play", 0)
            elif action == "queue":
                preview = json.loads(path.read_text(encoding="utf-8"))
                added = []
                try:
                    for album in preview["albums"]:
                        for uri in album["uris"]:
                            added.append(dict(mpd.call("addid", uri))["Id"])
                except (OSError, ValueError, KeyError):
                    for identifier in reversed(added):
                        mpd.call("deleteid", identifier)
                    raise
            else:
                raise ValueError("Unknown listening action")
            return {"message": {"save": "Session saved", "resume": "Session restored",
                                "queue": "Album mix appended to the queue",
                                "replace": "Queue replaced and album mix started"}[action]}
        finally:
            mpd.close()

    def connect(self):
        """Open a connection to the configured MPD partition."""
        return listening.MPD(self.server.host, self.server.port_mpd, self.server.partition)

    def run_handoff(self, path, uri):
        """Wait for a song boundary outside the HTTP request."""
        mpd = self.connect()
        try:
            listening.handoff(mpd, path, uri)
            return "Radio handoff complete"
        finally:
            mpd.close()


def main():
    """Start the loopback bridge."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--state", type=Path, required=True)
    parser.add_argument("--mpd-host", default="127.0.0.1")
    parser.add_argument("--mpd-port", type=int, default=6600)
    parser.add_argument("--partition", default="default")
    parser.add_argument("--port", type=int, default=18770)
    args = parser.parse_args()
    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.state = args.state
        server.host = args.mpd_host
        server.port_mpd = args.mpd_port
        server.partition = args.partition
        server.worker = ThreadPoolExecutor(max_workers=1)
        server.handoff = None
        server.serve_forever()


if __name__ == "__main__":
    main()
