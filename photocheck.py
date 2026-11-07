"""
photocheck.py - automatic checks of uploaded photos (product pictures and courier photos).

Pure image analysis with Pillow - no outside service and no machine-learning model:
  * size        - small pictures look bad when zoomed
  * the object  - on a plain white background only the part with the product is measured
  * sharpness   - how much detail a light blur removes: the variance of the Laplacian (edge) image of the
                  photo divided by the same for a slightly blurred copy. A sharp photo loses a lot (ratio 2-8),
                  a blurry one almost nothing (ratio about 1).
  * light       - mean brightness and contrast (too dark, washed out)
  * background  - share of near-white pixels on the border (product photos look best on white)
  * duplicates  - a "difference hash" (dHash, 64 bits): the same photo, even resized or re-saved, gives
                  almost the same hash. Used to find a courier photo that another account already uploaded,
                  or the same picture used for two different products.
  * a face     - version 5, courier photos only: OpenCV's trained face detector (a Haar cascade: a model
                  trained with AdaBoost on thousands of face pictures, Viola-Jones method). The photo must show
                  exactly one face. Without OpenCV (or without the model file) this check is skipped.
The result is advice for the seller and the admin; only broken files are refused.
"""

import json
import os

from PIL import Image, ImageFilter, ImageOps, ImageStat

from core import now

MIN_SIDE = {"product": 500, "courier": 300, "pod": 200}
SHARP_MIN = 1.1          # sharpness ratio below this looks blurry (see analyse)
_LAPLACE = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
DARK, BRIGHT = 55, 245
LOW_CONTRAST = 22
SAME_PHOTO = 5           # dHash bits that may differ for "the same photo"

PHOTO_SCHEMA = """
CREATE TABLE IF NOT EXISTS photo_checks (
    name TEXT PRIMARY KEY, purpose TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL,
    problems TEXT NOT NULL, details TEXT NOT NULL, dhash TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def dhash(img, size=8):
    """64-bit difference hash as 16 hex digits."""
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.tobytes())                       # one byte per pixel (works with every Pillow version)
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


FACE_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "models",
                          "haarcascade_frontalface_default.xml")
_detector = None


def face_detector():
    """The OpenCV face model, loaded once (None when OpenCV or the model file is missing)."""
    global _detector
    if _detector is None:
        try:
            import cv2
            path = FACE_MODEL if os.path.exists(FACE_MODEL) else os.path.join(
                getattr(getattr(cv2, "data", None), "haarcascades", ""), "haarcascade_frontalface_default.xml")
            d = cv2.CascadeClassifier(path)
            _detector = d if not d.empty() else False
        except Exception:
            _detector = False
    return _detector or None


def faces(img):
    """Faces in a picture: [(x, y, w, h)] as shares of the width / height (0..1), biggest first; None = no model."""
    det = face_detector()
    if det is None:
        return None
    import cv2
    import numpy as np
    small = ImageOps.contain(ImageOps.exif_transpose(img).convert("L"), (640, 640))
    gray = cv2.equalizeHist(np.asarray(small, dtype=np.uint8))
    side = min(small.size)
    found = det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, side // 8),) * 2)
    out = [(float(x) / small.width, float(y) / small.height, float(w) / small.width, float(h) / small.height)
           for x, y, w, h in found]
    return sorted(out, key=lambda b: -b[2] * b[3])


def distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def analyse(img, purpose="product"):
    """Return {score 0-100, problems [...], details {...}, dhash}."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    small = ImageOps.contain(img, (800, 800))
    gray = small.convert("L")
    # measure the object itself, not an empty white background around it (product photos on white)
    box = gray.point(lambda v: 255 if v < 238 else 0).getbbox()
    if box and (box[2] - box[0]) * (box[3] - box[1]) >= 0.04 * gray.width * gray.height:
        pad = 6
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(gray.width, box[2] + pad), min(gray.height, box[3] + pad))
        part = gray.crop(box)
    else:
        part = gray
    stat = ImageStat.Stat(part)
    brightness, contrast = stat.mean[0], stat.stddev[0]
    detail = ImageStat.Stat(part.filter(_LAPLACE)).var[0]
    soft = ImageStat.Stat(part.filter(ImageFilter.GaussianBlur(1.2)).filter(_LAPLACE)).var[0]
    sharp = detail / max(soft, 1e-6)
    # border pixels (5 % frame) that are almost white
    bw, bh = small.size
    m = max(2, min(bw, bh) // 20)
    frame = [small.crop(box) for box in ((0, 0, bw, m), (0, bh - m, bw, bh), (0, 0, m, bh), (bw - m, 0, bw, bh))]
    white = total = 0
    for part in frame:
        raw = part.tobytes()                     # R, G, B bytes of every pixel
        for i in range(0, len(raw), 3):
            total += 1
            white += raw[i] > 230 and raw[i + 1] > 230 and raw[i + 2] > 230
    white_share = round(100 * white / max(1, total))
    white_product = purpose == "product" and white_share >= 90
    problems = []
    if min(w, h) < MIN_SIDE.get(purpose, 300):
        problems.append(f"The photo is small ({w}x{h} px). Use at least {MIN_SIDE.get(purpose, 300)} px.")
    if sharp < SHARP_MIN:
        problems.append("The photo looks blurry. Hold the camera still and focus.")
    if brightness < DARK:
        problems.append("The photo is too dark. Take it in daylight.")
    elif white_product:
        pass                                         # a white product on white: bright and soft on purpose
    elif brightness > BRIGHT and contrast < LOW_CONTRAST:
        problems.append("The photo is too bright (washed out).")
    elif contrast < LOW_CONTRAST:
        problems.append("The photo has very little contrast.")
    face_list = faces(img) if purpose == "courier" else None
    if face_list is not None:
        if not face_list:
            problems.append("No face found. Take a photo of your face, looking at the camera, without a helmet.")
        elif len(face_list) > 1:
            problems.append("More than one face in the photo. The photo must show only you.")
    tips = []
    if purpose == "product" and white_share < 60:
        tips.append("Tip: products look best on a plain white background.")
    score = 100
    score -= 35 if sharp < SHARP_MIN else 0
    score -= 25 if min(w, h) < MIN_SIDE.get(purpose, 300) else 0
    score -= 20 if brightness < DARK or (brightness > BRIGHT and contrast < LOW_CONTRAST and not white_product) else 0
    score -= 10 if contrast < LOW_CONTRAST and not white_product else 0
    score -= 10 if tips else 0
    score -= 30 if face_list is not None and len(face_list) != 1 else 0
    details = {"width": w, "height": h, "sharpness": round(sharp, 2), "brightness": round(brightness),
               "contrast": round(contrast), "white_border": white_share}
    if face_list is not None:
        details["faces"] = len(face_list)
        details["face_boxes"] = [[round(v, 3) for v in b] for b in face_list[:3]]
    return {"score": max(0, score), "problems": problems, "tips": tips, "dhash": dhash(img), "details": details}


class PhotoCheckMixin:

    def save_photo_check(self, name, purpose, report, owner=""):
        """Keep the result; also look for the same photo uploaded before by somebody else."""
        with self.db() as conn:
            same = []
            for r in conn.execute("SELECT name, owner, dhash FROM photo_checks WHERE purpose = ? AND name <> ?",
                                  (purpose, name)):
                if owner and r["owner"] == owner:
                    continue
                if distance(r["dhash"], report["dhash"]) <= SAME_PHOTO:
                    same.append(r["name"])
            problems = list(report["problems"])
            if same:
                problems.append("The same photo was already uploaded for another "
                                + ("account" if purpose == "courier" else "product") + ".")
            conn.execute("INSERT INTO photo_checks (name, purpose, owner, score, problems, details, dhash, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET purpose = excluded.purpose, "
                         "owner = excluded.owner, score = excluded.score, problems = excluded.problems, "
                         "details = excluded.details, dhash = excluded.dhash, created_at = excluded.created_at",
                         (name, purpose, owner, max(0, report["score"] - (40 if same else 0)),
                          json.dumps(problems + report.get("tips", [])),
                          json.dumps(dict(report["details"], same_as=same)), report["dhash"], now()))
        return {"score": max(0, report["score"] - (40 if same else 0)), "problems": problems,
                "tips": report.get("tips", []), "same_as": same}

    def photo_check(self, name):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM photo_checks WHERE name = ?", (name,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["problems"], d["details"] = json.loads(d["problems"]), json.loads(d["details"])
        return d

    def photo_checks(self, purpose=None, limit=200):
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM photo_checks " + ("WHERE purpose = ? " if purpose else "") +
                                "ORDER BY score, created_at DESC LIMIT ?", ((purpose, limit) if purpose else (limit,)))
            out = []
            for r in rows:
                d = dict(r)
                d["problems"], d["details"] = json.loads(d["problems"]), json.loads(d["details"])
                out.append(d)
        return out
