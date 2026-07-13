"""Image preprocessing for label extraction.

Handles downscaling, JPEG re-encoding, and validation to optimize for
vision API inference while preserving label readability within 5-second budget.
"""

import io
import logging
from dataclasses import dataclass
from typing import Tuple

from PIL import (
    Image,
    ImageChops,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    UnidentifiedImageError,
)

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}
MAX_IMAGE_PIXELS = 20_000_000
DEFAULT_MAX_DIMENSION = 768
DEFAULT_JPEG_QUALITY = 55
DEFAULT_GRAYSCALE = False
DEFAULT_THRESHOLD_MODE = "off"
DEFAULT_CONTRAST = False


@dataclass(frozen=True)
class ImageInfo:
    """Basic validated image metadata."""

    width: int
    height: int
    format: str


def get_image_dimensions(image_bytes: bytes) -> Tuple[int, int] | None:
    """Get image width and height without loading full image.

    Returns (width, height) or None if image is invalid.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return img.width, img.height
    except Exception as e:
        logger.warning(f"Failed to read image dimensions: {e}")
        return None


def inspect_image(image_bytes: bytes) -> ImageInfo:
    """Validate uploaded bytes as a readable supported image and return metadata.

    Raises:
        ValueError: if the image is not readable, is unsupported, or is too large.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            image_format = (img.format or "").upper()
            width, height = img.size
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(
            "Image could not be read. Please choose a clear image file."
        ) from exc

    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValueError("Invalid image format. Please choose a supported image file.")

    if width <= 0 or height <= 0:
        raise ValueError("Image could not be read. Please choose a clear image file.")

    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError(
            "Image dimensions are too large. Please choose a smaller photo."
        )

    return ImageInfo(width=width, height=height, format=image_format)


def preprocess_image(
    image_bytes: bytes,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    grayscale: bool = DEFAULT_GRAYSCALE,
    threshold_mode: str = DEFAULT_THRESHOLD_MODE,
    enhance_contrast: bool = DEFAULT_CONTRAST,
) -> bytes:
    """Preprocess image: cap dimensions and re-encode as JPEG.

    Args:
        image_bytes: Raw image bytes from upload
        max_dimension: Maximum width or height in pixels
        jpeg_quality: JPEG quality used for re-encoding
        grayscale: Convert to grayscale before JPEG encoding
        threshold_mode: "off", "binary", or "adaptive" black/white conversion
        enhance_contrast: Apply light contrast enhancement before thresholding

    Returns:
        Preprocessed image bytes (typically 50-150KB)

    On error (corrupt image, unsupported format), returns original bytes
    and logs warning. Never throws exception.
    """
    try:
        # Load image
        img = Image.open(io.BytesIO(image_bytes))

        # Convert RGBA/palette modes to RGB for JPEG compatibility
        if img.mode in ("RGBA", "LA", "P", "1"):
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = rgb_img
        elif img.mode != "RGB":
            img = img.convert("RGB")

        max_dimension = max(320, min(int(max_dimension), 1600))
        jpeg_quality = max(50, min(int(jpeg_quality), 90))
        threshold_mode = (threshold_mode or "off").lower()
        if threshold_mode not in {"off", "binary", "adaptive"}:
            threshold_mode = "off"

        # Downscale if necessary. Cap the long edge so tall phone photos
        # cannot slip through as large token-heavy images.
        longest_edge = max(img.width, img.height)
        if longest_edge > max_dimension:
            ratio = max_dimension / longest_edge
            new_width = max(1, int(img.width * ratio))
            new_height = max(1, int(img.height * ratio))
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            logger.info(f"Downscaled image to {new_width}x{new_height}")

        if grayscale or threshold_mode != "off":
            gray_img = ImageOps.grayscale(img)
            if enhance_contrast:
                gray_img = ImageEnhance.Contrast(gray_img).enhance(1.35)
                gray_img = gray_img.filter(ImageFilter.SHARPEN)

            if threshold_mode == "binary":
                gray_img = gray_img.point(
                    lambda pixel: 255 if pixel >= 175 else 0, mode="1"
                ).convert("L")
            elif threshold_mode == "adaptive":
                local_mean = gray_img.filter(ImageFilter.BoxBlur(7))
                gray_img = Image.eval(
                    ImageChops.subtract(gray_img, local_mean),
                    lambda pixel: 255 if pixel >= 12 else 0,
                )

            img = gray_img.convert("RGB")

        # Re-encode as JPEG at a measured quality target for faster upload/inference.
        output_buffer = io.BytesIO()
        img.save(output_buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        preprocessed_bytes = output_buffer.getvalue()

        logger.info(
            f"Preprocessed image: {len(image_bytes)} → {len(preprocessed_bytes)} bytes"
        )
        return preprocessed_bytes

    except Exception as e:
        logger.error(f"Image preprocessing failed: {e}. Returning original bytes.")
        return image_bytes
