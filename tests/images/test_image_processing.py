import io
from unittest.mock import Mock

import pytest
from devfeed_core.image_processing import ImageProcessor
from PIL import Image


def encoded(image, format, **options):
    output = io.BytesIO()
    image.save(output, format=format, **options)
    return output.getvalue()


def test_format_policy_is_specific_to_the_consumer():
    body = encoded(Image.new("RGB", (12, 8)), "GIF")
    worker = ImageProcessor(max_pixels=40_000_000, formats={"JPEG", "PNG", "WEBP", "GIF", "AVIF"})
    uploads = ImageProcessor(max_pixels=20_000_000, formats={"JPEG", "PNG", "WEBP"})
    assert worker.inspect(body) == (12, "image/gif")
    with pytest.raises(ValueError, match="Unsupported or invalid"):
        uploads.inspect(body)


def test_inspection_uses_display_width_after_exif_rotation():
    exif = Image.Exif()
    exif[274] = 6
    body = encoded(Image.new("RGB", (12, 8)), "JPEG", exif=exif)
    processor = ImageProcessor(max_pixels=100, formats={"JPEG"})
    assert processor.inspect(body) == (8, "image/jpeg")


def test_pixel_limit_rejects_before_loading_metadata_or_verifying(monkeypatch):
    image = Mock(width=200, height=100, format="PNG")
    opened = Mock(__enter__=Mock(return_value=image), __exit__=Mock(return_value=False))
    monkeypatch.setattr(Image, "open", Mock(return_value=opened))
    processor = ImageProcessor(max_pixels=19_999, formats={"PNG"})
    with pytest.raises(ValueError, match="Unsupported or invalid"):
        processor.inspect(b"header-only")
    image.getexif.assert_not_called()
    image.verify.assert_not_called()
    opened.__exit__.assert_called_once()


def test_verification_only_policy_avoids_loading_exif(monkeypatch):
    image = Mock(width=20, height=10, format="PNG")
    opened = Mock(__enter__=Mock(return_value=image), __exit__=Mock(return_value=False))
    monkeypatch.setattr(Image, "open", Mock(return_value=opened))
    processor = ImageProcessor(max_pixels=1000, formats={"PNG"}, orient_width=False)
    assert processor.inspect(b"header-only") == (20, "image/png")
    image.getexif.assert_not_called()
    image.verify.assert_called_once()
    assert opened.__exit__.call_count == 2


def test_logo_encoding_keeps_existing_immutable_png_bytes():
    image = Image.new("RGBA", (32, 16), "#1255ff")
    exif = Image.Exif()
    exif[305] = "Publisher artwork"
    image.info["exif"] = exif.tobytes()
    processor = ImageProcessor(max_pixels=1000, formats={"PNG"})
    expected = encoded(image, "PNG", optimize=True)
    assert processor.encode(image, format="PNG", optimize=True) == expected
    assert image.info["exif"] == exif.tobytes()


def test_upload_encoding_strips_metadata_without_changing_geometry():
    image = Image.new("RGBA", (32, 16), "#1255ff")
    exif = Image.Exif()
    exif[305] = "Private camera metadata"
    image.info.update(exif=exif.tobytes(), icc_profile=b"private profile", xmp=b"private XMP")
    processor = ImageProcessor(max_pixels=1000, formats={"PNG", "WEBP"})
    body = processor.encode(image, format="WEBP", strip_metadata=True, quality=85, method=4)
    assert image.info == {}
    with Image.open(io.BytesIO(body)) as result:
        assert result.size == (32, 16)
        assert not result.getexif()
        assert not {"icc_profile", "xmp", "exif"}.intersection(result.info)
