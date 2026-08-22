"""Image normalisation, downscaling, deskew and blank detection.

Standalone images (and pages rendered from other formats) are normalised to PNG
before OCR: EXIF is stripped, orientation is corrected from the EXIF orientation
tag, and images are downscaled for the VLM to keep token cost sane. All functions
are pure and synchronous.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

from ipa.core.errors import UnsupportedMediaError


def normalise_image(data: bytes) -> tuple[bytes, str]:
    """Convert an image to a clean PNG, stripping EXIF and fixing orientation.

    Args:
        data: Source image bytes.

    Returns:
        A tuple of `(png_bytes, "image/png")`.

    Raises:
        UnsupportedMediaError: If the bytes are not a decodable image.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except Exception as exc:
        raise UnsupportedMediaError(
            "The bytes are not a decodable image.", code="invalid_image"
        ) from exc
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), "image/png"


def image_dimensions(data: bytes) -> tuple[int, int]:
    """Return an image's `(width, height)`.

    Args:
        data: Image bytes.

    Returns:
        A `(width, height)` tuple.

    Raises:
        UnsupportedMediaError: If the bytes are not a decodable image.
    """
    try:
        image = Image.open(io.BytesIO(data))
        return image.size
    except Exception as exc:
        raise UnsupportedMediaError(
            "The bytes are not a decodable image.", code="invalid_image"
        ) from exc


def downscale_for_vlm(data: bytes, max_long_edge: int = 2048) -> bytes:
    """Downscale an image so its longest edge does not exceed a bound.

    Args:
        data: Image bytes.
        max_long_edge: Maximum pixel length of the longest edge.

    Returns:
        Downscaled image bytes (same format as input).
    """
    with Image.open(io.BytesIO(data)) as source:
        width, height = source.size
        longest = max(width, height)
        if longest <= max_long_edge:
            return data
        scale = max_long_edge / longest
        resized = source.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    output = io.BytesIO()
    resized.save(output, format=resized.format or "PNG")
    return output.getvalue()


def deskew(data: bytes) -> bytes:
    """Best-effort deskew using a simple projection-profile estimate.

    Args:
        data: Image bytes.

    Returns:
        The deskewed image bytes (best-effort; returned unchanged when no skew
        can be estimated).
    """
    image = Image.open(io.BytesIO(data)).convert("L")
    best_angle = 0.0
    best_score = -1.0
    for angle in range(-5, 6):
        rotated = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
        score = _column_score(rotated)
        if score > best_score:
            best_score = score
            best_angle = angle
    if best_angle == 0:
        return data
    return _rerotate(data, best_angle)


def is_probably_blank(data: bytes, threshold: float = 0.995) -> bool:
    """Report whether an image is almost entirely uniform (a blank scan).

    Args:
        data: Image bytes.
        threshold: Fraction of pixels within a tight band of the median for the
            image to count as blank.

    Returns:
        True when the image is probably blank.
    """
    image = Image.open(io.BytesIO(data)).convert("L")
    pixels = list(image.getdata())
    if not pixels:
        return True
    median = sorted(pixels)[len(pixels) // 2]
    near = sum(1 for p in pixels if abs(p - median) < 20)
    return near / len(pixels) >= threshold


def _rerotate(data: bytes, angle: float) -> bytes:
    """Rotate an image and re-encode it in the same format.

    Args:
        data: Source image bytes.
        angle: Rotation angle in degrees.

    Returns:
        The rotated image bytes.
    """
    image = Image.open(io.BytesIO(data)).rotate(
        angle, resample=Image.Resampling.BICUBIC, fillcolor=255
    )
    output = io.BytesIO()
    image.save(output, format=image.format or "PNG")
    return output.getvalue()


def _column_score(image: Image.Image) -> float:
    """Score a binary projection by the squared sum of dark pixels per column.

    Args:
        image: A greyscale image.

    Returns:
        The sum of per-column dark-pixel counts squared. Higher means the text
        lines align more vertically with the column axis.
    """
    width, height = image.size
    pixels = list(image.getdata())
    score = 0.0
    for x in range(width):
        column_sum = 0
        for y in range(height):
            if pixels[y * width + x] < 128:
                column_sum += 1
        score += column_sum * column_sum
    return score
