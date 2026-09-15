# app/services/email_assets.py
"""
Validation for images uploaded to email templates.

Dimensions are read from the file headers directly (PNG, GIF, JPEG) so no
imaging library is needed; the type comes from the magic bytes, never from
the browser-supplied content type or the file extension.
"""
import struct

# What the hero slot in the templates is designed for (2:1, retina at 600px).
RECOMMENDED_WIDTH = 1200
RECOMMENDED_HEIGHT = 600

# Email clients download the image on open; keep it light.
MAX_BYTES = 2 * 1024 * 1024

# WebP is deliberately absent: Outlook for Windows does not render it.
ALLOWED_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}

_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def image_info(data: bytes) -> tuple[str, int, int] | None:
    """(content_type, width, height) from the header, or None if not an image
    this module recognises. Width/height are 0 when the type is known but the
    size could not be read."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height

    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return "image/gif", width, height

    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            (length,) = struct.unpack(">H", data[i + 2:i + 4])
            if marker in _JPEG_SOF:
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return "image/jpeg", width, height
            i += 2 + length
        return "image/jpeg", 0, 0

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", 0, 0

    return None


def validate_upload(data: bytes) -> tuple[dict | None, str | None]:
    """
    Check an uploaded file. Returns (info, error); exactly one is set. info
    is {"content_type", "extension", "width", "height", "size_bytes",
    "recommended": bool}. A size other than the recommended one is allowed
    (the template scales it) and reported through "recommended".
    """
    if not data:
        return None, "No file was received."
    if len(data) > MAX_BYTES:
        return None, (f"The image is {len(data) / 1024 / 1024:.1f} MB; the limit is "
                      f"{MAX_BYTES // 1024 // 1024} MB. Export it as a JPG at lower quality.")

    info = image_info(data)
    if info is None:
        return None, "That file is not a JPG, PNG or GIF image."
    content_type, width, height = info
    if content_type == "image/webp":
        return None, "WebP does not render in Outlook. Export the image as JPG or PNG."
    if content_type not in ALLOWED_TYPES:
        return None, "That file is not a JPG, PNG or GIF image."

    return {
        "content_type": content_type,
        "extension": ALLOWED_TYPES[content_type],
        "width": width,
        "height": height,
        "size_bytes": len(data),
        "recommended": (width, height) == (RECOMMENDED_WIDTH, RECOMMENDED_HEIGHT),
    }, None
