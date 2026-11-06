"""Send one request to a running NBR ADB Relay and print the response.

    python client.py ping
    python client.py screencap
    python client.py getprop key=ro.product.model
    python client.py timing count=5
"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def request(command, timeout=30, **options):
    request_id = "c" + uuid.uuid4().hex[:16]
    path = HERE / "requests" / f"{request_id}.json"
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps({"command": command, **options}), encoding="utf-8")
    partial.replace(path)
    response = HERE / "responses" / f"{request_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if response.exists():
            return json.loads(response.read_text(encoding="utf-8"))
        time.sleep(0.1)
    raise TimeoutError("No response; is the relay running? (check status.json)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    options = {}
    for item in sys.argv[2:]:
        key, _, value = item.partition("=")
        options[key] = int(value) if value.isdigit() else value
    print(json.dumps(request(sys.argv[1], **options), indent=2))
