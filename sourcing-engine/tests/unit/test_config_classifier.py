from sourcing.config import Settings


def test_classifier_defaults():
    s = Settings()
    assert s.classifier_provider == "ollama"
    assert s.classifier_model == "qwen2.5:3b"
    assert s.classifier_ollama_url == "http://localhost:11434"
    assert s.classifier_timeout_seconds == 30
    assert s.classifier_batch_size == 10
