"""Vision service for extracting label data from images.

Provides abstract VisionService base class, OpenAI implementation with
structured JSON output + defensive parsing, and mock for testing.
"""

import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import ValidationError

from app.models import ExtractedLabel

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


class VisionService(ABC):
    """Abstract base class for label extraction from images."""
    
    @abstractmethod
    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        """Extract label fields from image.
        
        Args:
            image_bytes: JPEG/PNG image bytes
        
        Returns:
            ExtractedLabel with fields populated or null.
            Always returns ExtractedLabel; never throws exception.
        """
        pass


class OpenAIVisionService(VisionService):
    """OpenAI vision service with structured JSON output.
    
    Uses response_format with explicit JSON schema for guaranteed structure.
    Defensive parsing catches malformed responses. Timeouts and API errors
    return all-null ExtractedLabel.
    """
    
    def __init__(self):
        """Initialize OpenAI client from OPENAI_API_KEY environment variable."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        from openai import AsyncOpenAI

        self.model_name = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        self.timeout_seconds = _env_float("OPENAI_TIMEOUT_SECONDS", 3.8, 1.0, 4.5)
        self.image_detail = os.getenv("OPENAI_IMAGE_DETAIL", "low").strip().lower()
        if self.image_detail not in {"low", "high", "auto"}:
            self.image_detail = "low"

        self.client = AsyncOpenAI(
            api_key=api_key,
            timeout=self.timeout_seconds,
            max_retries=0,
        )
    
    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        """Extract label fields from image using GPT-4o.
        
        Returns all-null ExtractedLabel on:
        - Non-label image (model returns nulls)
        - API timeout/error
        - Malformed JSON response
        - Invalid image format
        """
        try:
            # Encode image to base64 for API
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            
            # Build a compact extraction prompt (critical: verbatim warning instruction).
            system_prompt = (
                "Extract alcohol or tobacco label fields. Return only JSON matching "
                "the schema. Use null for unreadable fields."
            )
            
            user_prompt = (
                "Extract: brand, class, producer, country, abv, net_contents, "
                "government_warning. Copy abv/net_contents as shown. "
                "For government_warning, copy character-for-character exactly as "
                "displayed, preserving case, punctuation, spacing, and line breaks. "
                "Do not normalize or correct the warning. Use null when unclear."
            )
            
            # Call the configured vision model with explicit JSON schema.
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}",
                                    "detail": self.image_detail
                                }
                            }
                        ]
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "LabelExtraction",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "brand": {"type": ["string", "null"]},
                                "class": {"type": ["string", "null"]},
                                "producer": {"type": ["string", "null"]},
                                "country": {"type": ["string", "null"]},
                                "abv": {"type": ["string", "null"]},
                                "net_contents": {"type": ["string", "null"]},
                                "government_warning": {"type": ["string", "null"]}
                            },
                            "required": [
                                "brand", "class", "producer", "country",
                                "abv", "net_contents", "government_warning"
                            ],
                            "additionalProperties": False
                        }
                    }
                },
                temperature=0,
                max_tokens=500,
                timeout=self.timeout_seconds,
            )
            
            # Parse JSON response defensively
            response_json = json.loads(response.choices[0].message.content)
            extracted = ExtractedLabel(**response_json)
            logger.info(f"Extracted label: {extracted}")
            return extracted
            
        except ValidationError as e:
            logger.error(f"Malformed extraction JSON (Pydantic validation failed): {e}")
            return ExtractedLabel(brand=None, product_class=None, producer=None,
                                country=None, abv=None, net_contents=None,
                                government_warning=None)
        except json.JSONDecodeError as e:
            logger.error(f"Malformed extraction JSON (invalid JSON): {e}")
            return ExtractedLabel(brand=None, product_class=None, producer=None,
                                country=None, abv=None, net_contents=None,
                                government_warning=None)
        except Exception as e:
            error_name = e.__class__.__name__
            if error_name in {"APIConnectionError", "APITimeoutError", "Timeout"}:
                logger.error(f"Vision API network error ({error_name}): {e}")
            elif error_name == "RateLimitError":
                logger.error(f"Vision API rate limited: {e}")
            else:
                logger.error(f"Unexpected vision service error: {e}")
            return ExtractedLabel(brand=None, product_class=None, producer=None,
                                country=None, abv=None, net_contents=None,
                                government_warning=None)


class MockVisionService(VisionService):
    """Mock VisionService for testing (no API calls).
    
    Returns fixed ExtractedLabel objects. Can be configured per test.
    When using the default response, simple color markers in synthetic test
    images can simulate unreadable or missing-warning extraction outcomes.
    """
    
    def __init__(self, response: Optional[ExtractedLabel] = None):
        """Initialize with optional fixed response.
        
        Args:
            response: ExtractedLabel to return. Defaults to realistic label.
        """
        self._fixed_response = response is not None
        self.response = response or ExtractedLabel(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )

    def _marker_response(self, image_bytes: bytes) -> Optional[ExtractedLabel]:
        """Detect synthetic mock-only image markers used by live checklist tests."""
        if self._fixed_response:
            return None

        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                img = img.convert("RGB")
                corner = img.crop((0, 0, min(32, img.width), min(32, img.height)))
                center_left = max(0, (img.width // 2) - 16)
                center_top = max(0, (img.height // 2) - 16)
                center = img.crop(
                    (
                        center_left,
                        center_top,
                        min(img.width, center_left + 32),
                        min(img.height, center_top + 32),
                    )
                )
                corner_rgb = corner.resize((1, 1)).getpixel((0, 0))
                center_rgb = center.resize((1, 1)).getpixel((0, 0))
        except Exception:
            return None

        red, green, blue = corner_rgb
        center_red, center_green, center_blue = center_rgb

        corner_is_blue = blue > 150 and red < 120 and green < 140
        center_is_blue = center_blue > 150 and center_red < 120 and center_green < 140
        if corner_is_blue and not center_is_blue:
            return ExtractedLabel(
                brand=None,
                product_class=None,
                producer=None,
                country=None,
                abv=None,
                net_contents=None,
                government_warning=None,
            )

        corner_is_red = red > 150 and green < 120 and blue < 120
        center_is_red = center_red > 150 and center_green < 120 and center_blue < 120
        if corner_is_red and not center_is_red:
            return ExtractedLabel(
                brand=self.response.brand,
                product_class=self.response.product_class,
                producer=self.response.producer,
                country=self.response.country,
                abv=self.response.abv,
                net_contents=self.response.net_contents,
                government_warning=None,
            )

        return None
    
    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        """Return fixed mock response."""
        marker_response = self._marker_response(image_bytes)
        response = marker_response or self.response
        logger.info(f"Mock extraction returning: {response}")
        return response
