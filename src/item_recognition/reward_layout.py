"""Small shared checks for unrevealed rewards; no library or device access."""
from functools import lru_cache
import json
from pathlib import Path

import cv2


@lru_cache(maxsize=32)
def _template(path, stamp):
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise OSError(f'Missing reward template: {path}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def unrevealed_label(tile, profile, directory):
    for template in profile.get('placeholders', []):
        path = Path(directory) / template['path']
        target = _template(str(path), path.stat().st_mtime_ns)
        current = cv2.resize(tile, (target.shape[1], target.shape[0]))
        score = float(cv2.matchTemplate(current, target, cv2.TM_CCOEFF_NORMED)[0, 0])
        if score >= profile.get('placeholder_threshold', .94):
            return template['label']
    return None


def has_unrevealed_rewards(frame, directory):
    """Inspect only known package backs in an already-verified results frame."""
    profile = json.loads((Path(directory) / 'profiles.json').read_text('utf-8'))
    if frame.shape[:2] != (profile['height'], profile['width']):
        return False
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    for x, y, width, height in profile['grid_boxes']:
        if unrevealed_label(rgb[y:y+height, x:x+width], profile, directory):
            return True
    return False
