SURFACE_BY_TYPE = {'floor': 'floor', 'shelf': 'surface', 'other': 'surface', 'wall': 'wall'}
KIND_BY_TYPE = {'floor': 'floor', 'shelf': 'shelf', 'other': 'space', 'wall': 'wall'}


def _inches(value):
    text = f'{float(value):.2f}'.rstrip('0').rstrip('.')
    return text or '0'


def build_prompt(space, items, photo_numbers):
    """Return (prompt, photo_index_by_number). Photo k is the k-th image after
    the layout guide (image 1), so photo indexes start at 2."""
    wall = space.type == 'wall'
    width = _inches(space.dims['x'])
    surface = SURFACE_BY_TYPE.get(space.type, 'surface')

    if wall:
        height = _inches(space.dims.get('y') or space.dims['x'])
        opening = (
            f'Create a photorealistic straight-on front elevation of a wall that is '
            f'{width} in wide by {height} in tall.'
        )
    else:
        depth = _inches(space.dims.get('y') or space.dims['x'])
        kind = KIND_BY_TYPE.get(space.type, 'space')
        opening = (
            f'Create a photorealistic top-down (bird\'s-eye, orthographic, straight down) '
            f'view of a {kind} called "{space.name}" that is {width} in wide by {depth} in deep.'
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
    for item in items:
        title = f" — {item['productTitle']}" if item.get('productTitle') else ''
        details = [item['type']]
        if item.get('material'):
            details.append(item['material'])
        if item.get('style'):
            details.append(item['style'])
        line = (
            f"Item {item['number']}: {item['name']}{title} ({', '.join(details)}), "
            f"footprint {_inches(item['w'])} x {_inches(item['h'])} in at "
            f"{_inches(item['x'])} in from the left and {_inches(item['y'])} in from the top."
        )
        if item['number'] in photo_index:
            line += (
                f" Its product photo is image {photo_index[item['number']]}; reproduce this "
                f"exact product (shape, color, material, details) seen from above."
            )
        else:
            line += f" No photo: render a plausible {item['type']} matching the description."
        lines.append(line)

    lines.append(
        f'Leave every area outside the boxes as empty {surface}; do not add any other furniture '
        f'or objects. Even, soft daylight; neutral {surface} finish; no people, no labels, '
        f'no watermark.'
    )
    return '\n'.join(lines), photo_index
