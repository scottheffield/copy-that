from unittest.mock import Mock
import numpy as np
from dictation_engine import ParakeetModel


def test_word_times_follow_encoder_frames_without_changing_detokenization():
    model = ParakeetModel.__new__(ParakeetModel)
    model.vocab = {1: " Hello", 2: " world", 3: "."}
    def decoder(inputs):
        previous = int(inputs["targets"][0, 0])
        token = {model.BLANK_IDX: 1, 1: 2, 2: 3}[previous]
        output = np.zeros(model.VOCAB_SIZE + 5)
        output[token] = 1
        output[model.VOCAB_SIZE + 3] = 1
        return {"outputs": output, "output_states_1": inputs["input_states_1"],
                "output_states_2": inputs["input_states_2"]}
    model.dec_compiled = decoder
    encoded = np.zeros((1, model.ENC_DIM, 9))
    assert model._tdt_greedy_decode(encoded, 9) == "Hello world."
    timed = model._tdt_greedy_decode(encoded, 9, timed=True)
    assert [word.text for word in timed] == ["Hello", "world."]
    assert [word.time for word in timed] == [0, .24]


def test_encoder_receives_preprocessor_length_instead_of_padded_shape():
    model = ParakeetModel.__new__(ParakeetModel)
    model.device, model.decoder_device = "NPU", "GPU"
    model._preprocess = Mock(return_value=(np.zeros((1, 128, 101), dtype=np.float32),
                                          np.array([100], dtype=np.int64)))
    model.enc_compiled = Mock(return_value={"outputs": np.zeros((1, 1024, 200)),
                                            "encoded_lengths": np.array([13])})
    model._tdt_greedy_decode = Mock(return_value="hello")
    assert model._transcribe_window(np.zeros(16000, dtype=np.float32)) == "hello"
    inputs = model.enc_compiled.call_args.args[0]
    assert inputs["length"].tolist() == [100]
    assert inputs["audio_signal"].shape == (1, 128, 1600)
