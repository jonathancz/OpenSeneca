# Crew cockpit feeder

Seneca's Crew page shows what the coding crew is doing: Queued, In progress,
Ready for review, Waiting on you and Finished today, with work untouched for
over a day folded into a collapsed Stalled group. A card opens its agent as a
tab: the agent's trimmed conversation, its progress, and the files its branch
changed, with a Live toggle that mirrors the agent's terminal, view only. Two
agent tabs can sit side by side (Split). Seneca's chat sits docked on the
right. The data comes from
firstmate's own records, read on the host by
[`src/openjarvis/cockpit/feed.py`](../../src/openjarvis/cockpit/feed.py) (Python
standard library only).

## What it reads and writes

* Reads, never writes: `~/firstmate/state/<id>.meta`, `.status` and
  `.busy-state`, `~/firstmate/data/backlog.md`, the captain's intent from
  `data/<id>/brief.md`, and whether `data/<id>/report.md` exists. When
  `bin/fm-fleet-snapshot.sh --json` succeeds it is preferred for each task's
  state and open decisions; the feeder runs it at most every ten minutes, with
  its optional cache writes pointed at a temporary folder. Today that script
  fails on large backlogs, so the direct records are the normal path.
* Reads each agent's Claude Code session under
  `~/.claude/projects/<worktree slug>/` and keeps only messages, tool names and
  a one line summary of each tool call (a command's description, a file's
  name). Tool output and file contents never leave the transcript. A session
  belongs to a task only when it started after the task and touches that
  task's own firstmate state files. `--claude-projects none` leaves
  conversations out.
* Runs `git status` and `git diff --numstat` against main in each ship task's
  worktree, with optional locks off so not even the index is refreshed, and
  only while the worktree is still on the task's branch. `--no-git` skips it.
* Treats a task's PR as merged only from firstmate's own records: the merge
  poll's `state/<id>.pr-poll-merge-notified`, a `merged=` line in
  `state/<id>.meta`, or a status line mentioning `merge-landed`
  (`state/<id>.merge-authority` is permission to merge, not a merge, and is
  ignored). It never asks GitHub. A task whose PR merged counts as finished
  and leaves the board seven days later.
* Writes `fleet.json`, `transcripts/<id>.json` and `changes/<id>.json`
  (atomically, only when they changed) in `OPENSENECA_COCKPIT_DIR`, default
  `~/.local/share/openseneca/cockpit`. It refuses an output folder inside the
  firstmate home.
* Client projects (belit, VIEW, alpine by default; `--client` to change) leave
  the host as their project name and state only, with an opaque id: no title,
  status text, branch, PR, transcript or changes. The salt behind those ids
  lives in `~/.local/state/openseneca/cockpit.salt` (`--salt-file`), outside
  the mounted folder.

It watches with a cheap `stat` scan every 5 s, debounces changes for 2 s, and
rewrites the fleet at least once a minute, all under `nice -n 19`.

## Live terminal mirror

The feeder decides on its own which panes to mirror; Seneca sends it nothing
and its container only reads the cockpit folder. It captures live
own-project agents (working, or not closed, not done and active in the last
10 minutes), at most 5 of them, most recent activity first, about every 2 s,
with `tmux capture-pane -p -e` (printed to stdout, never into a tmux buffer),
and writes `terminals/<id>.json`, secrets redacted. Nothing else ever reaches
tmux: no `send-keys`, no attach, no resize. Client projects are never
mirrored. `--no-terminals` turns the mirror off.

## Run it

```bash
scripts/cockpit-feed/run.sh --once      # write once, check the output
scripts/cockpit-feed/run.sh             # keep it fresh
```

To keep it running, install the user unit in
[`cockpit-feed.service`](cockpit-feed.service) (instructions at its top). Where
user units are not available (WSL without a user session), start it detached:

```bash
setsid nohup scripts/cockpit-feed/run.sh \
  >>~/.local/state/openseneca/cockpit-feed.log 2>&1 </dev/null &
```

Then start Seneca with the cockpit folder mounted read only:

```bash
docker compose -f deploy/docker/docker-compose.yml \
  -f deploy/docker/docker-compose.cockpit.yml up -d
```

Without Docker, `jarvis serve` reads `OPENJARVIS_COCKPIT_DIR`, or
`<data dir>/cockpit` when it is unset.

## What Seneca serves

All cockpit routes are GETs, plus one push only WebSocket; decisions and
merges stay in Claude Code.

* `GET /v1/cockpit/fleet`, `GET /v1/cockpit/agents/{id}/transcript`,
  `GET /v1/cockpit/agents/{id}/changes`, `GET /v1/cockpit/agents/{id}/terminal`
  (the latest mirror capture, read from `terminals/<id>.json`),
  and `GET /v1/cockpit/nudges` (the nudge rules and whether the Discord
  channel is ready).
* `/v1/cockpit/live` tells the browser when `fleet.json` changed.

## Discord nudges

When the Discord channel is set up (its `captain` alias, the owner's DM),
Seneca sends one line messages from `fleet.json`:

* at once when an agent opens a decision or reports a block;
* a digest at 09:00 and 17:00: PRs ready for review, reports landed, agents
  quiet for a day, and how many decisions still wait;
* quiet hours 23:00 to 08:00 hold everything except blocks.

Each message names the agent, its project and what it needs, never code, file
contents or transcript text. On first start Seneca only notes what already
waits, so it does not send a burst. Times follow the fleet's time zone
(`OPENJARVIS_COCKPIT_TZ` overrides it). Set `OPENJARVIS_COCKPIT_NUDGES=0` to
turn nudges off.
