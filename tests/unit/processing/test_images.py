"""Image normalisation, downscaling, deskew and blank detection."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from ipa.core.errors import UnsupportedMediaError
from ipa.processing.images import (
    deskew,
    downscale_for_vlm,
    image_dimensions,
    is_probably_blank,
    normalise_image,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _tiff() -> bytes:
    return (FIXTURES / "multipage.tiff").read_bytes()


def _blank_png() -> bytes:
    buffer = BytesIO()
    Image.new("L", (50, 50), 255).save(buffer, format="PNG")
    return buffer.getvalue()


def _textured_png() -> bytes:
    image = Image.new("L", (50, 50), 255)
    for x in range(50):
        for y in range(50):
            if (x + y) % 2 == 0:
                image.putpixel((x, y), 10)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_normalise_image_tiff_to_png() -> None:
    png, mime = normalise_image(_tiff())

    assert mime == "image/png"
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_normalise_image_strips_exif_and_fixes_orientation() -> None:
    image = Image.new("RGB", (30, 30), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6  # orientation: rotate 90
    image.save("tests/fixtures/tmp-orient.jpg", exif=exif)
    data = (FIXTURES / "tmp-orient.jpg").read_bytes()
    (FIXTURES / "tmp-orient.jpg").unlink()

    png, _ = normalise_image(data)

    reopened = Image.open(BytesIO(png))
    assert reopened.size == (30, 30)


def test_image_dimensions() -> None:
    assert image_dimensions(_blank_png()) == (50, 50)


def test_downscale_for_vlm_reduces_large_edge() -> None:
    image = Image.new("L", (4096, 1024), 255)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()

    out = downscale_for_vlm(data, max_long_edge=2048)

    assert image_dimensions(out)[0] <= 2048


def test_downscale_for_vlm_returns_unchanged_when_small() -> None:
    data = _blank_png()

    assert downscale_for_vlm(data, max_long_edge=2048) == data


def test_deskew_returns_bytes() -> None:
    out = deskew(_textured_png())

    assert isinstance(out, bytes)


def test_is_probably_blank() -> None:
    assert is_probably_blank(_blank_png()) is True
    assert is_probably_blank(_textured_png()) is False


def test_normalise_invalid_image_raises() -> None:
    try:
        normalise_image(b"not an image")
    except UnsupportedMediaError:
        pass
    else:
        raise AssertionError("expected UnsupportedMediaError")
