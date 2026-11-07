"""
tools/make_gtfs_rt.py - write a small GTFS-Realtime file (trip updates) for trying the "Live delays" form of
/admin/tickets/gtfs (version 7). Nothing is sent anywhere: the file is written to the disk.

    python tools/make_gtfs_rt.py TRIP_ID YYYY-MM-DD MINUTES [TRIP_ID YYYY-MM-DD MINUTES ...] > delays.pb
    python tools/make_gtfs_rt.py T1 2026-11-10 cancel > cancel.pb      (the word "cancel" cancels the trip)

The trip ids are the ones of trips.txt of the imported GTFS file (for the demo: data/gtfs/sample_rwanda.zip).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gtfsrt  # noqa: E402


def main(args):
    if not args or len(args) % 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    trips = []
    for i in range(0, len(args), 3):
        trip_id, day, minutes = args[i:i + 3]
        trips.append((trip_id, day.replace("-", ""), None if minutes.lower() == "cancel" else int(minutes) * 60))
    sys.stdout.buffer.write(gtfsrt.sample_feed(trips))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
