"""Image preprocessing for label extraction.

Handles downscaling, JPEG re-encoding, and validation to optimize for
vision API inference while preserving label readability within 5-second budget.
"""

import io
import logging
from typing import Tuple

from PIL import Image

logger = logging.getLogger(__name__)


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


def preprocess_image(image_bytes: bytes, target_width: int = 768) -> bytes:
    """Preprocess image: downscale to target width, re-encode as JPEG 80%.
    
    Args:
        image_bytes: Raw image bytes from upload
        target_width: Target width in pixels (default 768px)
    
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
        
        # Downscale if necessary
        if img.width > target_width:
            ratio = target_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
            logger.info(f"Downscaled image to {target_width}x{new_height}")
        
        # Re-encode as JPEG with quality 80
        output_buffer = io.BytesIO()
        img.save(output_buffer, format="JPEG", quality=80, optimize=True)
        preprocessed_bytes = output_buffer.getvalue()
        
        logger.info(f"Preprocessed image: {len(image_bytes)} → {len(preprocessed_bytes)} bytes")
        return preprocessed_bytes
        
    except Exception as e:
        logger.error(f"Image preprocessing failed: {e}. Returning original bytes.")
        return image_bytes
