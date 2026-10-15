"""Shared resource ceiling for generated and independently validated jobs."""

# Keep desktop/browser planning and local/Pi MachineService validation aligned.
# This is a bounded-work limit, not a controller buffer size or safety rating.
MAX_STREAM_COMMANDS = 1_000_000
