import speaker_models


def test_dev_build_reads_runtime_folder():
    assert speaker_models.models_dir() == speaker_models.DEV_DIR


def test_problem_reports_missing_and_wrong_size(tmp_path):
    assert "missing" in speaker_models.problem(tmp_path)
    for name, (_, _, _, size, _) in speaker_models.MODELS.items():
        (tmp_path / name).write_bytes(b"x")
    assert "wrong size" in speaker_models.problem(tmp_path)


def test_model_paths_order(tmp_path):
    seg, emb = speaker_models.model_paths(tmp_path)
    assert (seg.name, emb.name) == ("segmentation.onnx", "embedding.onnx")
