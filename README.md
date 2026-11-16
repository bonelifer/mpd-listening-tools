# MPD Listening Tools

A command-line companion for MPD and myMPD. It uses the current MPD queue
and library. Python 3 is required. By default it connects to MPD at
`127.0.0.1:6600`. Put connection options before the command.

```sh
python3 listening.py save evening.json
python3 listening.py resume evening.json
python3 listening.py handoff break.json https://example.org/station
python3 listening.py resume break.json
```

Use a new filename for each saved session. Resume replaces the current
queue. Handoff waits for the current finite track to finish, saves the
queue at the transition, and plays the supplied station URL. Ctrl+C
cancels the wait. Stream positions are restored from the beginning.

```sh
python3 listening.py mix mix.json --minutes 120 --genre Rock --decade 1980 --deep-shelf
python3 listening.py mix mix.json --minutes 120 --genre Rock --decade 1980 --deep-shelf --lock 1
python3 listening.py queue-mix mix.json
```

The first command previews complete albums within the time budget. Repeat
with one or more `--lock` numbers to retain albums and reroll the rest.
Deep Shelf favors albums with lower average track play counts. Tracks
without a play count are treated as unplayed. Queueing appends the preview
in disc and track order.

## Never-scrobbled music

`--unscrobbled albums` limits a mix to albums where no track has ever been
scrobbled to Last.fm; `--unscrobbled tracks` builds the mix from single
tracks you have never scrobbled. Both work with `--genre`, `--decade`,
`--deep-shelf` and `--lock`.

The history comes from a local copy of your Last.fm scrobbles in
`data/scrobbles.db`. Download it once, then each mix fetches only the
scrobbles added since:

```sh
export LASTFM_API_KEY=your-api-key LASTFM_USER=your-username
python3 listening.py scrobbles sync
python3 listening.py mix fresh.json --minutes 90 --unscrobbled tracks
python3 listening.py scrobbles status
```

The first download takes about 2.5 seconds per 200 scrobbles (an hour for
a quarter of a million). If it stops part way, run it again and it carries
on from the last page it finished. Names are matched without case,
punctuation, featured artists or version notes such as "(Remastered 2011)"
or "- Live", so a remaster counts as heard when you scrobbled the original.
Get an API key at <https://www.last.fm/api/account/create>.

## myMPD web picker

For myMPD and MPD using host networking, start the loopback bridge and
install the Lua picker:

```sh
mkdir -m 700 -p data
docker compose up -d
python3 install-picker.py --mympd http://127.0.0.1:8081
```

Open **Listening Tools** from Scripts. You can also add the bundled Home
launcher by copying `assets/listening-tools.png` into myMPD’s `pics/thumbs/` directory and submitting `home-launcher.json` to its API once. The icon is [CC0](assets/LICENSE.md).
The UI stores sessions and mix previews in `./data`. For never-scrobbled
mixes, copy `.env.example` to `.env` (mode 600), fill in your Last.fm API key
and username, and restart the bridge. Then choose **Last.fm history →
Download new scrobbles** once; choose it again to see progress. The mix
builder's last field picks any albums, never-scrobbled albums or
never-scrobbled tracks.

Check the albums you want to keep before choosing
**Reroll unlocked albums**. Choose **Append this mix** to queue the
preview. **Replace queue with this mix** asks for confirmation, then
replaces the queue and starts playback. Radio handoff offers a direct stream URL or selections from installed
Pandora, TuneIn, iHeartRadio, SomaFM, and iBroadcast pickers. The bridge
expects their standard local ports 18765–18769. For iBroadcast, select an
album and then a track or the whole album. Use **Handoff status** to
check when the transition completes, then **Resume session** to return.

For visible field help and accessible album checkbox labels, copy
`listening-accessibility.js` to `pics/`, append `listening-loader.js` to
myMPD's custom JavaScript file, and restart myMPD.

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/mpd-listening-tools/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/mpd-listening-tools/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- Code and documentation assisted by [Codex](https://openai.com/codex/).
- Never-scrobbled mixes, tests and documentation assisted by [Claude](https://www.anthropic.com/claude).

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
