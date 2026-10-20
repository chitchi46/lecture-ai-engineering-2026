"""第1回の会話モードが一つのチャットを使い続けることを確認する。"""

from types import SimpleNamespace

from app import ask


def test_followup_uses_the_same_chat(monkeypatch, capsys):
    created = []

    class FakeChat:
        def __init__(self):
            self.questions = []

        def send_message(self, question):
            self.questions.append(question)
            return SimpleNamespace(
                text=" / ".join(self.questions),
                usage_metadata=SimpleNamespace(
                    prompt_token_count=len(self.questions),
                    candidates_token_count=2,
                ),
            )

    def create_chat(*, model):
        assert model == ask.MODEL
        chat = FakeChat()
        created.append(chat)
        return chat

    monkeypatch.setattr(
        ask.genai,
        "Client",
        lambda *, api_key: SimpleNamespace(chats=SimpleNamespace(create=create_chat)),
    )
    monkeypatch.setattr(ask, "get_api_key", lambda: "test-only-key")
    followups = iter(["その長所は？", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(followups))

    ask.chat_with_followups("LoRAとは？")

    assert len(created) == 1
    assert created[0].questions == ["LoRAとは？", "その長所は？"]
    output = capsys.readouterr().out
    assert "LoRAとは？ / その長所は？" in output
    assert "入力: 2, 出力: 2" in output
