# Crew cockpit feeder

Seneca's Crew page shows what the coding crew is doing: Queued, In progress,
Ready for review, Waiting on you and Finished today. The data comes from
firstmate's own records, read on the host by
[`src/openjarvis/cockpit/feed.py`](../../src/openjarvis/cockpit/feed.py) (Python
standard library only).

## What it reads and writes

* Reads, never writes: `~/firstmate/state/<id>.meta`, `.status` and
  `.busy-state`, `~/firstmate/data/backlog.md` and whether
  `data/<id>/report.md` exists. When `bin/fm-fleet-snapshot.sh --json`
  succeeds it is preferred for each task's state and open decisions; the
  feeder runs it at most every ten minutes, with its optional cache writes
  pointed at a temporary folder. Today that script fails on large backlogs, so
  the direct records are the normal path.
* Writes only `fleet.json` (atomically) in `OPENSENECA_COCKPIT_DIR`, default
  `~/.local/share/openseneca/cockpit`. It refuses an output folder inside the
  firstmate home.
* Client projects (belit, VIEW, alpine by default; `--client` to change) leave
  the host as their project name and state only, with an opaque id.

It watches with a cheap `stat` scan every 5 s, debounces changes for 2 s, and
rewrites the file at least once a minute, all under `nice -n 19`.

## Run it

```bash
scripts/cockpit-feed/run.sh --once      # write once, check the output
scripts/cockpit-feed/run.sh             # keep it fresh
```

To keep it running, install the user unit in
[`cockpit-feed.service`](cockpit-feed.service) (instructions at its top).

Then start Seneca with the cockpit folder mounted read only:

```bash
docker compose -f deploy/docker/docker-compose.yml \
  -f deploy/docker/docker-compose.cockpit.yml up -d
```

Without Docker, `jarvis serve` reads `OPENJARVIS_COCKPIT_DIR`, or
`<data dir>/cockpit` when it is unset.

Seneca serves the file at `GET /v1/cockpit/fleet` and announces new versions
on the push only WebSocket `/v1/cockpit/live`. Every cockpit route is read
only; decisions and merges stay in Claude Code.
