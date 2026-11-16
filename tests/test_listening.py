"""Offline tests for the Last.fm history cache and never-scrobbled mixes.

Last.fm and MPD are faked; nothing here touches the network or a real MPD.
"""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge  # noqa: E402
import listening  # noqa: E402
import scrobbles  # noqa: E402


def track(uts, artist, title, album=""):
    """One user.getRecentTracks entry."""
    return {"artist": {"#text": artist}, "name": title, "album": {"#text": album},
            "date": {"uts": str(uts)}}


class FakeResponse(io.BytesIO):
    """A urlopen() response holding a JSON document."""

    def __init__(self, data):
        super().__init__(json.dumps(data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeLastfm:
    """Serves pages of recent tracks and records each request's parameters."""

    def __init__(self, pages, fail_on_page=None):
        self.pages = pages
        self.fail_on_page = fail_on_page
        self.requests = []

    def __call__(self, request, timeout):
        params = {k: v[0] for k, v in parse_qs(urlsplit(request.full_url).query).items()}
        self.requests.append(params)
        page = int(params["page"])
        if page == self.fail_on_page:
            raise OSError("connection reset")
        tracks = self.pages[page - 1] if self.pages else []
        return FakeResponse({"recenttracks": {
            "track": tracks, "@attr": {"totalPages": str(max(1, len(self.pages)))}}})


class FakeMPD:
    """Answers listallinfo and playCount sticker queries from a song list."""

    def __init__(self, songs):
        self.songs = songs

    def call(self, command, *args):
        if command == "listallinfo":
            fields = []
            for song in self.songs:
                fields += list(song.items())
            return fields
        if command == "sticker":
            return []
        raise AssertionError(f"unexpected MPD command {command}")

    def close(self):
        pass


def song(path, artist, album, title, number, seconds=180, genre="Rock", date="1985"):
    """One MPD song record with 'file' first, as MPD sends it."""
    return {"file": path, "Artist": artist, "AlbumArtist": artist, "Album": album,
            "Title": title, "Track": str(number), "duration": str(seconds),
            "Genre": genre, "Date": date}


LIBRARY = [
    song("a/1.mp3", "The Cure", "Disintegration", "Pictures of You (Remastered)", 1),
    song("a/2.mp3", "The Cure", "Disintegration", "Lovesong", 2),
    song("b/1.mp3", "Billy Idol", "Don't Stop", "Dancing with Myself", 1),
    song("b/2.mp3", "Billy Idol", "Don't Stop", "Untouched Song", 2),
    song("c/1.mp3", "Unheard Band", "Fresh Album", "New One", 1),
    song("c/2.mp3", "Unheard Band", "Fresh Album", "New Two", 2, genre="Jazz"),
]


class MatchingTests(unittest.TestCase):
    """Library names match scrobbles despite version notes and spelling."""

    def test_title_version_notes_and_featuring_are_ignored(self):
        self.assertEqual(scrobbles.normalize_title("Pictures of You (Remastered 2010)"),
                         "pictures of you")
        self.assertEqual(scrobbles.normalize_title("Song - Live at Leeds"), "song")
        self.assertEqual(scrobbles.normalize_title("Smooth (feat. Rob Thomas)"), "smooth")
        self.assertEqual(scrobbles.normalize_title("Smooth ft. Rob Thomas"), "smooth")
        self.assertEqual(scrobbles.normalize_title("Café del Mar"), "cafe del mar")

    def test_ordinary_words_stay(self):
        self.assertEqual(scrobbles.normalize_title("Dancing with Myself"),
                         "dancing with myself")
        self.assertEqual(scrobbles.normalize_title("(I Can't Get No) Satisfaction"),
                         "i can t get no satisfaction")
        self.assertEqual(scrobbles.normalize_title("Bad Blood - Taylor's Song"),
                         "bad blood taylor s song")

    def test_artist_forms(self):
        keys = scrobbles.artist_keys("Johnny Cash & June Carter")
        self.assertIn("johnny cash and june carter", keys)
        self.assertIn("johnny cash", keys)
        self.assertIn("cure", scrobbles.artist_keys("The Cure"))

    def test_is_played(self):
        connection = scrobbles.connect(Path(self.tmp) / "h.db")
        connection.execute("INSERT INTO scrobbles VALUES (1, 'Cure', "
                           "'Pictures Of You', '')")
        keys = scrobbles.played_keys(connection)
        connection.close()
        self.assertTrue(scrobbles.is_played("The Cure", "Pictures of You (Remastered)", keys))
        self.assertFalse(scrobbles.is_played("The Cure", "Lovesong", keys))
        self.assertFalse(scrobbles.is_played("Someone Else", "Pictures of You", keys))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = directory.name


class SyncTests(unittest.TestCase):
    """Downloading and updating the saved history."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "scrobbles.db"
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, connection, fake, user="someone"):
        return scrobbles.sync(connection, "key", user, opener=fake,
                              sleep=self.sleeps.append)

    def test_full_then_incremental_sync(self):
        now_playing = track(0, "X", "Now")
        now_playing["@attr"] = {"nowplaying": "true"}
        del now_playing["date"]
        fake = FakeLastfm([[now_playing, track(300, "A", "Three"), track(200, "A", "Two")],
                           [track(100, "A", "One")]])
        connection = scrobbles.connect(self.path)
        self.assertEqual(self.sync(connection, fake), 3)
        self.assertNotIn("from", fake.requests[0])
        self.assertEqual(scrobbles.meta(connection, "last_uts"), "300")
        self.assertTrue(scrobbles.has_history(connection))
        self.assertEqual(self.sleeps, [scrobbles.PAUSE])

        later = FakeLastfm([[track(400, "A", "Four"), track(300, "A", "Three")]])
        self.assertEqual(self.sync(connection, later), 1)
        self.assertEqual(later.requests[0]["from"], "301")
        self.assertEqual(scrobbles.meta(connection, "last_uts"), "400")
        self.assertIn("4 scrobbles for someone", scrobbles.status(connection))
        connection.close()

    def test_interrupted_sync_keeps_old_position(self):
        connection = scrobbles.connect(self.path)
        fake = FakeLastfm([[track(300, "A", "Three")], [track(100, "A", "One")]],
                          fail_on_page=2)
        with self.assertRaises(scrobbles.SyncError):
            self.sync(connection, fake)
        self.assertFalse(scrobbles.has_history(connection))
        self.assertEqual(scrobbles.meta(connection, "last_uts", "0"), "0")
        # The next run carries on with page 2 of the same time window.
        retry = FakeLastfm([[track(300, "A", "Three")], [track(100, "A", "One")]])
        self.assertEqual(self.sync(connection, retry), 1)
        self.assertEqual([r["page"] for r in retry.requests], ["2"])
        self.assertEqual(retry.requests[0]["to"], fake.requests[0]["to"])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0], 2)
        self.assertEqual(scrobbles.meta(connection, "last_uts"), "300")
        self.assertEqual(scrobbles.meta(connection, "pending_page"), "")
        connection.close()

    def test_rate_limit_is_retried_with_retry_after(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "Too Many", {"Retry-After": "7"},
                                io.BytesIO(b"{}"))
            return FakeResponse({"recenttracks": {"track": [track(5, "A", "B")],
                                                  "@attr": {"totalPages": "1"}}})

        connection = scrobbles.connect(self.path)
        self.assertEqual(self.sync(connection, opener), 1)
        self.assertGreaterEqual(self.sleeps[0], 7)
        connection.close()

    def test_bad_key_stops_at_once(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {},
                            io.BytesIO(b'{"error": 10, "message": "Invalid API key"}'))

        connection = scrobbles.connect(self.path)
        with self.assertRaisesRegex(scrobbles.SyncError, "Invalid API key"):
            self.sync(connection, opener)
        self.assertEqual(self.sleeps, [])
        connection.close()

    def test_other_users_history_is_refused(self):
        connection = scrobbles.connect(self.path)
        self.sync(connection, FakeLastfm([[track(1, "A", "B")]]))
        with self.assertRaisesRegex(scrobbles.SyncError, "belongs to someone"):
            self.sync(connection, FakeLastfm([]), user="another")
        connection.close()

    def test_missing_credentials(self):
        connection = scrobbles.connect(self.path)
        with self.assertRaisesRegex(scrobbles.SyncError, "LASTFM_API_KEY"):
            scrobbles.sync(connection, "", "")
        connection.close()


class MixTests(unittest.TestCase):
    """Never-scrobbled albums and tracks in the mix builder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = Path(self.tmp.name) / "scrobbles.db"
        connection = scrobbles.connect(self.history)
        scrobbles.sync(connection, "key", "someone", sleep=lambda _: None,
                       opener=FakeLastfm([[track(2, "Cure", "Pictures of You"),
                                           track(1, "Billy Idol", "Dancing With Myself")]]))
        connection.close()
        self.mpd = FakeMPD(LIBRARY)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unscrobbled_albums(self):
        found = listening.candidates(self.mpd, "", 0, False, "albums", self.history,
                                     update=False)
        self.assertEqual([item["album"] for item in found], ["Fresh Album"])
        everything = listening.candidates(self.mpd, "", 0, False, "", self.history)
        self.assertEqual(len(everything), 3)

    def test_unscrobbled_tracks_with_genre(self):
        found = listening.candidates(self.mpd, "", 0, False, "tracks", self.history,
                                     update=False)
        self.assertEqual(sorted(item["title"] for item in found),
                         ["Lovesong", "New One", "New Two", "Untouched Song"])
        jazz = listening.candidates(self.mpd, "jazz", 0, False, "tracks", self.history,
                                    update=False)
        self.assertEqual([item["title"] for item in jazz], ["New Two"])
        result = listening.mix(found, 7 * 60, [], False)
        self.assertEqual(len(result), 2)
        self.assertIn("— ", listening.describe(result[0]))
        self.assertTrue(listening.describe(result[0]).endswith("(3.0 min)"))

    def test_no_history_yet(self):
        with self.assertRaisesRegex(ValueError, "No Last.fm history"):
            listening.candidates(self.mpd, "", 0, False, "tracks",
                                 Path(self.tmp.name) / "empty.db")

    def test_failed_update_uses_saved_history(self):
        messages = []
        with mock.patch.dict(os.environ, {"LASTFM_API_KEY": "k", "LASTFM_USER": "someone"}), \
                mock.patch.object(scrobbles, "fetch_page",
                                  side_effect=scrobbles.SyncError("offline")):
            found = listening.candidates(self.mpd, "", 0, False, "albums", self.history,
                                         log=messages.append)
        self.assertEqual(len(found), 1)
        self.assertIn("offline", messages[0])


class BridgeTests(unittest.TestCase):
    """The myMPD bridge builds never-scrobbled mixes and reports history status."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        connection = scrobbles.connect(self.state / "scrobbles.db")
        scrobbles.sync(connection, "key", "someone", sleep=lambda _: None,
                       opener=FakeLastfm([[track(2, "Cure", "Pictures of You")]]))
        connection.close()
        self.server = HTTPServer(("127.0.0.1", 0), bridge.Handler)
        self.server.state = self.state
        self.server.worker = ThreadPoolExecutor(max_workers=1)
        self.server.sync_worker = ThreadPoolExecutor(max_workers=1)
        self.server.handoff = None
        self.server.sync_job = None
        self.server.sync_progress = ""
        patcher = mock.patch.object(bridge.Handler, "connect",
                                    lambda handler: FakeMPD(LIBRARY))
        patcher.start()
        self.addCleanup(patcher.stop)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, payload):
        request = Request(f"http://127.0.0.1:{self.server.server_port}/",
                          json.dumps(payload).encode(),
                          {"Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_track_mix_and_status(self):
        with mock.patch.dict(os.environ, {"LASTFM_API_KEY": "", "LASTFM_USER": ""}):
            reply = self.post({"action": "mix", "name": "fresh", "minutes": "30",
                               "unscrobbled": "tracks"})
        self.assertNotIn("Pictures of You", " ".join(reply["albums"]))
        self.assertIn("Lovesong", " ".join(reply["albums"]))
        saved = json.loads((self.state / "mixes" / "fresh.json").read_text())
        self.assertTrue(all(item["kind"] == "track" for item in saved["albums"]))
        status = self.post({"action": "scrobble-status"})["message"]
        self.assertIn("1 scrobbles for someone", status)

    def test_sync_needs_credentials(self):
        with mock.patch.dict(os.environ, {"LASTFM_API_KEY": "", "LASTFM_USER": ""}):
            reply = self.post({"action": "scrobble-sync"})
        self.assertIn("LASTFM_API_KEY", reply["message"])


if __name__ == "__main__":
    unittest.main()
