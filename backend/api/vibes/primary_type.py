"""Pick ONE ObjectType group for an inspiration pin from its item noun."""
import re

KEYWORDS = {
    'lighting': ['lamp', 'light', 'sconce', 'chandelier', 'pendant', 'lantern', 'bulb'],
    'rug': ['rug', 'carpet', 'runner', 'doormat', 'mat', 'kilim'],
    'wall-art': [
        'art', 'artwork', 'print', 'poster', 'painting', 'canvas', 'tapestry', 'frame', 'photo',
        'hanging',
    ],
    'storage': [
        'shelf', 'shelving', 'bookcase', 'bookshelf', 'cabinet', 'basket', 'rack', 'organizer',
        'sideboard', 'credenza', 'bin', 'crate', 'cart', 'hutch', 'wardrobe', 'armoire', 'drawer',
    ],
    'furniture': [
        'sofa', 'couch', 'sectional', 'chair', 'armchair', 'stool', 'bench', 'ottoman', 'table',
        'desk', 'bed', 'headboard', 'dresser', 'nightstand', 'console', 'daybed', 'loveseat',
        'futon', 'screen',
    ],
    'decor': [
        'vase', 'plant', 'planter', 'pot', 'pillow', 'cushion', 'throw', 'blanket', 'mirror',
        'candle', 'candlestick', 'bowl', 'tray', 'dish', 'plate', 'clock', 'sculpture', 'figurine',
        'book', 'curtain', 'towel', 'mug', 'coaster', 'dinnerware', 'flatware', 'tableware',
    ],
}
_WORD_TYPE = {word: type_ for type_, words in KEYWORDS.items() for word in words}


def _lookup(word):
    """Type for a word or its singular form (trailing 's' stripped), else None."""
    if word in _WORD_TYPE:
        return _WORD_TYPE[word]
    if word.endswith('s') and not word.endswith('ss'):
        return _WORD_TYPE.get(word[:-1])
    return None


def primary_type(item, categories):
    """One ObjectType value (or 'other'): head noun first, then words right-to-left."""
    words = [w for w in re.split(r'[\s\-]+', (item or '').lower()) if w]
    for word in reversed(words):
        found = _lookup(word)
        if found:
            return found
    categories = list(categories or [])
    if 'decor' in categories:
        return 'decor'
    return categories[0] if categories else 'other'
