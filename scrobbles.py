#!/usr/bin/env python3
"""Keep a local copy of a Last.fm scrobble history and match library tracks to it.

The history lives in a small SQLite file (one row per scrobble). The first
sync downloads every page of user.getRecentTracks; later syncs ask only for
scrobbles newer than the newest one saved. The saved position moves forward
only after a sync has read every page, so an interrupted sync never leaves
a gap. Each sync asks for a fixed time window, so its pages stay put; an
interrupted sync records the last page it finished and the next run carries
on from there (duplicate rows are ignored either way).

Inputs:  LASTFM_API_KEY and LASTFM_USER environment variables (or explicit
         arguments), and the path of the history database.
Outputs: the database file; played_keys() returns normalized (artist, title)
         pairs for matching library tracks with is_played().
"""
import json
import random
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = ("mpd-listening-tools/1.0 "
              "(+https://github.com/bonelifer/mpd-listening-tools)")
PAGE_SIZE = 200          # the most user.getRecentTracks returns per page
PAUSE = 0.25             # seconds between requests (Last.fm allows ~5/s)
TIMEOUT = 20
ATTEMPTS = 4
# Last.fm error codes worth retrying: 8 operation failed, 11 service offline,
# 16 temporary error, 29 rate limit exceeded.
RETRY_CODES = {8, 11, 16, 29}

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrobbles (
    uts INTEGER NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    album TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (uts, artist, title)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Words that mark a bracketed or dash-separated part of a title as a
# version note ("(Remastered 2011)", "- Live at Leeds") rather than a name.
VERSION_WORDS = re.compile(
    r"\b(remaster(ed)?|live|mono|stereo|version|edit|mix|remix|demo|single|"
    r"bonus|deluxe|acoustic|instrumental|take|session|anniversary|expanded|"
    r"radio|explicit|clean|bbc|outtake|alternate|unplugged)\b")
# "(feat. X)", "[with X]" anywhere, or a trailing "feat. X" / "ft. X". A bare
# "with" is left alone so titles like "Dancing with Myself" stay whole.
FEATURING = re.compile(
    r"\s*(?:[(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^)\]]*[)\]]"
    r"|\b(?:feat\.|ft\.|featuring)\s+.*$)")
ARTIST_SPLIT = re.compile(r"\s*(?:,|;|/|&|\+|\band\b|\bx\b|\bvs\.?)\s*")


class SyncError(Exception):
    """A sync could not finish; the saved history is unchanged."""


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the history database at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection


def meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    """Read one value from the meta table."""
    row = connection.execute("SELECT value FROM meta WHERE key = ?",
                             (key,)).fetchone()
    return row[0] if row else default


def set_meta(connection: sqlite3.Connection, key: str, value) -> None:
    """Store one value in the meta table (caller commits)."""
    connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                       (key, str(value)))


def plain(text: str) -> str:
    """Lower-case text without accents, punctuation or repeated spaces."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def normalize_title(title: str) -> str:
    """Title without featured artists and version notes, for matching."""
    title = FEATURING.sub("", title.casefold())
    title = re.sub(r"[(\[][^)\]]*[)\]]",
                   lambda m: "" if VERSION_WORDS.search(m.group(0)) else m.group(0),
                   title)
    head, dash, tail = title.partition(" - ")
    if dash and VERSION_WORDS.search(tail):
        title = head
    return plain(title)


def artist_keys(artist: str) -> set:
    """Normalized forms of an artist: the whole name and its first artist."""
    artist = FEATURING.sub("", artist.casefold())
    keys = {plain(artist)}
    first = ARTIST_SPLIT.split(artist, maxsplit=1)[0]
    keys.add(plain(first))
    keys |= {key[4:] for key in keys if key.startswith("the ")}
    keys.discard("")
    return keys


def played_keys(connection: sqlite3.Connection) -> set:
    """All normalized (artist, title) pairs in the saved history."""
    keys = set()
    for artist, title in connection.execute(
            "SELECT DISTINCT artist, title FROM scrobbles"):
        name = normalize_title(title)
        if name:
            keys |= {(key, name) for key in artist_keys(artist)}
    return keys


def is_played(artist: str, title: str, keys: set) -> bool:
    """Whether a library track matches a scrobble in played_keys()."""
    name = normalize_title(title)
    return bool(name) and any((key, name) in keys for key in artist_keys(artist))


def has_history(connection: sqlite3.Connection) -> bool:
    """Whether at least one complete sync has finished."""
    return bool(meta(connection, "synced_at"))


def status(connection: sqlite3.Connection) -> str:
    """One line describing the saved history."""
    synced = meta(connection, "synced_at")
    if not synced:
        return "No Last.fm history downloaded yet"
    count = connection.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    moment = datetime.fromisoformat(synced)
    hour = moment.hour % 12 or 12
    when = (f"{moment:%m/%d/%Y} {hour}:{moment:%M} "
            f"{'AM' if moment.hour < 12 else 'PM'} UTC")
    return (f"{count} scrobbles for {meta(connection, 'user')}, "
            f"last synced {when}")


def fetch_page(api_key: str, user: str, page: int, since: int, until: int,
               opener=urlopen, sleep=time.sleep) -> dict:
    """Fetch one page of recent tracks, retrying temporary failures."""
    params = {"method": "user.getrecenttracks", "user": user,
              "api_key": api_key, "format": "json", "limit": PAGE_SIZE,
              "page": page, "to": until}
    if since:
        params["from"] = since + 1
    request = Request(API_URL + "?" + urlencode(params),
                      headers={"User-Agent": USER_AGENT})
    for attempt in range(1, ATTEMPTS + 1):
        wait = 2 ** attempt + random.random()
        try:
            with opener(request, timeout=TIMEOUT) as response:
                reply = json.load(response)
        except HTTPError as error:
            body = {}
            try:
                body = json.load(error)
            except ValueError:
                pass
            code = body.get("error") if isinstance(body, dict) else None
            if error.code == 429 or error.code >= 500 or code in RETRY_CODES:
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                if retry_after.isdigit():
                    wait = int(retry_after) + random.random()
            else:
                message = body.get("message") if isinstance(body, dict) else ""
                raise SyncError(f"Last.fm refused the request: {message or error}") from error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            if attempt == ATTEMPTS:
                raise SyncError(f"Last.fm could not be reached: {error}") from error
        else:
            if "error" not in reply:
                return reply
            if reply["error"] not in RETRY_CODES:
                raise SyncError(f"Last.fm error {reply['error']}: {reply.get('message', '')}")
        if attempt == ATTEMPTS:
            raise SyncError("Last.fm kept failing; try again later")
        sleep(wait)
    raise SyncError("Last.fm kept failing; try again later")


def sync(connection: sqlite3.Connection, api_key: str, user: str,
         progress=None, opener=urlopen, sleep=time.sleep) -> int:
    """Download scrobbles newer than the saved ones; return how many were new.

    progress, if given, is called with (page, total_pages) after each page.
    """
    if not api_key or not user:
        raise SyncError("Set LASTFM_API_KEY and LASTFM_USER")
    if meta(connection, "user") not in ("", user):
        raise SyncError(f"This history belongs to {meta(connection, 'user')}; "
                        "use a different history file for another user")
    since = int(meta(connection, "last_uts", "0") or 0)
    until, page = int(time.time()), 1
    # Carry on with an interrupted sync of the same window.
    if meta(connection, "pending_since") == str(since) and meta(connection, "pending_until"):
        until = int(meta(connection, "pending_until"))
        page = int(meta(connection, "pending_page", "0")) + 1
        since_rows = connection.execute(
            "SELECT MAX(uts) FROM scrobbles WHERE uts <= ?", (until,)).fetchone()[0]
        newest = max(since, since_rows or 0)
    else:
        newest = since
        set_meta(connection, "pending_since", since)
        set_meta(connection, "pending_until", until)
        set_meta(connection, "pending_page", 0)
        set_meta(connection, "user", user)
        connection.commit()
    added, pages = 0, page
    while page <= pages:
        reply = fetch_page(api_key, user, page, since, until, opener, sleep)
        block = reply.get("recenttracks", {})
        tracks = block.get("track", [])
        if isinstance(tracks, dict):
            tracks = [tracks]
        try:
            pages = int(block.get("@attr", {}).get("totalPages", 1) or 1)
        except ValueError:
            pages = 1
        rows = []
        for track in tracks:
            if track.get("@attr", {}).get("nowplaying") == "true":
                continue
            uts = str(track.get("date", {}).get("uts", ""))
            artist = track.get("artist", {}).get("#text", "")
            title = track.get("name", "")
            if not uts.isdigit() or not artist or not title:
                continue
            rows.append((int(uts), artist, title,
                         track.get("album", {}).get("#text", "")))
            newest = max(newest, int(uts))
        before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO scrobbles (uts, artist, title, album) "
            "VALUES (?, ?, ?, ?)", rows)
        added += connection.total_changes - before
        set_meta(connection, "pending_page", page)
        connection.commit()
        if progress:
            progress(page, pages)
        page += 1
        if page <= pages:
            sleep(PAUSE)
    connection.execute("DELETE FROM meta WHERE key LIKE 'pending_%'")
    set_meta(connection, "user", user)
    set_meta(connection, "last_uts", newest)
    set_meta(connection, "synced_at",
             datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    connection.commit()
    return added
