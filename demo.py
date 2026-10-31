import json
import tempfile
from pathlib import Path
from metered_api import Meter, QuotaExceeded

with tempfile.TemporaryDirectory() as directory:
    meter = Meter(Path(directory) / "demo.db", clock=lambda: 1000)
    meter.register("demo-client", 3)
    print(meter.charge("demo-client", "demo-event", units=2))
    print(meter.charge("demo-client", "demo-event", units=2))
    try:
        meter.charge("demo-client", "too-large", units=2)
    except QuotaExceeded:
        print("Expected quota rejection")
    print(json.dumps(meter.status("demo-client"), indent=2))
    print(meter.export_csv("demo-client"))
