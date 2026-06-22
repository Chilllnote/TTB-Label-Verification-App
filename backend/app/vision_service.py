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
    """OpenAI GPT-4o vision service with structured JSON output.
    
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

        self.client = AsyncOpenAI(api_key=api_key, timeout=4.5)
    
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
            
            # Build extraction prompt (critical: verbatim warning instruction)
            system_prompt = (
                "You are an alcohol and tobacco label verification specialist. "
                "Extract structured data from product labels. "
                "Return ONLY valid JSON matching the specified schema. "
                "If you cannot reliably extract a field, use null."
            )
            
            user_prompt = (
                "Extract exactly these 7 fields from the label image:\n"
                "1. brand: product brand name\n"
                "2. class: product classification (e.g., 'Vodka', 'Whiskey', 'Cigar')\n"
                "3. producer: manufacturer or distillery name\n"
                "4. country: country of origin\n"
                "5. abv: alcohol by volume (copy as shown on label, e.g., '45%' or '45% Alc./Vol.')\n"
                "6. net_contents: volume/quantity (copy as shown, e.g., '750 ml')\n"
                "7. government_warning: exact government warning text\n\n"
                "CRITICAL FOR government_warning:\n"
                "- Copy character-for-character AS DISPLAYED on the label\n"
                "- Include ALL case, punctuation, and whitespace EXACTLY\n"
                "- Do NOT normalize, correct, or reformat the text\n"
                "- Do NOT change 'WARNING: CONTAINS ALCOHOL' to 'Warning: Contains Alcohol'\n"
                "- If the warning spans multiple lines, preserve line breaks\n"
                "- If you cannot read the warning clearly, use null\n\n"
                "Return JSON with these 7 fields (all nullable)."
            )
            
            # Call GPT-4o with explicit JSON schema
            response = await self.client.chat.completions.create(
                model="gpt-4o",
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
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                response_format={
                    "type": "json_object",
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
                timeout=4.5
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
            if error_name in {"APIConnectionError", "Timeout"}:
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
    """
    
    def __init__(self, response: Optional[ExtractedLabel] = None):
        """Initialize with optional fixed response.
        
        Args:
            response: ExtractedLabel to return. Defaults to realistic label.
        """
        self.response = response or ExtractedLabel(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
    
    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        """Return fixed mock response."""
        logger.info(f"Mock extraction returning: {self.response}")
        return self.response
