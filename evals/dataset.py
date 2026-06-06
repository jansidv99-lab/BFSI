import json
import os

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "golden_dataset.json")


def load_dataset(path=None) -> list[dict]:
    with open(path or _DEFAULT_PATH, encoding="utf-8") as f:
        return json.load(f)
