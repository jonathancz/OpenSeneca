"""Seneca's crew cockpit: a read only view of firstmate's coding crew.

:mod:`openjarvis.cockpit.feed` runs on the host (stdlib only). It reads
firstmate's records and writes one JSON file, ``fleet.json``, into a folder
that the Seneca container mounts read only. Nothing here can write to
firstmate, tmux, git or GitHub.
"""
