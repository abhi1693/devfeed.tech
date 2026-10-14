"""Configurable image validation and encoding without storage or transport dependencies."""

import io
import warnings
from collections.abc import Iterable


class ImageProcessor:
    def __init__(self, *, max_pixels: int, formats: Iterable[str], orient_width: bool = True):
        self.max_pixels = max_pixels
        self.formats = frozenset(formats)
        self.orient_width = orient_width

    def inspect(self, body: bytes) -> tuple[int, str]:
        from PIL import Image, UnidentifiedImageError

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(body)) as image:
                    if (
                        image.width * image.height > self.max_pixels
                        or image.format not in self.formats
                    ):
                        raise ValueError("Unsupported image")
                    assert image.format is not None
                    rotated = self.orient_width and image.getexif().get(274) in {5, 6, 7, 8}
                    width = image.height if rotated else image.width
                    mime = Image.MIME[image.format]
                # Reading PNG EXIF can load the stream; verify must use a fresh image.
                with Image.open(io.BytesIO(body)) as image:
                    image.verify()
                return width, mime
        except (
            ValueError,
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise ValueError("Unsupported or invalid image") from exc

    def encode(self, image, *, format: str, strip_metadata: bool = False, **options) -> bytes:
        if strip_metadata:
            image.info.clear()
        output = io.BytesIO()
        image.save(output, format=format, **options)
        return output.getvalue()
