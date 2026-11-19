"""
Product pictures: what is accepted, and how an upload is prepared before it is stored.

Whatever the admin uploads (a 12 MB phone photo, a PNG, a rotated JPEG) is turned into a modest WebP
before it goes to storage (S3 in production). Customers on mobile data then download ~100 KB instead of
several MB, and the storefront can use the file as it is.
"""

from __future__ import annotations

import uuid
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # what we accept from the admin
MAX_SIDE = 1200  # longest side stored, in pixels (cards show ~500 px wide, product pages ~700)
QUALITY = 82


def validate_image_size(file) -> None:
    if file.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f"The picture is too big ({file.size // (1024 * 1024)} MB). The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")


def optimize(uploaded) -> ContentFile:
    """The uploaded picture as a right-way-up, resized WebP (the original file is left alone)."""
    uploaded.seek(0)
    with Image.open(uploaded) as img:
        img = ImageOps.exif_transpose(img)  # phone photos carry their rotation in metadata
        # keep transparency (logos, cut-outs); everything else is flattened to plain RGB
        has_alpha = "A" in img.getbands() or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)  # only ever shrinks
        out = BytesIO()
        img.save(out, format="WEBP", quality=QUALITY, method=4)
    return ContentFile(out.getvalue(), name="picture.webp")


def menu_image_path(instance, filename: str) -> str:
    """
    Where a product picture is stored: menu/<product>-<random>.webp. The random part means a new upload
    never reuses an address, so browsers and CDNs can cache every picture for good.
    """
    return f"menu/{instance.slug or 'item'}-{uuid.uuid4().hex[:8]}.webp"
