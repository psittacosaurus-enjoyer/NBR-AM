"""Register only visually inspected Getting Rich Quick [B] patterns."""
import copy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from farm import ROOT, read_json, write_json

CATALOG = ROOT / 'patterns/rich_b.json'

def register(name, source, regions, action=None, priority=0, recovery=True, notes=''):
    frame = cv2.imread(str(ROOT / source))
    if frame is None:
        raise ValueError(source)
    folder = ROOT / 'patterns/rich_b' / name.lower()
    folder.mkdir(parents=True, exist_ok=True)
    anchors = []
    for index, region in enumerate(regions):
        x1,y1,x2,y2 = region
        path = folder / f'{index}.png'
        cv2.imwrite(str(path), frame[y1:y2,x1:x2])
        anchors.append({'region':region, 'template':str(path.relative_to(ROOT)),
                        'correlation':.96, 'difference':12})
    spec = {'name':name, 'anchors':anchors, 'priority':priority,
            'verified_safe':bool(action), 'recovery':recovery, 'notes':notes}
    if action:
        spec['action'] = copy.deepcopy(action)
    catalog = read_json(CATALOG)
    catalog['states'] = [s for s in catalog['states'] if s['name'] != name] + [spec]
    write_json(CATALOG, catalog)
    return spec
