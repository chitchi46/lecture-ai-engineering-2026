"""第1回: 素のLLMに質問するスクリプト。

使い方:
    uv run python -m app.ask "LoRAとは何ですか？"
    uv run python -m app.ask            # 質問を省略すると、講座に固有の質問をする
    uv run python -m app.ask --chat "LoRAとは何ですか？"  # 追加質問を続ける

通常の呼び出しはツールも会話履歴も持たない「素の」呼び出し。
--chat を指定した場合だけ、同じチャットで会話履歴を保持する。
講座に固有のことを聞くと、「分からない」と答えるか、一般論でもっともらしく答える
（講座の実際とは違う）ことを確かめよう。
（第3回で、用語集を検索できるエージェントにして改善する）
"""

import argparse

from google import genai
from google.genai import errors

from app.config import MODEL, api_error_message, get_api_key

# 講座に固有の質問。正しい答えは data/glossary.json の homework-levels にある
DEFAULT_QUESTION = "AIE2026の講座の宿題にある「3レベル」とは何ですか？"


def ask(question: str) -> str:
    client = genai.Client(api_key=get_api_key())
    response = client.models.generate_content(model=MODEL, contents=question)

    print_usage(response)

    return response.text


def print_usage(response: object) -> None:
    """API がトークン数を返した場合だけ表示する。"""
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        print(f"[トークン] 入力: {usage.prompt_token_count}, 出力: {usage.candidates_token_count}")


def chat_with_followups(first_question: str) -> None:
    """同じチャットを使い、空行が入力されるまで追加質問を受け付ける。"""
    client = genai.Client(api_key=get_api_key())
    chat = client.chats.create(model=MODEL)
    question = first_question
    while question:
        print(f"[質問] {question}\n")
        response = chat.send_message(question)
        print_usage(response)
        print(response.text or "")
        try:
            question = input("\n[追加の質問（空行で終了）] ").strip()
        except EOFError:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="素のLLMに質問する")
    parser.add_argument("--chat", action="store_true", help="会話履歴を保って追加質問する")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    args = parser.parse_args()

    print(f"[model] {MODEL}")
    try:
        if args.chat:
            chat_with_followups(args.question)
        else:
            print(f"[質問] {args.question}\n")
            print(ask(args.question))
    except errors.APIError as e:
        print(api_error_message(e.code, e.message))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
