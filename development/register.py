"""Development-only registration of visually inspected safe screen patterns."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from farm import ROOT, CATALOG, read_json, write_json


def register(name, source, regions, action=None, expected=None, point=None, priority=0,
             correlation=.96, difference=12, notes="", points=None, recovery=False):
    source = Path(source)
    if not source.is_absolute():
        source = ROOT / source
    frame = cv2.imread(str(source))
    if frame is None:
        raise ValueError(f"Missing source {source}")
    folder = ROOT / "patterns" / name.lower()
    folder.mkdir(parents=True, exist_ok=True)
    anchors = []
    for i, region in enumerate(regions):
        x1,y1,x2,y2 = region
        path = folder / f"{i}.png"
        cv2.imwrite(str(path), frame[y1:y2,x1:x2])
        anchors.append({"region": region, "template": str(path.relative_to(ROOT)),
                        "correlation": correlation, "difference": difference})
    spec = {"name": name, "anchors": anchors, "priority": priority,
            "verified_safe": bool(action), "notes": notes, "recovery": recovery}
    if action:
        spec["action"] = {"id": action, "kind": "swipe" if points else "tap", "expected": expected,
                          **({"points": points} if points else {"point": point})}
    catalog = read_json(CATALOG, {"states": []})
    catalog["states"] = [s for s in catalog["states"] if s["name"] != name] + [spec]
    write_json(CATALOG, catalog)
    print(f"Registered {name}")


if __name__ == "__main__":
    register(**json.loads(sys.argv[1]))
