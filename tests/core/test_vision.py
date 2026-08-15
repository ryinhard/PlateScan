"""app.core.vision 單元測試：JSON 回應解析、空輸入短路邏輯與備用模型降級。

以 monkeypatch 替換 _get_client()，不實際呼叫 Gemini API。
"""

import pytest
from google.genai import errors as genai_errors

from app.config import settings
from app.core import vision


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.received_contents: list = []

    def generate_content(self, model: str, contents: list):
        self.received_contents.append((model, contents))
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


def _server_error() -> genai_errors.ServerError:
    return genai_errors.ServerError(
        503, {"error": {"message": "This model is currently experiencing high demand."}}
    )


class _FlakyPrimaryModels:
    """主模型第一次呼叫回傳 503，其餘模型正常回應。"""

    def __init__(self, fallback_text: str) -> None:
        self._fallback_text = fallback_text
        self.received_models: list[str] = []

    def generate_content(self, model: str, contents: list):
        self.received_models.append(model)
        if model == settings.gemini_model:
            raise _server_error()
        return _FakeResponse(self._fallback_text)


class _FlakyPrimaryClient:
    def __init__(self, fallback_text: str) -> None:
        self.models = _FlakyPrimaryModels(fallback_text)


class _AlwaysServerErrorModels:
    def generate_content(self, model: str, contents: list):
        raise _server_error()


class _AlwaysServerErrorClient:
    def __init__(self) -> None:
        self.models = _AlwaysServerErrorModels()


async def test_analyze_meal_returns_empty_list_when_no_input():
    assert await vision.analyze_meal([], []) == []


async def test_analyze_meal_parses_plain_json_array(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeClient(
        '[{"name": "雞腿便當", "calories": 650, "carbs_g": 80, "protein_g": 30, "fat_g": 20}]'
    )
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)

    result = await vision.analyze_meal([b"fake-image"], ["雞腿便當"])

    assert result == [
        {"name": "雞腿便當", "calories": 650, "carbs_g": 80, "protein_g": 30, "fat_g": 20}
    ]


async def test_analyze_meal_strips_markdown_code_fence(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeClient('```json\n[{"name": "白飯", "calories": 280, '
                               '"carbs_g": 60, "protein_g": 5, "fat_g": 1}]\n```')
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)

    result = await vision.analyze_meal([b"fake-image"], [])

    assert result == [{"name": "白飯", "calories": 280, "carbs_g": 60, "protein_g": 5, "fat_g": 1}]


async def test_analyze_meal_returns_empty_list_on_invalid_json(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeClient("抱歉，我無法辨識這張照片")
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)

    result = await vision.analyze_meal([b"fake-image"], [])

    assert result == []


async def test_analyze_meal_falls_back_to_secondary_model_on_503(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FlakyPrimaryClient(
        '[{"name": "炒飯", "calories": 600, "carbs_g": 90, "protein_g": 15, "fat_g": 18}]'
    )
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)

    result = await vision.analyze_meal([b"fake-image"], [])

    assert result == [{"name": "炒飯", "calories": 600, "carbs_g": 90, "protein_g": 15, "fat_g": 18}]
    assert fake_client.models.received_models == [settings.gemini_model, settings.gemini_fallback_model]


async def test_analyze_meal_raises_when_fallback_model_also_fails(monkeypatch: pytest.MonkeyPatch):
    fake_client = _AlwaysServerErrorClient()
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)

    with pytest.raises(genai_errors.ServerError):
        await vision.analyze_meal([b"fake-image"], [])


async def test_analyze_meal_reraises_when_no_fallback_configured(monkeypatch: pytest.MonkeyPatch):
    fake_client = _AlwaysServerErrorClient()
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)
    monkeypatch.setattr(settings, "gemini_fallback_model", settings.gemini_model)

    with pytest.raises(genai_errors.ServerError):
        await vision.analyze_meal([b"fake-image"], [])
