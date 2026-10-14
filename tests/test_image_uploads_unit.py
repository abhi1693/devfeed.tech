"""Private raster uploads bound decoding, preserve artwork, and reject invalid images."""

import io
import struct
import zlib

import pytest
from devfeed_http import image_uploads
from fastapi import HTTPException
from PIL import Image, PngImagePlugin


def picture(size=(120, 80), *, format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("size", [(1600, 900), (900, 1600), (120, 80)])
def test_creative_variants_preserve_aspect_ratio_and_report_actual_widths(size):
    encoded, widths = image_uploads.ImageUploadStorage("unit-uploads", (400, 800, 1200)).normalize(
        picture(size)
    )
    assert set(encoded) == {400, 800, 1200}
    for slot, body in encoded.items():
        with Image.open(io.BytesIO(body)) as variant:
            assert variant.format == "WEBP"
            assert variant.width == widths[slot] <= min(slot, size[0])
            assert variant.height <= min(1200, size[1])
            assert abs(variant.height - variant.width * size[1] / size[0]) <= 1


def test_orientation_is_applied_before_resizing_and_private_metadata_is_removed():
    source = Image.new("RGB", (80, 120), "blue")
    source.paste("red", (0, 0, 80, 60))
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "Private image description"
    output = io.BytesIO()
    source.save(output, format="JPEG", exif=exif, icc_profile=b"private-profile")

    encoded, widths = image_uploads.ImageUploadStorage("unit-uploads", (40, 80, 120)).normalize(
        output.getvalue()
    )

    with Image.open(io.BytesIO(encoded[120])) as variant:
        assert variant.size == (120, 80) and widths[120] == 120
        left, right = variant.convert("RGB").getpixel((20, 40)), variant.getpixel((100, 40))
        assert left[2] > 200 and left[0] < 40
        assert right[0] > 200 and right[2] < 40
        assert not variant.getexif()
        assert not {"exif", "icc_profile", "xmp"}.intersection(variant.info)
    assert all(b"Private image description" not in body for body in encoded.values())


def test_png_upload_decodes_pixels_once_after_preflight_verification(monkeypatch):
    decoded = []
    original = PngImagePlugin.PngImageFile.load

    def load(image, *args, **kwargs):
        if image.tile:
            decoded.append(image)
        return original(image, *args, **kwargs)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", load)
    upload = image_uploads.ImageUploadStorage("unit-uploads", (40, 80, 120))
    encoded, widths = upload.normalize(picture())
    assert len(decoded) == 1
    assert set(encoded) == {40, 80, 120} and widths[120] == 120


def test_animated_webp_is_frozen_to_its_first_frame():
    output = io.BytesIO()
    Image.new("RGB", (120, 80), "red").save(
        output,
        format="WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (120, 80), "blue")],
        duration=100,
        loop=0,
    )
    with Image.open(io.BytesIO(output.getvalue())) as source:
        assert source.n_frames == 2

    encoded, _ = image_uploads.ImageUploadStorage("unit-uploads", (40, 80, 120)).normalize(
        output.getvalue()
    )

    for body in encoded.values():
        with Image.open(io.BytesIO(body)) as variant:
            assert variant.n_frames == 1 and not variant.is_animated
            pixel = variant.convert("RGB").getpixel((variant.width // 2, variant.height // 2))
            assert pixel[0] > 200 and pixel[2] < 40


@pytest.mark.parametrize("body", [b"<svg></svg>", b"not an image", picture(format="GIF")])
def test_invalid_rasters_are_rejected_and_release_the_normalization_slot(body):
    with pytest.raises(HTTPException) as error:
        image_uploads.ImageUploadStorage("unit-uploads", (400, 800, 1200)).normalize(body)
    assert error.value.status_code == 422
    assert image_uploads.ImageUploadStorage("unit-uploads", (400,)).normalize(picture())[1] == {
        400: 120
    }


def test_file_byte_limit_is_checked_before_image_decoding(monkeypatch):
    def decode(*args, **kwargs):
        pytest.fail("Oversized upload reached the image decoder")

    monkeypatch.setattr(image_uploads.Image, "open", decode)
    with pytest.raises(HTTPException) as error:
        image_uploads.ImageUploadStorage("unit-uploads", (400,)).normalize(
            b"x" * (image_uploads.MAX_BYTES + 1)
        )
    assert error.value.status_code == 413


def test_twenty_megapixel_limit_is_checked_before_decoding_pixels(monkeypatch):
    # Advertise oversized dimensions in a real PNG header without allocating its pixels.
    body = bytearray(picture((1, 1)))
    body[16:24] = struct.pack(">II", 5000, 4001)
    body[29:33] = struct.pack(">I", zlib.crc32(body[12:29]))
    with Image.open(io.BytesIO(body)) as source:
        assert source.width * source.height > image_uploads.MAX_PIXELS == 20_000_000

    def decode(*args, **kwargs):
        pytest.fail("Oversized dimensions reached pixel decoding or verification")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "verify", decode)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", decode)
    with pytest.raises(HTTPException) as error:
        image_uploads.ImageUploadStorage("unit-uploads", (400,)).normalize(bytes(body))
    assert error.value.status_code == 422


def test_concurrent_normalization_rejects_without_waiting_and_can_be_retried():
    assert image_uploads._normalization_slot.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            image_uploads.ImageUploadStorage("unit-uploads", (400,)).normalize(picture())
        assert error.value.status_code == 503
        assert error.value.headers == {"Retry-After": "1"}
    finally:
        image_uploads._normalization_slot.release()
    assert image_uploads.ImageUploadStorage("unit-uploads", (400,)).normalize(picture())[1] == {
        400: 120
    }
