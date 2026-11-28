"""第6回 宿題（基礎・実装）のテスト（LLM も API キーも使わない）。

FunctionModel（決めた順にツールを呼ぶ偽モデル）で、MCP サーバー経由の呼び出しと、
範囲外の引数のときにモデルへ渡る内容（return と raise の違い）を確かめる。
"""

import json
import sys
import textwrap

from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from app.mcp_agent import MCP_CONFIG, build_mcp_agent


def _glossary_only_config(tmp_path):
    config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"mcpServers": {"glossary": config["mcpServers"]["glossary"]}}), encoding="utf-8")
    return path


def _call_once(tool_name, args):
    """1 回目は指定のツールを呼び、2 回目はツールの結果を読んで答えたことにする偽モデル。"""

    def respond(messages, info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name, args)])
        return ModelResponse(parts=[TextPart("ツールの結果を読んで答えました")])

    return FunctionModel(respond)


def _parts(result, kind):
    return [p for m in result.all_messages() for p in m.parts if isinstance(p, kind)]


def test_basic_lecture_summary_is_exposed_without_changing_agent(tmp_path):
    model = TestModel(call_tools=[])
    build_mcp_agent(_glossary_only_config(tmp_path)).run_sync("ツール一覧", model=model)
    offered = {t.name for t in model.last_model_request_parameters.function_tools}
    assert {"glossary_search_glossary", "glossary_get_lecture_summary", "glossary_make_quiz"} <= offered


def test_basic_lecture_summary_comes_back_through_mcp(tmp_path):
    agent = build_mcp_agent(_glossary_only_config(tmp_path))
    result = agent.run_sync("第2回では何を学びますか？", model=_call_once("glossary_get_lecture_summary", {"lecture": 2}))
    returns = _parts(result, ToolReturnPart)
    assert returns[0].tool_name == "glossary_get_lecture_summary"
    assert "第2回" in str(returns[0].content)


def test_quiz_for_existing_lecture(tmp_path):
    agent = build_mcp_agent(_glossary_only_config(tmp_path))
    result = agent.run_sync("第2回のクイズを作って", model=_call_once("glossary_make_quiz", {"lecture": 2}))
    quiz = json.loads(str(_parts(result, ToolReturnPart)[0].content))
    assert quiz["lecture"] == 2 and quiz["question"].endswith("の意味は？") and quiz["answer"]


def test_out_of_range_message_reaches_model_as_tool_return(tmp_path):
    """return で返した説明文は [tool return] としてモデルに渡り、エラー扱いにならない。"""
    agent = build_mcp_agent(_glossary_only_config(tmp_path))
    for lecture, expected in [(99, "範囲外"), (0, "1以上"), (5, "まだありません")]:
        result = agent.run_sync("クイズ", model=_call_once("glossary_make_quiz", {"lecture": lecture}))
        returns = _parts(result, ToolReturnPart)
        assert returns and expected in str(returns[0].content)
        assert "第1回" in str(returns[0].content)  # 作れる回の一覧も伝える
        assert not [p for p in _parts(result, RetryPromptPart) if p.tool_name]


def _raising_server_config(tmp_path, error_line):
    server = tmp_path / "raising_server.py"
    server.write_text(
        textwrap.dedent(
            f"""
            from mcp.server.mcpserver import MCPServer
            from mcp.server.mcpserver.exceptions import ToolError
            server = MCPServer("raising")

            @server.tool()
            def make_quiz(lecture: int) -> str:
                \"\"\"範囲外なら例外にする版。\"\"\"
                if lecture > 6:
                    {error_line}
                return "ok"

            server.run()
            """
        ),
        encoding="utf-8",
    )
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"mcpServers": {"raising": {"command": sys.executable, "args": [str(server)]}}}), encoding="utf-8")
    return config


def _tool_errors(tmp_path, error_line):
    config = _raising_server_config(tmp_path, error_line)
    result = build_mcp_agent(config).run_sync("クイズ", model=_call_once("raising_make_quiz", {"lecture": 99}))
    assert not _parts(result, ToolReturnPart)
    return [str(p.content) for p in _parts(result, RetryPromptPart) if p.tool_name]


def test_raised_value_error_hides_message_from_model(tmp_path):
    """比較用: ValueError で raise すると [tool error] になり、モデルには定型文しか渡らない。"""
    errors = _tool_errors(tmp_path, 'raise ValueError(f"第{lecture}回は範囲外です")')
    assert errors == ["Error executing tool make_quiz"]


def test_raised_tool_error_passes_message_as_tool_error(tmp_path):
    """比較用: ToolError で raise すると [tool error] になり、説明文もモデルに渡る。"""
    errors = _tool_errors(tmp_path, 'raise ToolError(f"第{lecture}回は範囲外です")')
    assert errors and "範囲外" in errors[0] and errors[0].startswith("Error executing tool make_quiz")
