"""The command-line entry point picks its device the same way the tray app does."""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

import dictation_engine
from dictation_engine import DEFAULT_CONFIG


@contextmanager
def _acquired():
    yield True


def _launch(saved, argv=()):
    with patch.object(dictation_engine, "load_config", return_value={**DEFAULT_CONFIG, **saved}), \
            patch("sys.argv", ["dictation_engine", *argv]), \
            patch("sys.setswitchinterval"), \
            patch("windows_io.single_instance", _acquired), \
            patch.object(dictation_engine, "DictationApp") as app:
        dictation_engine.main()
    return app.call_args.args[0]["device"]


@pytest.mark.parametrize("auto, expected", [(True, "GPU"), (False, "CPU")])
def test_cli_respects_device_auto(auto, expected):
    assert _launch({"model_size": "parakeet", "device": "CPU", "device_auto": auto}) == expected


def test_cli_device_flag_still_wins():
    assert _launch({"model_size": "parakeet", "device": "GPU", "device_auto": True},
                   ["--device", "NPU"]) == "NPU"
