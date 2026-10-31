"""
images.py - pictures uploaded by the admin or a seller (products and categories).

Every uploaded file is opened with Pillow (so only real images are accepted),
then turned into an 800x800 JPG - the same format as the built-in product pictures:
    products/<slug>-1.jpg   main picture (white background)
    products/<slug>-2.jpg   second picture (or a styled copy of the first)
    products/<slug>-3.jpg   third picture (or a close-up of the first)

The pictures are saved IN THE DATABASE (table media), not in the static folder: free hosting
deletes the server's files at every restart, the database keeps them. They are served at /img/<name>.
"""

import io

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from store import ShopError

SIZE = 800
ALLOWED = (".jpg", ".jpeg", ".png", ".webp")


def _open(file):
    name = (file.filename or "").lower()
    if not name.endswith(ALLOWED):
        raise ShopError("Pictures must be JPG, PNG or WEBP files.")
    try:
        img = Image.open(file.stream)
        img.load()
    except (UnidentifiedImageError, OSError):
        raise ShopError(f"'{file.filename}' is not a valid picture.")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):                       # transparent PNG -> white background
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _square(img, size=SIZE, color="white"):
    """Fit the whole picture inside a square (no cropping)."""
    return ImageOps.pad(img, (size, size), Image.LANCZOS, color=color)


def _styled(img):
    bg = Image.new("RGB", (SIZE, SIZE), (255, 243, 236))
    d = ImageDraw.Draw(bg)
    d.ellipse([SIZE * 0.12, SIZE * 0.12, SIZE * 0.88, SIZE * 0.88], fill=(255, 231, 217))
    small = ImageOps.contain(img, (int(SIZE * 0.72), int(SIZE * 0.72)), Image.LANCZOS)
    bg.paste(small, ((SIZE - small.width) // 2, (SIZE - small.height) // 2))
    return bg


def _closeup(img):
    sq = _square(img)
    m = SIZE // 6
    return sq.crop((m, m, SIZE - m, SIZE - m)).resize((SIZE, SIZE), Image.LANCZOS)


def _jpeg(img, quality=88):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def save_product_images(files, slug, store):
    """files: up to 3 uploaded files (FileStorage). Returns how many were uploaded."""
    files = [f for f in files if f and f.filename][:3]
    if not files:
        return 0
    pictures = [_open(f) for f in files]                     # check all before saving any
    first = pictures[0]
    views = [_square(first),
             _square(pictures[1]) if len(pictures) > 1 else _styled(first),
             _square(pictures[2]) if len(pictures) > 2 else _closeup(first)]
    for i, view in enumerate(views, start=1):
        store.save_media(f"products/{slug}-{i}.jpg", "image/jpeg", _jpeg(view))
    return len(files)


def save_category_image(file, slug, store):
    """Returns the picture's name (as kept in the categories table)."""
    img = _open(file)
    store.save_media(f"categories/{slug}.jpg", "image/jpeg", _jpeg(ImageOps.fit(img, (600, 600), Image.LANCZOS), 86))
    return f"categories/{slug}.jpg"


def delete_product_images(slug, store):
    store.delete_media(f"products/{slug}-")
