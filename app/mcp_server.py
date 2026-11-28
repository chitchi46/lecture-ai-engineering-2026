"""第6回: 用語集と各回の要点を MCP サーバーとして公開する。

使い方:
    uv run python -m app.mcp_agent --list-tools     # このサーバーが公開するツールを一覧する（APIキー不要）
    uv run python -m app.mcp_server --http          # Streamable HTTP で起動する（http://127.0.0.1:8000/mcp）

引数なしで実行すると stdio（標準入出力）で待ち受ける。これは MCP クライアント（app.mcp_agent など）が
サブプロセスとして起動する使い方なので、手で実行すると入力待ちのまま止まって見える。

第3回では tools.py の関数を agent.py に直接登録した。MCP サーバーにすると、同じ関数を
エージェントのプログラム（app/mcp_agent.py）からも、MCP に対応したほかのソフトウェアからも、
共通の手順で使えるようになる。
"""

import argparse
import json
import random

from mcp.server.mcpserver import MCPServer

from app import tools

# サーバーの名前と説明。クライアントに表示される（設定ファイル側のキー "glossary" とは別物）
server = MCPServer(
    "aie2026-glossary",
    instructions="AIE2026（AIエンジニアリング実践講座）の用語集と各回の要点を提供する。",
)

# 第3回と同じ関数を、そのまま MCP のツールとして公開する。docstring と型ヒントが説明文になる
server.tool()(tools.search_glossary)

# TODO(宿題・基礎): get_lecture_summary も公開しよう
server.tool()(tools.get_lecture_summary)


# 宿題・実装: 第3回の make_quiz にあたるツールを、サーバー側に直接書いて公開する。
# 引数が範囲外のときは例外にせず、どう直せばよいかが分かる文を return で返す
# （return の文は [tool return] としてそのままモデルに渡る。raise にすると [tool error] になる）
@server.tool()
def make_quiz(lecture: int) -> str:
    """指定した講義回の復習・理解確認を求められたとき、その回の用語の問題と答えを1つ返す。

    lecture には回の番号を整数で入れる（例: 第2回なら 2）。
    """
    by_lecture: dict[int, list[dict]] = {}
    for entry in tools.load_glossary():
        by_lecture.setdefault(entry["lecture"], []).append(entry)
    available = sorted(by_lecture)
    listed = "、".join(f"第{n}回" for n in available)
    if lecture < 1:
        return f"第{lecture}回はありません。回の番号は1以上の整数で指定してください（クイズを作れる回: {listed}）。"
    if lecture > max(available):
        return (
            f"第{lecture}回は、この講座の用語集の範囲外です。"
            f"クイズを作れる回は {listed} です。どれか1つを指定してください。"
        )
    if lecture not in by_lecture:
        return f"第{lecture}回の用語は用語集にまだありません。クイズを作れる回は {listed} です。"
    term = random.choice(by_lecture[lecture])
    return json.dumps(
        {"lecture": lecture, "question": f"{term['term']}の意味は？", "answer": term["definition"]},
        ensure_ascii=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="用語集 MCP サーバー")
    parser.add_argument("--http", action="store_true", help="Streamable HTTP で起動する（既定は stdio）")
    args = parser.parse_args()
    if args.http:
        print("MCP サーバーを http://127.0.0.1:8000/mcp で起動します（Ctrl+C で停止）")
        server.run(transport="streamable-http")
    else:
        # stdio では標準出力がプロトコルの通信路になるので、print してはいけない
        server.run()


if __name__ == "__main__":
    main()
