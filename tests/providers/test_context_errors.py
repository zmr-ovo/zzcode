import json


import pytest



from zzcode.context.budget import (
    ContextOverflowError,
)


def test_http_error_classification_requires_explicit_capacity_code(monkeypatch):
    import io
    import urllib.error
    from zzcode.providers.native import _http

    def failure(code):
        def call(*args, **kwargs):
            raise urllib.error.HTTPError(
                "https://example.invalid",
                400,
                "bad",
                {},
                io.BytesIO(json.dumps({"error": {"code": code}}).encode()),
            )

        return call

    monkeypatch.setattr("urllib.request.urlopen", failure("context_length_exceeded"))
    with pytest.raises(ContextOverflowError):
        _http("https://example.invalid", {}, {}, 1, "test")
    monkeypatch.setattr("urllib.request.urlopen", failure("rate_limit_exceeded"))
    with pytest.raises(RuntimeError) as error:
        _http("https://example.invalid", {}, {}, 1, "test")
    assert not isinstance(error.value, ContextOverflowError)
