#!/usr/bin/env python3
"""Hard gate: refuse to proceed if Jianying (VideoFusion-macOS) is running.

Run this immediately before ANY destructive draft operation - deleting a
draft folder, build+publish that will replace a live draft, or the manual
root_meta_info.json stale-entry prune. Exits nonzero with no output changed
if the app is open, so it's safe to chain with `&&`.

This exists because of a real incident: a rebuild's `rm -rf` on a draft
folder ran while the user had Jianying open with manual UI additions (a 4K
watermark, template-generated content) layered on top of the last publish.
Deleting the folder while the app had it open also destroyed Jianying's own
`.backup/` snapshots for that draft, making the manual additions
unrecoverable. Checking process state right before a `pgrep` earlier in the
same turn is not enough - the user can open the app in the gap. Re-run this
check immediately adjacent to the destructive command itself.
"""
import subprocess
import sys

PROCESS_PATTERN = "VideoFusion-macOS/Contents/MacOS/VideoFusion-macOS"


def is_running():
    result = subprocess.run(["pgrep", "-f", PROCESS_PATTERN], capture_output=True, text=True)
    return result.returncode == 0


def main():
    if is_running():
        print(
            "REFUSING: Jianying (VideoFusion-macOS) is currently running. "
            "Do not delete or rebuild any draft while it's open - you may destroy "
            "manual edits the user made in the UI (and Jianying's own .backup/ "
            "snapshots get deleted along with the folder). Ask the user to fully "
            "quit Jianying (Cmd+Q) and confirm before retrying.",
            file=sys.stderr,
        )
        sys.exit(1)
    print('{"status": "closed", "safe_to_proceed": true}')


if __name__ == "__main__":
    main()
