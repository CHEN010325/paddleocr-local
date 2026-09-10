"""Exercise an already-running CPU WebUI with generated, non-private documents.

Usage: python scripts/smoke-cpu.py --url http://127.0.0.1:8000
No GPU, platform-specific font, user document or pre-existing model cache needed.
"""
import argparse
import io
import json
import time

import fitz
import httpx
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    image = Image.new('RGB', (1200, 600), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    for y, text in [(50, 'PaddleOCR Local release check'),
                    (160, 'Invoice 2026 - Total: 123.45'),
                    (470, 'Footnote: retain this important text.')]:
        draw.text((40, y), text, font=font, fill='black')
    out = io.BytesIO()
    image.save(out, format='PNG')
    png = out.getvalue()
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=600, height=300)
            page.insert_image(page.rect, stream=png)
        pdf = doc.tobytes()
    results = []
    with httpx.Client(timeout=300) as client:
        for name, content, count in [('sample.png', png, 1), ('sample.pdf', pdf, 2)]:
            started = time.monotonic()
            response = client.post(args.url.rstrip('/') + '/api/parse',
                files={'file': (name, content)}, data={'modelId': 'pp-ocrv6'})
            response.raise_for_status()
            data = response.json()
            text = data['markdown'].lower()
            assert '123.45' in text and 'retain this important text' in text, data['markdown']
            assert len(data['layoutParsingResults']) == count
            results.append({'file': name, 'pages': count, 'seconds': round(time.monotonic()-started, 2)})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
