"""Local pipeline smoke test on fixed text and silence, never microphone capture."""
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dictation_engine import MODEL_DIR, CONFIG_DIR, load_config, WhisperModel
from cleanup import Cleanup
import numpy as np


def main():
    config = load_config()
    config["cleanup_enabled"] = True
    config["cleanup_timeout_seconds"] = 20
    if len(sys.argv) > 1:
        config["cleanup_model_path"] = str(MODEL_DIR / sys.argv[1])
    print("Loading resident speech model", flush=True)
    speech = WhisperModel(MODEL_DIR / "whisper-base-openvino", "NPU")
    print("Loading cleanup model", flush=True)
    cleaner = Cleanup(config)
    cleaner.load()
    if cleaner.load_error:
        raise RuntimeError(f"Cleanup failed to load: {cleaner.load_error}")
    silence = speech.transcribe(np.zeros(16000, dtype=np.float32))
    print("Both models loaded; non-thinking template verified", flush=True)
    inputs = [
        "please check the current build and tell me why it failed",
        "please do not deploy this to production until I approve it",
        "send it on Tuesday sorry Wednesday but do not change the time",
        "please update foo_bar in /tmp/main.py and keep the current public interface",
    ]
    results = []
    for text in inputs:
        for iteration in range(2):
            start = time.monotonic()
            result = cleaner.run(text, "cursor.exe")
            elapsed = time.monotonic() - start
            results.append({"input": text, "output": result.text, "fallback": result.fallback,
                            "seconds": elapsed, "iteration": iteration})
            print(f"Cleanup {elapsed:.2f}s, fallback={result.fallback}: {result.text}", flush=True)
    report = {"speech_device": speech.device, "model_path": config["cleanup_model_path"],
              "silence_direct_recognizer": silence, "note": "Fixed synthetic text, not a personal dictation accuracy benchmark", "results": results}
    path = CONFIG_DIR / f"smoke-{Path(config['cleanup_model_path']).name}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
