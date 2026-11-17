import hashlib
import io
import json
import math

from PIL import Image, ImageDraw, ImageFont

from ..models import Placement

PALETTE = [
    (230, 57, 70),
    (42, 157, 143),
    (244, 162, 97),
    (69, 123, 157),
    (142, 68, 173),
    (233, 196, 106),
    (38, 70, 83),
    (231, 111, 81),
    (106, 153, 78),
    (247, 114, 180),
]

ASPECT_RATIOS = ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9']

GUIDE_LONG_SIDE = 1024
GUIDE_MARGIN = 40
GRID_STEP_IN = 12


def _round2(v):
    return round(float(v), 2)


def footprint(space_type, dims):
    """Plan-view footprint of an object in inches. Walls are drawn on their
    elevation, so their height comes from z first."""
    w = max(1, dims['x'])
    if space_type == 'wall':
        h = max(1, dims.get('z') if dims.get('z') is not None
                else dims.get('y') if dims.get('y') is not None else dims['x'])
    else:
        h = max(1, dims.get('y') if dims.get('y') is not None else dims['x'])
    return w, h


def render_items(space):
    """One entry per placement, numbered from 1 in placement order."""
    placements = (
        Placement.objects.filter(space=space)
        .select_related('object__selected_candidate')
        .order_by('pk')
    )
    items = []
    for placement in placements:
        obj = placement.object
        if obj is None:
            continue
        cand = obj.selected_candidate
        if cand is not None:
            product_key = f'cand_{cand.id}'
            product_title = cand.title
            image_url = cand.image_url or None
        elif obj.owned:
            product_key = obj.owned.get('productId')
            product_title = None
            image_url = None
        else:
            product_key = None
            product_title = None
            image_url = None
        w, h = footprint(space.type, obj.dims)
        items.append({
            'objectId': obj.id,
            'name': obj.name,
            'type': obj.type,
            'material': obj.material,
            'style': obj.style,
            'x': _round2(placement.x),
            'y': _round2(placement.y),
            'w': _round2(w),
            'h': _round2(h),
            'dims': obj.dims,
            'productKey': product_key,
            'productTitle': product_title,
            'imageUrl': image_url,
        })
    for number, item in enumerate(items, start=1):
        item['number'] = number
    return items


def fingerprint(space, items):
    payload = {
        'type': space.type,
        'dims': [space.dims.get('x'), space.dims.get('y')],
        'items': [
            [i['objectId'], i['name'], i['type'], i['productKey'], i['x'], i['y'], i['w'], i['h']]
            for i in sorted(items, key=lambda i: i['objectId'])
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _font(size):
    return ImageFont.load_default(size=size)


def layout_guide_png(space, items):
    width_in = float(space.dims['x'])
    depth_in = float(space.dims.get('y') or space.dims['x'])
    scale = GUIDE_LONG_SIDE / max(width_in, depth_in, 1e-9)
    m = GUIDE_MARGIN
    img_w = round(width_in * scale) + 2 * m
    img_h = round(depth_in * scale) + 2 * m
    img = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img)

    def px(x, y):
        return m + x * scale, m + y * scale

    grid_color = (225, 225, 225)
    x = 0.0
    while x <= width_in + 1e-9:
        gx = round(px(x, 0)[0])
        draw.line([(gx, m), (gx, m + depth_in * scale)], fill=grid_color, width=1)
        x += GRID_STEP_IN
    y = 0.0
    while y <= depth_in + 1e-9:
        gy = round(px(0, y)[1])
        draw.line([(m, gy), (m + width_in * scale, gy)], fill=grid_color, width=1)
        y += GRID_STEP_IN

    draw.rectangle(
        [m, m, round(m + width_in * scale), round(m + depth_in * scale)],
        outline=(40, 40, 40), width=3,
    )

    for index, item in enumerate(items):
        x0, y0 = px(item['x'], item['y'])
        x1, y1 = px(item['x'] + item['w'], item['y'] + item['h'])
        box = [round(x0), round(y0), round(x1), round(y1)]
        draw.rectangle(box, fill=PALETTE[index % len(PALETTE)], outline=(30, 30, 30), width=2)
        box_px = min(x1 - x0, y1 - y0)
        size = int(min(96, max(14, box_px * 0.5)))
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        draw.text(
            (cx, cy), str(item['number']), font=_font(size), fill='white', anchor='mm',
            stroke_width=max(1, size // 12), stroke_fill=(30, 30, 30),
        )

    label_font = _font(22)
    label_color = (60, 60, 60)
    if space.type == 'wall':
        draw.text((img_w / 2, m / 2), 'CEILING', font=label_font, fill=label_color, anchor='mm')
        draw.text((img_w / 2, img_h - m / 2), 'FLOOR', font=label_font, fill=label_color,
                  anchor='mm')
    else:
        draw.text((img_w / 2, m / 2), 'TOP', font=label_font, fill=label_color, anchor='mm')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def aspect_ratio(space):
    width_in = float(space.dims['x'])
    depth_in = float(space.dims.get('y') or 0)
    if width_in <= 0 or depth_in <= 0:
        return '1:1'
    target = math.log(width_in / depth_in)

    def distance(ratio):
        a, b = ratio.split(':')
        return abs(target - math.log(int(a) / int(b)))

    return min(ASPECT_RATIOS, key=distance)
