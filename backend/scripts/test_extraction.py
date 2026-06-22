#!/usr/bin/env python
"""Test script: extract label from sample image using VisionService.

Usage:
    # With mock service (no API call)
    USE_MOCK_VISION=true python scripts/test_extraction.py
    
    # With real OpenAI API (requires OPENAI_API_KEY)
    OPENAI_API_KEY=sk-... python scripts/test_extraction.py <image_path>
    
    # Create sample test image
    python scripts/test_extraction.py --create-sample
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path so we can import app module
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main():
    """Run extraction pipeline with sample or provided image."""
    from app.models import ApplicationData
    from app.preprocessing import preprocess_image
    from app.vision_service import MockVisionService, OpenAIVisionService
    from app.comparison import verify_label
    
    # Check for --create-sample flag
    if "--create-sample" in sys.argv:
        logger.info("Creating sample test image...")
        from PIL import Image, ImageDraw, ImageFont
        
        # Create a simple label-like image
        img = Image.new("RGB", (400, 600), color=(240, 240, 240))
        draw = ImageDraw.Draw(img)
        
        # Simple text (no fancy font)
        text_lines = [
            "PREMIUM VODKA",
            "",
            "Brand: Vodka Premium",
            "Class: Vodka",
            "Producer: Premium Distillery",
            "Country: Russia",
            "ABV: 40%",
            "Net Contents: 750 ml",
            "",
            "WARNING: CONTAINS ALCOHOL",
            "Keep out of reach of children",
            "Do not operate machinery",
        ]
        
        y_offset = 50
        for line in text_lines:
            draw.text((20, y_offset), line, fill=(0, 0, 0))
            y_offset += 40
        
        sample_path = Path(__file__).parent / "sample_label.jpg"
        img.save(sample_path, "JPEG", quality=90)
        logger.info(f"✓ Created sample image: {sample_path}")
        return
    
    # Determine image path and service
    image_path = None
    use_mock = os.getenv("USE_MOCK_VISION", "").lower() == "true"
    
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        image_path = Path(sys.argv[1])
    else:
        # Look for sample image
        sample_path = Path(__file__).parent / "sample_label.jpg"
        if sample_path.exists():
            image_path = sample_path
            logger.info(f"Using sample image: {image_path}")
        else:
            logger.error("No image provided. Usage: python scripts/test_extraction.py <image_path>")
            logger.error("Or create a sample: python scripts/test_extraction.py --create-sample")
            return
    
    # Load image
    if not image_path.exists():
        logger.error(f"Image not found: {image_path}")
        return
    
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    
    logger.info(f"Loaded image: {image_path} ({len(image_bytes)} bytes)")
    
    # Initialize vision service
    if use_mock:
        logger.info("Using MockVisionService (no API call)")
        vision_service = MockVisionService()
    else:
        logger.info("Using OpenAIVisionService (real API)")
        try:
            vision_service = OpenAIVisionService()
        except ValueError as e:
            logger.error(f"Failed to initialize OpenAI: {e}")
            logger.info("Falling back to mock service")
            vision_service = MockVisionService()
    
    # Preprocess image
    logger.info("Preprocessing image...")
    preprocessed = preprocess_image(image_bytes)
    logger.info(f"  Original: {len(image_bytes)} bytes → Preprocessed: {len(preprocessed)} bytes")
    
    # Extract label
    logger.info("Extracting label fields...")
    extracted_label = await vision_service.extract(preprocessed)
    
    print("\n" + "="*60)
    print("EXTRACTED LABEL")
    print("="*60)
    print(json.dumps(extracted_label.model_dump(exclude_none=False), indent=2))
    
    # Run verification
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    
    app_data = ApplicationData(
        brand=extracted_label.brand or "Unknown",
        product_class=extracted_label.product_class or "Unknown",
        producer=extracted_label.producer or "Unknown",
        country=extracted_label.country or "Unknown",
        abv=extracted_label.abv or "Unknown",
        net_contents=extracted_label.net_contents or "Unknown",
        government_warning=extracted_label.government_warning or "Missing"
    )
    
    logger.info("Comparing extracted label against application data...")
    result = verify_label(app_data, extracted_label)
    
    print(f"\nOverall Status: {result.overall_status}")
    print(f"Summary: {result.summary}\n")
    
    print("Field Results:")
    for fr in result.field_results:
        status_icon = "✓" if fr.status == "PASS" else "✗"
        print(f"  {status_icon} {fr.field_name}")
        print(f"    Expected: {fr.expected}")
        print(f"    Extracted: {fr.extracted}")
        if fr.score is not None:
            print(f"    Score: {fr.score:.1f}%")
        print(f"    Message: {fr.message}\n")


if __name__ == "__main__":
    asyncio.run(main())
