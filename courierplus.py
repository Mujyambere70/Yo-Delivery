"""
courierplus.py - version 7 (mixin class used by Store): more for couriers and parcels.

    signature        proof of delivery: the receiver signs on the courier's screen; the drawing is checked (a real PNG,
                     at most 200 KB, at least a few strokes) and kept as a private picture, once per parcel
    advance data     parcels abroad: the customs data of the CN 23 (sender, receiver, every item with its HS code, value,
                     weight) as JSON for the post or the partner of the other country (electronic advance data). The UPU's
                     own ITMATT messages need membership of the UPU network; this export carries the same data elements.
    turn by turn     the courier's route page asks the routing server for the road steps (OSRM "steps") and shows them
                     as simple instructions; offline it shows the last saved list and the drawing of the stops
    Android app      /.well-known/assetlinks.json lets the courier web app be packed as an Android app (Trusted Web
                     Activity, for example with Bubblewrap) when ANDROID_APP_ID and ANDROID_APP_SHA256 are set
"""

import base64
import io
import json

from PIL import Image

from core import ShopError, now
import courier as C

MAX_SIGNATURE = 200 * 1024


def signature_png(data_url):
    """A data:image/png;base64,... drawing -> clean PNG bytes (black on white), or ShopError."""
    text = str(data_url or "")
    if not text.startswith("data:image/png;base64,"):
        raise ShopError("Please ask the receiver to sign.")
    try:
        raw = base64.b64decode(text.split(",", 1)[1], validate=True)
    except (ValueError, IndexError):
        raise ShopError("The signature could not be read. Please try again.")
    if len(raw) > MAX_SIGNATURE or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ShopError("The signature could not be read. Please try again.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:                                   # not a picture at all
        raise ShopError("The signature could not be read. Please try again.")
    if img.width > 1200 or img.height > 800:
        raise ShopError("The signature could not be read. Please try again.")
    gray = Image.new("RGBA", img.size, (255, 255, 255, 255))
    gray.alpha_composite(img.convert("RGBA"))
    g = gray.convert("L")
    ink = sum(g.histogram()[:128])                      # pixels darker than middle grey
    if ink < 150:                                       # an empty box or one dot
        raise ShopError("Please ask the receiver to sign (the box is empty).")
    out = io.BytesIO()
    g.point(lambda v: 0 if v < 128 else 255).convert("1").save(out, "PNG", optimize=True)
    return out.getvalue()


class CourierPlusMixin:

    def save_pod_signature(self, courier_id, number, data_url):
        png = signature_png(data_url)
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (str(number).upper(),)).fetchone()
            if s is None or s["courier_id"] != courier_id or s["status"] != C.DELIVERED:
                raise ShopError("You can add the signature only to a parcel you delivered.")
            name = f"private/sign/{s['number']}.png"
            if not conn.execute("UPDATE shipments SET pod_signature = ? WHERE id = ? AND pod_signature = ''",
                                (name, s["id"])).rowcount:
                raise ShopError("This parcel has a signature already.")
        self.save_media(name, "image/png", png)
        return name

    def advance_customs_data(self, number):
        """The electronic advance data of a parcel abroad (dict), or None."""
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (str(number).upper(),)).fetchone()
        if s is None:
            return None
        d = self.customs_of(s["id"])
        if d is None:
            return None
        return {
            "format": "yo-ead/1.0 (CN 23 data elements)",
            "parcel": s["number"], "created_at": s["created_at"], "service": s["service"],
            "gross_weight_kg": round(s["weight_g"] / 1000, 3),
            "sender": {"name": s["from_name"], "city": s["from_city"], "country": s["from_country"] or "IN"},
            "receiver": {"name": s["to_name"], "city": s["to_city"], "country": s["to_country"],
                         "postcode": s["to_postcode"]},
            "category": d["reason"],
            "items": [{"description": x["desc"], "quantity": x["qty"], "value": round(x["value"] / 100, 2),
                       "currency": "INR", "hs_code": x.get("hs", ""), "origin_country": s["from_country"] or "IN"}
                      for x in d["items"]],
            "total_value": round(d["total_value"] / 100, 2), "currency": "INR",
            "estimated_duty": round(d["duty_estimate"] / 100, 2), "duty_rule": d["rule_country"],
            "exported_at": now(),
        }


def assetlinks(env):
    """The Digital Asset Links file of the courier app (empty list when the app is not set up)."""
    app_id, sha = env.get("ANDROID_APP_ID", ""), env.get("ANDROID_APP_SHA256", "")
    if not app_id or not sha:
        return []
    return [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": app_id,
                        "sha256_cert_fingerprints": [x.strip() for x in sha.split(",") if x.strip()]}}]


def ead_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2)
