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
ROOM_STROKE = 3
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


def room_size(space):
    """(width, depth) of the frame in inches. For walls the depth is the height."""
    width = float(space.dims['x'])
    depth = float(space.dims.get('y') or space.dims['x'])
    return width, depth


def guide_size(space):
    """Pixel size of the guide image: the aspect ratio the model will render at,
    with the long side at GUIDE_LONG_SIDE."""
    a, b = (int(n) for n in aspect_ratio(space).split(':'))
    if a >= b:
        return GUIDE_LONG_SIDE, max(1, round(GUIDE_LONG_SIDE * b / a))
    return max(1, round(GUIDE_LONG_SIDE * a / b)), GUIDE_LONG_SIDE


def layout_guide_png(space, items):
    """The room fills the whole image (no margin, no text). Inches map to pixels
    per axis, so a snapped aspect ratio stretches the room slightly."""
    width_in, depth_in = room_size(space)
    img_w, img_h = guide_size(space)
    sx = img_w / max(width_in, 1e-9)
    sy = img_h / max(depth_in, 1e-9)
    img = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img)

    grid_color = (225, 225, 225)
    x = GRID_STEP_IN
    while x < width_in - 1e-9:
        gx = round(x * sx)
        draw.line([(gx, 0), (gx, img_h - 1)], fill=grid_color, width=1)
        x += GRID_STEP_IN
    y = GRID_STEP_IN
    while y < depth_in - 1e-9:
        gy = round(y * sy)
        draw.line([(0, gy), (img_w - 1, gy)], fill=grid_color, width=1)
        y += GRID_STEP_IN

    # PIL draws the stroke inward from the box, so the stroke is centered half
    # its width inside the image edge and the room outline stays fully visible.
    draw.rectangle([0, 0, img_w - 1, img_h - 1], outline=(40, 40, 40), width=ROOM_STROKE)

    for index, item in enumerate(items):
        x0, y0 = item['x'] * sx, item['y'] * sy
        x1, y1 = (item['x'] + item['w']) * sx, (item['y'] + item['h']) * sy
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

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


# Spatial facts: pure functions over item dicts (x, y, w, h, number) and the
# frame size in inches. Used to tell the model the true proportions and gaps.

EPS = 1e-6
SIDES = ('left', 'right', 'top', 'bottom')


def _pct(value, total):
    return round(100.0 * value / total, 1) if total > 0 else 0.0


def _edges(item):
    return item['x'], item['x'] + item['w'], item['y'], item['y'] + item['h']


def item_facts(item, width, depth):
    """Frame percentages, center, size and wall distances of one item."""
    left, right, top, bottom = _edges(item)
    return {
        'left_pct': _pct(left, width),
        'right_pct': _pct(right, width),
        'top_pct': _pct(top, depth),
        'bottom_pct': _pct(bottom, depth),
        'center_x_pct': _pct((left + right) / 2, width),
        'center_y_pct': _pct((top + bottom) / 2, depth),
        'width_pct': _pct(item['w'], width),
        'depth_pct': _pct(item['h'], depth),
        'wall_in': {
            'left': round(max(0.0, left), 2),
            'right': round(max(0.0, width - right), 2),
            'top': round(max(0.0, top), 2),
            'bottom': round(max(0.0, depth - bottom), 2),
        },
    }


def clear_gaps(item, items, width, depth):
    """Nearest clear gap in inches from `item` in each direction. Only
    neighbours overlapping the item in the perpendicular axis count; otherwise
    the wall. Returns {side: {'inches', 'pct', 'to'}} where `to` is 'wall' or
    the neighbour's number and `pct` is relative to the gap's own dimension."""
    left, right, top, bottom = _edges(item)
    best = {
        'left': [max(0.0, left), 'wall'],
        'right': [max(0.0, width - right), 'wall'],
        'top': [max(0.0, top), 'wall'],
        'bottom': [max(0.0, depth - bottom), 'wall'],
    }

    def consider(side, gap, other):
        if gap < best[side][0] - EPS:
            best[side] = [max(0.0, gap), other['number']]

    for other in sorted(items, key=lambda i: i['number']):
        if other['number'] == item['number']:
            continue
        o_left, o_right, o_top, o_bottom = _edges(other)
        if min(bottom, o_bottom) - max(top, o_top) > EPS:
            if o_right <= left + EPS:
                consider('left', left - o_right, other)
            if o_left >= right - EPS:
                consider('right', o_left - right, other)
        if min(right, o_right) - max(left, o_left) > EPS:
            if o_bottom <= top + EPS:
                consider('top', top - o_bottom, other)
            if o_top >= bottom - EPS:
                consider('bottom', o_top - bottom, other)

    return {
        side: {
            'inches': round(gap, 2),
            'pct': _pct(gap, width if side in ('left', 'right') else depth),
            'to': to,
        }
        for side, (gap, to) in best.items()
    }


def empty_bands(items, width, depth, axis):
    """Empty full-width bands (axis 'y': rows no item touches) or full-depth
    bands (axis 'x': columns no item touches) as (start, end) inches."""
    total = depth if axis == 'y' else width
    spans = []
    for item in items:
        left, right, top, bottom = _edges(item)
        lo, hi = (top, bottom) if axis == 'y' else (left, right)
        lo, hi = max(0.0, lo), min(total, hi)
        if hi - lo > EPS:
            spans.append((lo, hi))
    spans.sort()
    bands = []
    cursor = 0.0
    for lo, hi in spans:
        if lo - cursor > EPS:
            bands.append((round(cursor, 2), round(lo, 2)))
        cursor = max(cursor, hi)
    if total - cursor > EPS:
        bands.append((round(cursor, 2), round(total, 2)))
    return bands


def covered_pct(items, width, depth):
    """Percent of the room area covered by at least one item (overlaps counted once)."""
    area = width * depth
    if area <= 0:
        return 0.0
    rects = []
    for item in items:
        left, right, top, bottom = _edges(item)
        rect = (max(0.0, left), min(width, right), max(0.0, top), min(depth, bottom))
        if rect[1] - rect[0] > EPS and rect[3] - rect[2] > EPS:
            rects.append(rect)
    xs = sorted({0.0, width, *(v for r in rects for v in r[:2])})
    covered = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        spans = sorted(
            (r[2], r[3]) for r in rects if r[0] < x1 - EPS and r[1] > x0 + EPS)
        length = 0.0
        cursor = 0.0
        for lo, hi in spans:
            lo = max(lo, cursor)
            if hi > lo:
                length += hi - lo
                cursor = hi
        covered += (x1 - x0) * length
    return round(100.0 * covered / area, 1)


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
