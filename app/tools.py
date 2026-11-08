"""第3回: エージェントに持たせるツール。

ここに書いた関数の docstring と型ヒントが、そのままモデルへの「ツールの説明文」になる。
説明文を書き換えると、モデルがツールを使うかどうか・どんな引数で呼ぶかが変わる。
"""

import json
from datetime import datetime, timezone
from random import choice
from uuid import uuid4
from functools import cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAX_RESULTS = 3
LEARNING_LOG_DIR = Path(__file__).resolve().parent.parent / "learning_log"


def make_quiz(lecture: int) -> str:
    """指定した講義回の復習・理解確認を求められたとき、用語の問題と答えを1つ返す。"""
    terms = [entry for entry in load_glossary() if entry["lecture"] == lecture]
    if not terms:
        return f"第{lecture}回の用語はありません。別の回を指定してください。"
    term = choice(terms)
    return json.dumps({"lecture": lecture, "question": f"{term['term']}の意味は？",
                       "answer": term["definition"]}, ensure_ascii=False)


def save_learning_log(lecture: int, content: str) -> str:
    """利用者が学習記録の保存を依頼したときだけ使う。対象の講義回と学んだ内容を保存する。

    書き込み前にハーネスが利用者に内容を提示し、承認を得る必要がある。
    """
    if lecture < 1 or not content.strip() or len(content) > 10000:
        return "保存しませんでした。講義回は1以上、内容は1〜10000文字で指定してください。"
    LEARNING_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LEARNING_LOG_DIR / f"{uuid4().hex}.json"
    record = {"lecture": lecture, "content": content,
              "created_at": datetime.now(timezone.utc).isoformat()}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return f"保存しました: learning_log/{path.name}"


@cache
def load_glossary() -> list[dict]:
    with open(DATA_DIR / "glossary.json", encoding="utf-8") as f:
        return json.load(f)["terms"]


def _normalize(text: str) -> str:
    return text.casefold().replace(" ", "").replace("　", "")


def _names(entry: dict) -> list[str]:
    return [_normalize(entry["term"]), _normalize(entry["id"])] + [
        _normalize(a) for a in entry["aliases"]
    ]


def search_glossary(query: str) -> str:
    """講座の用語集から用語を検索し、定義と、どの回で扱うかを返す。

    AIエンジニアリングの用語や、この講座（AIE2026）に固有の言葉について
    答える前に使うこと。query には調べたい用語を1つだけ入れる（例: "LoRA"）。
    """
    q = _normalize(query)
    if not q:
        return "検索語が空です。調べたい用語を1つ指定してください。"
    glossary = load_glossary()

    exact = [e for e in glossary if q in _names(e)]
    # 「r」のような短い別名が何にでも当たらないよう、名前側が短いものは除く
    partial = [
        e for e in glossary
        if e not in exact and any(q in n or (len(n) >= 3 and n in q) for n in _names(e))
    ]
    in_definition = [
        e for e in glossary
        if e not in exact and e not in partial and q in _normalize(e["definition"])
    ]
    hits = (exact + partial + in_definition)[:MAX_RESULTS]

    if not hits:
        return f"用語集に「{query}」は見つかりませんでした。"
    return json.dumps(
        [
            {
                "term": e["term"],
                "lecture": e["lecture"],
                "definition": e["definition"],
                "related": e["related"],
            }
            for e in hits
        ],
        ensure_ascii=False,
    )


def get_lecture_summary(lecture: int) -> str:
    """指定した回（1始まりの整数）の講義の要点を返す。

    「第3回では何を学ぶ？」のように、回の内容や関係を聞かれたときに使う。
    """
    path = DATA_DIR / "lectures" / f"{lecture:02d}.md"
    if not path.exists():
        available = sorted(p.stem for p in (DATA_DIR / "lectures").glob("*.md"))
        return f"第{lecture}回の要点はまだありません。用意されている回: {', '.join(available)}"
    return path.read_text(encoding="utf-8")
