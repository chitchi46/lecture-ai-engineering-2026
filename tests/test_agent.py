"""エージェントの配線テスト。

TestModel は実際のLLMを呼ばない偽モデルで、登録されたツールを全部呼んでから
ダミーの回答を返す。APIキーも利用枠も使わずに、
「ツールが登録され、呼ばれ、結果がモデルに戻るか」を確かめられる。
"""

import json

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel

from app.agent import agent

BASE_TOOLS = {"search_materials"}


def _parts(result, kind):
    return [p for m in result.all_messages() for p in m.parts if isinstance(p, kind)]


def test_base_tools_are_registered_and_called(monkeypatch):
    monkeypatch.setattr("app.retrieval.search", lambda *args, **kwargs: [])
    model = TestModel()
    with agent.override(model=model):
        result = agent.run_sync("LoRAとは？")

    offered = {t.name for t in model.last_model_request_parameters.function_tools}
    assert BASE_TOOLS <= offered  # 演習でツールを足しても壊れないよう「含む」で判定

    called = {p.tool_name for p in _parts(result, ToolCallPart)}
    assert BASE_TOOLS <= called
    # 承認が必要なツール（requires_approval=True）は、実行されずに承認待ちになるので戻り値が無い
    waiting = len(result.output.approvals) if isinstance(result.output, DeferredToolRequests) else 0
    assert len(_parts(result, ToolReturnPart)) + waiting == len(_parts(result, ToolCallPart))
    assert result.output


def test_material_result_reaches_the_model(monkeypatch):
    hit = {"page_id": "lec02-main-p052", "lecture": 2, "page": 52, "content": "元の重みは固定"}
    monkeypatch.setattr("app.retrieval.search", lambda *args, **kwargs: [hit])
    model = TestModel(call_tools=["search_materials"])
    with agent.override(model=model):
        result = agent.run_sync("RAGとは？")
    returns = _parts(result, ToolReturnPart)
    assert returns and returns[0].tool_name == "search_materials"
    assert json.loads(returns[0].content)["results"] == [hit]


def test_hybrid_tools_are_registered_and_called(monkeypatch):
    from app import tools
    from app.agent import HYBRID_INSTRUCTIONS

    fake_hybrid = {
        "seeds": [{"page_id": "lec02-main-p053", "lecture": 2, "page": 53, "content": "構造化出力"}],
        "expanded": [{"page_id": "lec03-main-p035", "lecture": 3, "page": 35, "content": "制約付きデコーディング"}],
        "tree_sections": [],
        "chapter_index": [],
    }
    monkeypatch.setattr("app.retrieval.search_hybrid", lambda *args, **kwargs: fake_hybrid)
    monkeypatch.setattr("app.retrieval.read_page", lambda page_id: {"page_id": page_id, "content": "本文"})
    monkeypatch.setattr("app.retrieval.browse_tree", lambda node_id=None: {"node": {"id": "root", "title": "root"}, "children": []})

    model = TestModel()
    with agent.override(model=model, instructions=HYBRID_INSTRUCTIONS, tools=[tools.search_hybrid, tools.read_page, tools.browse_tree]):
        result = agent.run_sync("構造化出力とFTの使い分けは？")

    offered = {t.name for t in model.last_model_request_parameters.function_tools}
    assert {"search_hybrid", "read_page", "browse_tree"} <= offered
    returns = _parts(result, ToolReturnPart)
    by_name = {r.tool_name: json.loads(r.content) for r in returns}
    assert by_name["search_hybrid"] == fake_hybrid
    assert by_name["read_page"]["content"] == "本文"
