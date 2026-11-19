"""CPU as a real choice for the speech decoder and for Polish."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dictation_engine import (
    DEFAULT_CONFIG, DictationApp, ParakeetNPU, create_model, validate_config,
)


def test_defaults_keep_the_current_devices():
    assert DEFAULT_CONFIG["decoder_device"] == "GPU"
    assert DEFAULT_CONFIG["cleanup_device"] == "GPU"
    assert DEFAULT_CONFIG["device_auto"] is True


@pytest.mark.parametrize("key", ["decoder_device", "cleanup_device"])
def test_decoder_and_cleanup_device_accept_gpu_and_cpu_only(key):
    validate_config({**DEFAULT_CONFIG, key: "CPU"})
    validate_config({**DEFAULT_CONFIG, key: "GPU"})
    for bad in ("NPU", "TPU"):
        with pytest.raises(ValueError, match=key):
            validate_config({**DEFAULT_CONFIG, key: bad})


def _fake_openvino(compiled):
    class Core:
        def read_model(self, path):
            return MagicMock()

        def compile_model(self, model, device, props=None):
            compiled.append(device)
            return MagicMock()
    return types.SimpleNamespace(Core=Core)


def _load_parakeet(tmp_path, **kwargs):
    (tmp_path / "nemo128.onnx").write_bytes(b"x")
    compiled = []
    ort = types.SimpleNamespace(InferenceSession=MagicMock())
    with patch.dict(sys.modules, {"openvino": _fake_openvino(compiled), "onnxruntime": ort}), \
            patch.object(ParakeetNPU, "_load_vocab"):
        model = ParakeetNPU(tmp_path, device="CPU", **kwargs)
    return model, compiled


def test_parakeet_decoder_defaults_to_gpu(tmp_path):
    model, compiled = _load_parakeet(tmp_path)
    assert compiled == ["CPU", "GPU"]
    assert model.decoder_device == "GPU"


def test_parakeet_decoder_runs_on_the_requested_device(tmp_path):
    model, compiled = _load_parakeet(tmp_path, decoder_device="CPU")
    assert compiled == ["CPU", "CPU"]
    assert model.decoder_device == "CPU"


def test_create_model_passes_decoder_device_to_parakeet_only():
    with patch("dictation_engine.ParakeetNPU") as parakeet, patch("dictation_engine.WhisperNPU") as whisper:
        create_model(Path("/fake"), "CPU", "parakeet", decoder_device="CPU")
        create_model(Path("/fake"), "CPU", "whisper", decoder_device="CPU")
    parakeet.assert_called_once_with(Path("/fake"), device="CPU", decoder_device="CPU")
    whisper.assert_called_once_with(Path("/fake"), device="CPU")


@patch("model_store.verify")
@patch("dictation_engine.create_model")
@patch("dictation_engine.setup_model")
def test_ensure_model_passes_a_non_default_decoder_device(mock_setup, mock_create, _verify):
    mock_setup.return_value = Path("/fake/parakeet")
    config = {**DEFAULT_CONFIG, "model_size": "parakeet", "device": "CPU", "decoder_device": "CPU"}
    DictationApp(config).ensure_model()
    mock_create.assert_called_once_with(
        Path("/fake/parakeet"), device="CPU", backend="parakeet", decoder_device="CPU")


def _load_cleanup(tmp_path, config, fail_first=False):
    (tmp_path / "openvino_model.xml").write_text("x")
    devices = []

    def pipeline(path, device, **kwargs):
        devices.append(device)
        if fail_first and len(devices) == 1:
            raise RuntimeError("prompt lookup refused")
        pipe = MagicMock()
        pipe.get_tokenizer.return_value.apply_chat_template.return_value = "a<think>\n\n</think>"
        return pipe
    genai = types.SimpleNamespace(LLMPipeline=pipeline)
    from cleanup import OpenVINOCleanup
    with patch.dict(sys.modules, {"openvino_genai": genai}), patch("model_store.verify"):
        OpenVINOCleanup(tmp_path, config)
    return devices


def test_cleanup_loads_on_gpu_by_default(tmp_path):
    assert _load_cleanup(tmp_path, {}) == ["GPU"]


def test_cleanup_loads_on_the_configured_device_including_the_retry(tmp_path):
    assert _load_cleanup(tmp_path, {"cleanup_device": "CPU"}) == ["CPU"]
    assert _load_cleanup(tmp_path, {"cleanup_device": "CPU"}, fail_first=True) == ["CPU", "CPU"]


def test_launch_device_picks_the_model_default_only_when_device_auto_is_on():
    from dictation_engine import launch_device
    assert launch_device({"model_size": "parakeet", "device": "CPU", "device_auto": True}) == "GPU"
    assert launch_device({"model_size": "parakeet", "device": "CPU"}) == "GPU"
    assert launch_device({"model_size": "parakeet", "device": "CPU", "device_auto": False}) == "CPU"
    assert launch_device({"model_size": "parakeet", "device": "NPU", "device_auto": False}) == "NPU"


def test_cleanup_log_names_the_real_device():
    for device in ("GPU", "CPU"):
        app = DictationApp({**DEFAULT_CONFIG, "probe_text_field": False, "cleanup_device": device})
        lines = []
        app.log = lines.append
        app.ensure_model = lambda: None
        app._cleanup = MagicMock(load_error=None)
        app._cleanup.model.prompt_lookup = True
        app._cleanup.model.device = device
        app._cleanup.model.device_notes = []
        app._load_model_background()
        assert any(f"on {device};" in line for line in lines), lines


def test_cleanup_and_parakeet_use_the_resolved_gpu(tmp_path, monkeypatch):
    import ov_devices
    monkeypatch.setattr(ov_devices, "resolve_device",
                        lambda device, core=None, log=print: "CPU" if device == "GPU" else device)
    assert _load_cleanup(tmp_path, {"cleanup_device": "GPU"}) == ["CPU"]
    model, compiled = _load_parakeet(tmp_path)
    assert compiled == ["CPU", "CPU"]
    assert model.decoder_device == "CPU"
