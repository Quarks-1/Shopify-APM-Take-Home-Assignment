from . import layout

SURFACE_BY_TYPE = {'floor': 'floor', 'shelf': 'surface', 'other': 'surface', 'wall': 'wall'}
KIND_BY_TYPE = {'floor': 'floor', 'shelf': 'shelf', 'other': 'space', 'wall': 'wall'}

LARGE_BAND_PCT = 15.0
MIN_BAND_PCT = 3.0


def _inches(value):
    text = f'{float(value):.2f}'.rstrip('0').rstrip('.')
    return text or '0'


def _pct(value):
    text = f'{float(value):.1f}'.rstrip('0').rstrip('.')
    return text or '0'


def _side_names(wall):
    return {
        'left': 'left',
        'right': 'right',
        'top': 'ceiling' if wall else 'top',
        'bottom': 'floor' if wall else 'bottom',
    }


def _dims_text(item, wall):
    """Object dimensions. Plan-view spaces list W x D only: heights make the model
    render perspective, so they are deliberately left out."""
    w, h = _inches(item['w']), _inches(item['h'])
    dims = item.get('dims') or {}
    if wall:
        text = f'{w} W x {h} H in'
        if dims.get('y') is not None:
            text += f", projecting {_inches(dims['y'])} in from the wall"
        return text
    return f'{w} W x {h} D in'


def _band_lines(items, width, depth, wall, surface):
    """One sentence per empty full-width / full-depth band worth mentioning."""
    names = _side_names(wall)
    lines = []
    for axis, total, origin in (('y', depth, names['top']), ('x', width, 'left')):
        for start, end in layout.empty_bands(items, width, depth, axis):
            share = 100.0 * (end - start) / total
            if share < MIN_BAND_PCT:
                continue
            label = f'Open {surface}' if share >= LARGE_BAND_PCT else f'Clear strip of {surface}'
            dimension = 'width' if axis == 'x' else ('height' if wall else 'depth')
            lines.append(
                f'- {label}: no objects between {_inches(start)} in and {_inches(end)} in '
                f'from the {origin} ({_pct(share)}% of the room {dimension}).'
            )
    return lines


def _gap_text(side, gap, names):
    to = 'the wall' if gap['to'] == 'wall' else f"item {gap['to']}"
    if gap['to'] == 'wall' and side in ('top', 'bottom') and names[side] != side:
        to = f'the {names[side]}'
    return f"{names[side]} {_inches(gap['inches'])} in ({_pct(gap['pct'])}%) to {to}"


def _scale_section(space, items, surface):
    """The "Scale and spacing" block: frame, scale, open bands, binding rules."""
    wall = space.type == 'wall'
    width, depth = layout.room_size(space)
    if wall:
        orientation = (
            'Orientation: the top edge of the frame is the ceiling and the bottom edge is the '
            'floor; left and right are as seen when facing the wall.'
        )
    else:
        orientation = (
            'Orientation: the top edge of the frame is the top of the plan (0 in), the bottom '
            f'edge is {_inches(depth)} in from the top; the left edge is 0 in and the right '
            f'edge is {_inches(width)} in from the left.'
        )
    lines = [
        'Scale and spacing (must be respected):',
        'The room outline in image 1 IS the frame: the room edges coincide with the image '
        'edges. Your output must use the same framing, with the room running edge to edge and '
        'no extra margin, border or surroundings.',
        orientation,
        f'Room: {_inches(width)} in wide by {_inches(depth)} in '
        f'{"tall" if wall else "deep"}. Scale: 1 inch = {100 / width:.2f}% of the image width, '
        f'{100 / depth:.2f}% of the image height; the grid in image 1 has a line every '
        f'{layout.GRID_STEP_IN} in. Objects cover {_pct(layout.covered_pct(items, width, depth))}% '
        f'of the {surface}.',
    ]
    bands = _band_lines(items, width, depth, wall, surface)
    if bands:
        lines.append(f'Empty {surface} that must stay empty:')
        lines.extend(bands)
    lines.append(
        'Rules: do NOT enlarge objects and do NOT move them toward each other. Preserve every '
        'open gap above exactly. Each box\'s size relative to the room (its percentages below) '
        'is binding.'
    )
    return lines


def _item_lines(item, items, space, photo_number):
    wall = space.type == 'wall'
    width, depth = layout.room_size(space)
    names = _side_names(wall)
    facts = layout.item_facts(item, width, depth)
    gaps = layout.clear_gaps(item, items, width, depth)

    title = f" — {item['productTitle']}" if item.get('productTitle') else ''
    details = [item['type']]
    if item.get('material'):
        details.append(item['material'])
    if item.get('style'):
        details.append(item['style'])
    dims_text = _dims_text(item, wall)
    walls = facts['wall_in']
    lines = [
        f"Item {item['number']}: {item['name']}{title} ({', '.join(details)}), "
        f"footprint {_inches(item['w'])} x {_inches(item['h'])} in at "
        f"{_inches(item['x'])} in from the left and {_inches(item['y'])} in from the top.",
        f'  - Size: {dims_text}; covers {_pct(facts["width_pct"])}% of the image width x '
        f'{_pct(facts["depth_pct"])}% of the image height.',
        f'  - Frame position: left edge {_pct(facts["left_pct"])}%, right edge '
        f'{_pct(facts["right_pct"])}%, top edge {_pct(facts["top_pct"])}%, bottom edge '
        f'{_pct(facts["bottom_pct"])}%; center at {_pct(facts["center_x_pct"])}% across, '
        f'{_pct(facts["center_y_pct"])}% down.',
        '  - Distance to the edge: ' + ', '.join(
            f'{names[side]} {_inches(walls[side])} in' for side in layout.SIDES) + '.',
        '  - Nearest clear gap: ' + '; '.join(
            _gap_text(side, gaps[side], names) for side in layout.SIDES) + '.',
    ]
    if photo_number is not None:
        lines.append(
            f"  - Its product photo is image {photo_number}; reproduce this exact product "
            f"(shape, color, material, details) seen from above."
        )
    else:
        lines.append(f"  - No photo: render a plausible {item['type']} matching the description.")
    return lines


def build_prompt(space, items, photo_numbers):
    """Return (prompt, photo_index_by_number). Photo k is the k-th image after
    the layout guide (image 1), so photo indexes start at 2."""
    wall = space.type == 'wall'
    width = _inches(space.dims['x'])
    surface = SURFACE_BY_TYPE.get(space.type, 'surface')

    if wall:
        height = _inches(space.dims.get('y') or space.dims['x'])
        opening = (
            f'Create a photorealistic, perfectly flat, straight-on front elevation (zero '
            f'perspective) of a wall that is {width} in wide by {height} in tall.'
        )
    else:
        depth = _inches(space.dims.get('y') or space.dims['x'])
        kind = KIND_BY_TYPE.get(space.type, 'space')
        opening = (
            f'Create a photorealistic, perfectly flat, true-orthographic (zero perspective: a '
            f'tall object must look exactly as wide as its footprint, like an architectural '
            f'plan rendering) top-down view of a {kind} called "{space.name}" that is '
            f'{width} in wide by {depth} in deep.'
        )

    guide = (
        'Image 1 is the layout guide: the room outline with numbered colored boxes. '
        'Place each item exactly inside its numbered box, matching the box\'s position, size '
        'and proportions. The colors and numbers are only markers; do not draw them, the grid, '
        'or any text.'
    )

    photo_index = {}
    next_index = 2
    for item in items:
        if item['number'] in photo_numbers:
            photo_index[item['number']] = next_index
            next_index += 1

    lines = [opening, guide]
    lines.extend(_scale_section(space, items, surface))
    lines.append('Items:')
    for item in items:
        lines.extend(_item_lines(item, items, space, photo_index.get(item['number'])))

    lines.append(
        f'Leave every area outside the boxes as empty {surface}; do not add any other furniture '
        f'or objects. Even, soft daylight; neutral {surface} finish; no people, no labels, '
        f'no watermark.'
    )
    if wall:
        lines.append(
            'The image shows ONLY the wall surface, edge to edge: no floor, no ceiling, no '
            'adjoining walls visible. The frame is a tight crop of the wall, so every object\'s '
            'size in the image is exactly its footprint percentage given above and no larger.'
        )
    else:
        lines.append(
            f'The image shows ONLY the {surface} surface, edge to edge: no walls, no '
            f'baseboards, no windows, no ceiling, no wall shadows visible. The frame is a '
            f'tight crop of the {surface}, so every object\'s size in the image is exactly its '
            f'footprint percentage given above and no larger.'
        )
    return '\n'.join(lines), photo_index
