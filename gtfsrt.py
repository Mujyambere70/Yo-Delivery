"""
gtfsrt.py - version 7 (mixin class used by Store): live delays from GTFS-Realtime.

A transport company that publishes its timetable in GTFS (version 6) often also publishes a GTFS-Realtime feed: a
small Protocol Buffers file with "trip updates" (this trip is 12 minutes late at this stop, this trip is cancelled).
Version 7 reads such a feed (the official bindings, gtfs-realtime-bindings, Apache 2.0):

    * the feed URL is saved on the GTFS feed (gtfs_feeds.rt_url); a job reads it every 2 minutes in production
      (in the demo and the tests the file is given directly: tools/make_gtfs_rt.py makes a sample)
    * for every trip update: the trip id and the start date find our trips (every pair of stops of a GTFS trip is a
      service with code GT<feed>-<trip id>-<i>-<j>); the delay of the first stop that has one becomes the delay of
      the trip; travellers get a message when the delay changes by 5 minutes or more
    * a CANCELED trip is cancelled with full refunds, as when the operator cancels it by hand
"""

from datetime import datetime

from google.transit import gtfs_realtime_pb2

from core import ShopError, now

NOTIFY_STEP_MIN = 5


def read_feed(blob):
    """GTFS-Realtime bytes -> [{"trip_id", "start_date" (YYYY-MM-DD or ''), "delay_min", "cancelled"}]."""
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(blob)
    except Exception:                                     # protobuf DecodeError and friends
        raise ShopError("This is not a GTFS-Realtime file.")
    if not feed.header.gtfs_realtime_version:
        raise ShopError("This is not a GTFS-Realtime file.")
    out = []
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        trip_id = tu.trip.trip_id
        if not trip_id:
            continue
        d = tu.trip.start_date
        day = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""
        cancelled = tu.trip.schedule_relationship == gtfs_realtime_pb2.TripDescriptor.CANCELED
        delay = None
        if tu.HasField("delay"):
            delay = tu.delay
        for stu in tu.stop_time_update:
            for ev in (stu.departure, stu.arrival):
                if ev.HasField("delay"):
                    delay = ev.delay
                    break
            if delay is not None:
                break
        out.append({"trip_id": trip_id, "start_date": day, "cancelled": cancelled,
                    "delay_min": max(0, round((delay or 0) / 60))})
    return out


class GtfsRtMixin:

    def apply_gtfs_rt(self, feed_id, blob):
        """Apply a realtime file to the trips of a GTFS feed. Returns counts."""
        updates = read_feed(blob)
        done = {"updates": len(updates), "trips": 0, "delayed": 0, "cancelled": 0, "messages": 0}
        prefix = f"GT{int(feed_id)}-"
        for u in updates:
            like = f"{prefix}{u['trip_id'][:24]}-%"
            with self.db() as conn:
                rows = conn.execute(
                    "SELECT t.* FROM ticket_trips t JOIN ticket_services s ON s.id = t.service_id WHERE s.code LIKE ? "
                    "AND t.status = 'Scheduled' AND (? = '' OR t.day = ?)",
                    (like, u["start_date"], u["start_date"])).fetchall()
            for t in rows:
                done["trips"] += 1
                if u["cancelled"]:
                    try:
                        done["messages"] += self.cancel_trip(t["id"], "Cancelled by the operator (live data)")
                        done["cancelled"] += 1
                    except ShopError:
                        pass
                    continue
                if abs(u["delay_min"] - (t["delay_min"] or 0)) >= NOTIFY_STEP_MIN or (u["delay_min"] == 0 and t["delay_min"]):
                    done["messages"] += self.set_trip_delay(t["id"], min(u["delay_min"], 1440), "Live data of the operator")
                    done["delayed"] += 1
        with self.db() as conn:
            conn.execute("UPDATE gtfs_feeds SET rt_checked_at = ?, rt_result = ? WHERE id = ?",
                         (now(), f"{done['updates']} updates, {done['delayed']} delays, {done['cancelled']} cancelled",
                          feed_id))
        return done

    def set_gtfs_rt_url(self, user_id, feed_id, url):
        if not self.can(user_id, "transport.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        url = str(url or "").strip()
        if url and not url.startswith("https://"):
            raise ShopError("The address of the live feed must start with https://")
        with self.db() as conn:
            conn.execute("UPDATE gtfs_feeds SET rt_url = ? WHERE id = ?", (url[:300], feed_id))


def sample_feed(trips, when=None):
    """A GTFS-Realtime file for tests and the demo: trips = [(trip_id, 'YYYYMMDD', delay seconds or None if cancelled)]."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int((when or datetime.now()).timestamp())
    for i, (trip_id, day, delay) in enumerate(trips):
        e = feed.entity.add()
        e.id = f"u{i}"
        tu = e.trip_update
        tu.trip.trip_id = trip_id
        tu.trip.start_date = day
        if delay is None:
            tu.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.CANCELED
        else:
            stu = tu.stop_time_update.add()
            stu.stop_sequence = 1
            stu.departure.delay = int(delay)
    return feed.SerializeToString()
