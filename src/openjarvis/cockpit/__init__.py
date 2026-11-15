"""Seneca's crew cockpit: a read only view of firstmate's coding crew.

* :mod:`openjarvis.cockpit.feed` runs on the host (stdlib only). It reads
  firstmate's records, Claude Code transcripts and task worktrees, and writes a
  small folder of JSON files that the Seneca container mounts read only.
* :mod:`openjarvis.cockpit.nudges` decides which one line Discord messages to
  send the captain from that folder (decisions and blocks at once, a digest at
  09:00 and 17:00, quiet hours 23:00 to 08:00).

Nothing here can write to firstmate, tmux, git or GitHub.
"""
