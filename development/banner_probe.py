"""Supervised, user-authorized banner tap experiment; never used by the runner."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from farm import Device, Detector, STOP, ROOT, lock, event

x, y = map(int, sys.argv[1:3])
if not (150 <= x <= 780 and 870 <= y <= 1010):
    raise ValueError("Outside visually inspected All Scrolls banner")
with lock("worker"):
    device = Device()
    device.verify()
    detector = Detector()
    frame = device.capture()
    name, spec, _ = detector.classify(frame)
    if name != "SCROLLS_BANNER":
        raise RuntimeError(f"Banner not verified: {name}")
    STOP.unlink(missing_ok=True)
    try:
        event("supervised_banner_probe", point=[x,y], authorization="User requested multiple taps on center, artwork and text")
        for attempt in range(int(sys.argv[3]) if len(sys.argv) > 3 else 1):
            if attempt >= 5:
                break
            if detector.classify(device.capture())[0] != "SCROLLS_BANNER":
                break
            device.command(["shell", "input", "tap", str(x), str(y)])
        time.sleep(2)
        frame = device.capture()
        path = ROOT / "development" / f"probe_{x}_{y}.png"
        cv2.imwrite(str(path), frame)
        print(detector.classify(frame)[0], path)
    finally:
        STOP.write_text("Supervised probe complete; background remains stopped")
