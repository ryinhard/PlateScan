"""app.core.vision 單元測試：JSON 回應解析與空輸入短路邏輯。

以 monkeypatch 替換 _get_client()，不實際呼叫 Gemini API。
"""

import pytest

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
