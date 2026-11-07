"""
idcheck.py - version 6 (mixin class used by Store): ID document checks with face matching, only with consent.

A courier (or a worker) can prove who he is. He reads what is kept and why, ticks "I agree", and uploads:
    * a photo of the page of his ID document with his portrait (passport, ID card, driving licence)
    * a selfie taken now
    * optionally the two machine-readable lines (MRZ) at the bottom of a passport
The shop then checks automatically:
    * a face on the document and a face on the selfie (OpenCV, as in photocheck.py)
    * how alike the two faces are: both faces are cut out, made grey and the same size, and compared with local
      binary pattern histograms (LBPH, a classic face recognition method that needs no internet and no big model).
      The score is 0-100; it is a HINT for the person who decides, never a decision by itself
    * the check digits of the MRZ (ICAO 9303: weights 7, 3, 1) and that the document has not expired
A person with the ability "photos.review" approves or refuses. The shop keeps only: the kind of document, the
country, the expiry date, the last 3 characters of the number and the results. The two pictures are deleted
RETENTION_DAYS after the decision (or when the person withdraws his consent).
"""

import io
from datetime import date, datetime, timedelta

from core import ShopError, now

IDCHECK_SCHEMA = """
CREATE TABLE IF NOT EXISTS id_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), purpose TEXT NOT NULL,
    doc_type TEXT NOT NULL, country TEXT NOT NULL DEFAULT '', doc_last3 TEXT NOT NULL DEFAULT '',
    expiry TEXT NOT NULL DEFAULT '', mrz_ok INTEGER, faces_doc INTEGER NOT NULL DEFAULT 0,
    faces_selfie INTEGER NOT NULL DEFAULT 0, score REAL, hint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Waiting', consent_at TEXT NOT NULL, decided_by TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', doc_image TEXT NOT NULL DEFAULT '',
    selfie_image TEXT NOT NULL DEFAULT '', purge_after TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_id_check_waiting ON id_checks(user_id) WHERE status = 'Waiting';
"""

DOC_TYPES = {"passport": "Passport", "id_card": "National ID card", "licence": "Driving licence"}
RETENTION_DAYS = 30
SAME_FROM, DIFFERENT_BELOW = 70, 45          # score hints (calibrated on test pictures, see tests/test_v6.py)


# ---------------------------------------------------------------- MRZ (ICAO 9303)
def mrz_value(ch):
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - 55
    return 0                                       # "<" and anything else


def mrz_digit(text):
    return str(sum(mrz_value(c) * w for c, w in zip(text, [7, 3, 1] * 20)) % 10)


def _mrz_date(yymmdd, future):
    try:
        d = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None
    if future and d < date.today() - timedelta(days=365 * 50):
        d = d.replace(year=d.year + 100)
    if not future and d > date.today():
        d = d.replace(year=d.year - 100)
    return d


def parse_mrz(line1, line2):
    """The two lines of 44 characters of a passport (TD3). Returns the fields and which checks passed."""
    l1 = str(line1 or "").strip().upper().replace(" ", "")
    l2 = str(line2 or "").strip().upper().replace(" ", "")
    if len(l1) != 44 or len(l2) != 44 or not l1.startswith("P"):
        raise ShopError("The machine-readable lines of a passport have 2 x 44 characters (the first starts with P).")
    number, birth, expiry = l2[0:9], l2[13:19], l2[21:27]
    checks = {"number": mrz_digit(number) == l2[9], "birth": mrz_digit(birth) == l2[19],
              "expiry": mrz_digit(expiry) == l2[27], "personal": mrz_digit(l2[28:42]) == l2[42] or l2[28:43] == "<" * 15,
              "final": mrz_digit(l2[0:10] + l2[13:20] + l2[21:43]) == l2[43]}
    exp = _mrz_date(expiry, True)
    return {"country": l1[2:5].replace("<", ""), "number": number.replace("<", ""), "birth": _mrz_date(birth, False),
            "expiry": exp, "checks": checks, "valid": all(checks.values()),
            "expired": exp is not None and exp < date.today()}


# ---------------------------------------------------------------- faces
def _face_grays(img, most=3):
    """The (up to 3) biggest faces of a picture, cut out with a margin, grey, 112 x 112, equalised.
    Returns (faces, number of faces found) or (None, None) when the face model is missing."""
    from PIL import ImageOps
    import photocheck
    boxes = photocheck.faces(img)
    if boxes is None:
        return None, None
    im = ImageOps.exif_transpose(img).convert("L")
    W, H = im.size
    m = 0.2
    out = []
    for x, y, w, h in boxes[:most]:
        box = (max(0, int((x - w * m) * W)), max(0, int((y - h * m) * H)),
               min(W, int((x + w * (1 + m)) * W)), min(H, int((y + h * (1 + m)) * H)))
        out.append(ImageOps.equalize(im.crop(box).resize((112, 112))))
    return out, len(boxes)


def _uniform_table():
    table, k = [], 0
    for v in range(256):
        b = [(v >> i) & 1 for i in range(8)]
        jumps = sum(b[i] != b[(i + 1) % 8] for i in range(8))
        if jumps <= 2:
            table.append(k)
            k += 1
        else:
            table.append(58)
    return table


_UNIFORM = _uniform_table()


def lbp_histogram(face):
    """Uniform LBP (8 neighbours, radius 1) in a 6 x 6 grid: 36 histograms of 59 bins, each of length 1."""
    import numpy as np
    g = np.asarray(face, dtype=np.int16)
    c = g[1:-1, 1:-1]
    code = np.zeros_like(c, dtype=np.int32)
    for bit, (dy, dx) in enumerate([(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]):
        n = g[1 + dy:g.shape[0] - 1 + dy, 1 + dx:g.shape[1] - 1 + dx]
        code |= ((n >= c).astype(np.int32) << bit)
    u = np.array(_UNIFORM, dtype=np.int32)[code]
    cells = []
    step = u.shape[0] // 6
    for i in range(6):
        for j in range(6):
            h = np.bincount(u[i * step:(i + 1) * step, j * step:(j + 1) * step].ravel(), minlength=59).astype(np.float64)
            cells.append(h / (h.sum() or 1.0))
    return np.concatenate(cells)


def _windows(face):
    """96 x 96 windows of the 112 x 112 face: the centre and 8 small moves and zooms (a face detector never cuts two
    photos in exactly the same place)."""
    out = [face.crop((8, 8, 104, 104))]
    for x, y in ((4, 8), (12, 8), (8, 4), (8, 12), (4, 4), (12, 12)):
        out.append(face.crop((x, y, x + 96, y + 96)))
    for a, b in ((4, 108), (12, 100)):
        out.append(face.crop((a, a, b, b)).resize((96, 96)))
    return out


def face_score(doc_img, selfie_img):
    """(score 0..100 or None, faces on the document, faces on the selfie). The best pair of faces and the best of
    the small moves count (a pattern on a shirt can look like a second face to the detector)."""
    import numpy as np
    fa, na = _face_grays(doc_img)
    fb, nb = _face_grays(selfie_img)
    if not fa or not fb:
        return None, na, nb
    best = 1.0
    for a in fa:
        ha = lbp_histogram(a.crop((8, 8, 104, 104)))
        for b in fb:
            for f in _windows(b):
                hb = lbp_histogram(f)
                best = min(best, 0.5 * float(np.sum((ha - hb) ** 2 / (ha + hb + 1e-9))) / 36)
    return round(max(0.0, min(100.0, 100 * (1 - best / 0.55))), 1), na, nb


LIVE_STEPS = {"closer": "Move your face closer to the camera", "left": "Turn or move your head to your left",
              "right": "Turn or move your head to your right"}
LIVE_SHIFT = 0.06                                         # the face must move by 6 % of the width
LIVE_GROW = 1.2                                           # ... or be 20 % bigger


def new_live_steps(rng=None):
    """Two different instructions in a random order (a recorded video cannot know them)."""
    import random
    r = rng or random.SystemRandom()
    steps = list(LIVE_STEPS)
    r.shuffle(steps)
    return steps[:2]


def _gray_small(img):
    from PIL import ImageOps
    return ImageOps.exif_transpose(img).convert("L").resize((48, 48))


def liveness(frames, steps):
    """Version 7: a simple live check. frames: 3 photos taken one after the other - looking at the camera, then after
    each of the 2 instructions. Each photo must show one face; the photos must not be the same picture; the face must
    move as asked (raw camera picture: when a person moves to his left, his face moves to the right of the picture).
    A held-up still photo fails. It is a hint for the person who decides, like the face score."""
    import photocheck
    if len(frames) != len(steps) + 1 or len(steps) < 1 or any(s not in LIVE_STEPS for s in steps):
        return {"ok": False, "note": "The live check needs one photo for the start and one for each instruction."}
    boxes = []
    for f in frames:
        b = photocheck.faces(f)
        if b is None:
            return {"ok": False, "note": "The face model is missing."}
        if not b:
            return {"ok": False, "note": "No face on one of the live photos."}
        if not boxes:                                   # the first photo: the biggest face
            boxes.append(max(b, key=lambda r: r[2] * r[3]))
        else:                                           # then follow the same face (the nearest to the last one)
            px, py = boxes[-1][0] + boxes[-1][2] / 2, boxes[-1][1] + boxes[-1][3] / 2
            boxes.append(min(b, key=lambda r: (r[0] + r[2] / 2 - px) ** 2 + (r[1] + r[3] / 2 - py) ** 2))
    smalls = [list(_gray_small(f).tobytes()) for f in frames]       # grey values 0..255
    for a, b in zip(smalls, smalls[1:]):
        diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        if diff < 2.0:
            return {"ok": False, "note": "Two live photos are the same picture."}
    notes = []
    for i, step in enumerate(steps):
        (x0, y0, w0, h0), (x1, y1, w1, h1) = boxes[i], boxes[i + 1]
        c0, c1 = x0 + w0 / 2, x1 + w1 / 2
        if step == "closer":
            ok = (w1 * h1) >= LIVE_GROW * (w0 * h0)
        elif step == "left":
            ok = c1 - c0 >= LIVE_SHIFT
        else:
            ok = c0 - c1 >= LIVE_SHIFT
        notes.append(f"{step}: {'yes' if ok else 'no'}")
        if not ok:
            return {"ok": False, "note": "The face did not move as asked (" + ", ".join(notes) + ")."}
    return {"ok": True, "note": "The face moved as asked (" + ", ".join(notes) + ")."}


def hint_for(score, faces_doc, faces_selfie, mrz=None):
    if faces_doc is None or faces_selfie is None:
        return "The face check is not available on this server. Please compare the pictures yourself."
    if not faces_doc:
        return "No face found on the document photo."
    if not faces_selfie:
        return "No face found on the selfie."
    parts = []
    if score >= SAME_FROM:
        parts.append(f"The faces look alike (score {score:g}).")
    elif score < DIFFERENT_BELOW:
        parts.append(f"The faces look different (score {score:g}). Please look carefully.")
    else:
        parts.append(f"Not sure (score {score:g}): please compare the pictures yourself.")
    if faces_doc > 1 or faces_selfie > 1:
        parts.append("More than one face on a picture.")
    if mrz is not None:
        parts.append("The MRZ check digits are right." if mrz["valid"] else "The MRZ check digits are WRONG.")
        if mrz["expired"]:
            parts.append("The passport has EXPIRED.")
    return " ".join(parts)


class IdCheckMixin:
    def start_id_check(self, user_id, purpose, doc_type, country, doc_file, selfie_file, consent, mrz1="", mrz2="",
                       expiry="", live_frames=None, live_steps=None):
        from PIL import Image
        import images
        if not consent:
            raise ShopError("Please read and accept how we use your ID photos.")
        if doc_type not in DOC_TYPES:
            raise ShopError("Please choose the kind of document.")
        mrz = parse_mrz(mrz1, mrz2) if (mrz1 or mrz2) else None
        try:
            doc_img = Image.open(io.BytesIO(doc_file.read() if hasattr(doc_file, "read") else doc_file))
            selfie_img = Image.open(io.BytesIO(selfie_file.read() if hasattr(selfie_file, "read") else selfie_file))
            doc_img.load()
            selfie_img.load()
        except Exception:
            raise ShopError("Please upload two photos (JPG or PNG): the document and a selfie.")
        score, nd, ns = face_score(doc_img, selfie_img)
        live = None
        if live_frames:                                  # version 7: three photos taken after the instructions
            try:
                frames = []
                for f in live_frames:
                    im = Image.open(io.BytesIO(f.read() if hasattr(f, "read") else f))
                    im.load()
                    frames.append(im)
            except Exception:
                raise ShopError("The live photos could not be read. Please try again.")
            live = liveness(frames, live_steps or [])
        exp = (mrz["expiry"].isoformat() if mrz and mrz["expiry"] else str(expiry or "")[:10])
        if exp and exp < date.today().isoformat():
            raise ShopError("This document has expired. Please use a valid document.")
        last3 = (mrz["number"][-3:] if mrz else "")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        doc_name, selfie_name = f"private/id/{user_id}-{stamp}-doc.jpg", f"private/id/{user_id}-{stamp}-selfie.jpg"
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM id_checks WHERE user_id = ? AND status = 'Waiting'", (user_id,)).fetchone():
                raise ShopError("Your ID check is already waiting for a person.")
        self.save_media(doc_name, "image/jpeg", images._jpeg(images.ImageOps.contain(doc_img.convert("RGB"),
                                                                                      (1400, 1400)), 85))
        self.save_media(selfie_name, "image/jpeg", images._jpeg(images.ImageOps.contain(selfie_img.convert("RGB"),
                                                                                         (1000, 1000)), 85))
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM id_checks WHERE user_id = ? AND status = 'Waiting'", (user_id,)).fetchone():
                raise ShopError("Your ID check is already waiting for a person.")
            cid = conn.execute(
                "INSERT INTO id_checks (user_id, purpose, doc_type, country, doc_last3, expiry, mrz_ok, faces_doc, "
                "faces_selfie, score, hint, consent_at, doc_image, selfie_image, created_at, live_ok, live_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, purpose[:20], doc_type, (mrz["country"] if mrz else str(country or ""))[:3].upper(), last3,
                 exp, None if mrz is None else int(mrz["valid"]), nd or 0, ns or 0, score,
                 hint_for(score or 0, nd, ns, mrz) + ("" if live is None else
                                                      (" Live check passed." if live["ok"] else " Live check failed.")),
                 now(), doc_name, selfie_name, now(), None if live is None else int(live["ok"]),
                 "" if live is None else live["note"])).lastrowid
        return cid

    def id_checks(self, status="", user_id=None, limit=100):
        sql, args = "SELECT c.*, u.name, u.email FROM id_checks c JOIN users u ON u.id = c.user_id WHERE 1 = 1", []
        if status:
            sql += " AND c.status = ?"
            args.append(status)
        if user_id:
            sql += " AND c.user_id = ?"
            args.append(user_id)
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY c.id DESC LIMIT ?", (*args, limit))]

    def decide_id_check(self, user_id, check_id, approve, note=""):
        if not self.can(user_id, "photos.review"):
            raise ShopError("You do not have the ability to check ID documents.")
        u = self.get_user(user_id)
        purge = (datetime.now() + timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            c = conn.execute("SELECT * FROM id_checks WHERE id = ?", (check_id,)).fetchone()
            if c is None:
                raise ShopError("Unknown ID check.")
            if c["user_id"] == user_id:
                raise ShopError("Nobody can decide his own ID check.")
            if not conn.execute("UPDATE id_checks SET status = ?, decided_by = ?, decided_at = ?, note = ?, purge_after = ? "
                                "WHERE id = ? AND status = 'Waiting'",
                                ("Approved" if approve else "Refused", u["name"], now(), " ".join(str(note).split())[:200],
                                 purge, check_id)).rowcount:
                raise ShopError("Somebody decided this check a moment ago.")
            if approve:
                conn.execute("UPDATE users SET id_verified = 1 WHERE id = ?", (c["user_id"],))
            self.notify(conn, c["user_id"], "ID check " + ("approved" if approve else "refused"),
                        "Your ID is confirmed. Thank you!" if approve else
                        "We could not confirm your ID" + (f": {note}" if note else ".") + " You can try again.",
                        url="/id-check")

    def withdraw_id_consent(self, user_id):
        """The person takes back his consent: the pictures are deleted now; the result line stays."""
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM id_checks WHERE user_id = ? AND (doc_image != '' OR selfie_image != '')",
                                (user_id,)).fetchall()
            for r in rows:
                self._drop_id_images(conn, r)
                conn.execute("UPDATE id_checks SET status = CASE WHEN status = 'Waiting' THEN 'Withdrawn' ELSE status END "
                             "WHERE id = ?", (r["id"],))
        return len(rows)

    def _drop_id_images(self, conn, r):
        for name in (r["doc_image"], r["selfie_image"]):
            if name:
                conn.execute("DELETE FROM media WHERE name = ?", (name,))
        conn.execute("UPDATE id_checks SET doc_image = '', selfie_image = '' WHERE id = ?", (r["id"],))

    def purge_id_images(self, at=None):
        """Daily: the pictures of decided checks are deleted RETENTION_DAYS after the decision."""
        at = at or now()
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM id_checks WHERE purge_after != '' AND purge_after <= ? AND "
                                "(doc_image != '' OR selfie_image != '')", (at,)).fetchall()
            for r in rows:
                self._drop_id_images(conn, r)
        return len(rows)
