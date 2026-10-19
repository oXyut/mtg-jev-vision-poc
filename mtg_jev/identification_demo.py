"""Self-authored text and shapes for real local OCR, never MTG recognition evidence."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .geometry import COORDINATES, Box


def generate_identification_demo(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    image = Image.new('RGB', (960, 720), '#243044')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    cards = [{'id': 'fictional-amber', 'name': 'Amber Harbor'},
             {'id': 'fictional-quiet', 'name': 'Quiet Orchard'},
             {'id': 'fictional-azure', 'name': 'Azure Workshop'}]
    detections, objects = [], []
    for index, (card, left) in enumerate(zip(cards, (40, 520))):
        draw.rectangle((left, 80, left + 400, 640), fill='white', outline='#a7d5c9', width=4)
        draw.text((left + 22, 110), card['name'].upper(), font=font, fill='black')
        draw.rectangle((left + 24, 220, left + 376, 490), fill=('#a7d5c9', '#a9b7d6')[index])
        box = Box(left / 960, 80 / 720, (left + 400) / 960, 640 / 720).json()
        detections.append({'id': f'card-{index + 1}', 'box': box})
        objects.append({'box': box, 'card_id': card['id']})
    frame = {'schema_version': 1, 'coordinate_system': COORDINATES,
             'image': {'width': 960, 'height': 720}}
    prediction = {**frame, 'mode': 'synthetic_geometry',
                  'evidence': 'known_synthetic_boxes_not_jev_detection', 'detections': detections}
    truth = {**frame, 'objects': objects}
    catalog = {'schema_version': 1, 'coverage': 'reference',
               'coverage_scope': 'three fictional names in this generated fixture only',
               'provenance': {'source': 'self-authored fictional fixture', 'license': 'self-authored test data',
                              'retrieved_at': '2026-10-19', 'rights_confirmed': True}, 'cards': cards}
    image.save(root / 'photo.png')
    for name, value in [('detections.json', prediction), ('truth.json', truth), ('catalog.json', catalog)]:
        (root / name).write_text(json.dumps(value, indent=2) + '\n')
    (root / 'PROVENANCE.txt').write_text(
        'Self-authored fictional titles and plain geometric shapes; no MTG artwork or database.\n'
        'Boxes are supplied from known geometry. Local OCR can read these synthetic titles;\n'
        'this does not measure Jev detection or real MTG identification accuracy.\n')
