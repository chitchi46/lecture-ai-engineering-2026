import json

import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ToolReturnPart

from app import tools
from app.agent import agent, run_with_approval


def test_quiz_uses_lecture_terms():
    result = json.loads(tools.make_quiz(2))
    assert result['lecture'] == 2
    assert any(result['answer'] == e['definition'] for e in tools.load_glossary() if e['lecture'] == 2)
    assert 'ありません' in tools.make_quiz(0)


@pytest.mark.parametrize('approve', [True, False])
def test_save_requires_approval(tmp_path, monkeypatch, approve):
    log_dir = tmp_path / 'learning_log'
    monkeypatch.setattr(tools, 'LEARNING_LOG_DIR', log_dir)
    model = TestModel(call_tools=['save_learning_log'])
    first = agent.run_sync('第3回の学習内容を保存', model=model)
    assert isinstance(first.output, DeferredToolRequests)
    assert not log_dir.exists()  # 保留時にはディレクトリさえ作らない
    call = first.output.approvals[0]
    from pydantic_ai import ToolApproved
    decision = ToolApproved(override_args={'lecture': 3, 'content': '承認後にのみ保存する。'}) if approve else False
    final = agent.run_sync(model=model, message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: decision}))
    assert isinstance(final.output, str)
    if approve:
        files = list(log_dir.glob('*.json'))
        assert len(files) == 1
        assert json.loads(files[0].read_text())['content'] == '承認後にのみ保存する。'
    else:
        assert not log_dir.exists()
        returns = [p for m in final.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
        assert returns and returns[-1].tool_name == 'save_learning_log'


@pytest.mark.parametrize('answer', ['', 'no', 'yes'])
def test_cli_defaults_to_denial(tmp_path, monkeypatch, answer):
    log_dir = tmp_path / 'learning_log'
    monkeypatch.setattr(tools, 'LEARNING_LOG_DIR', log_dir)
    prompts = []
    def confirm(prompt):
        prompts.append(prompt)
        return answer
    result = run_with_approval(agent, '記録を保存', TestModel(call_tools=['save_learning_log']), confirm)
    assert isinstance(result.output, str)
    assert prompts and 'save_learning_log' in prompts[0]
    assert not log_dir.exists()


def test_invalid_and_multiple_records(tmp_path, monkeypatch):
    log_dir = tmp_path / 'learning_log'
    monkeypatch.setattr(tools, 'LEARNING_LOG_DIR', log_dir)
    for lecture, content in [(0, '内容'), (3, ' '), (3, 'a' * 10001)]:
        assert '保存しませんでした' in tools.save_learning_log(lecture, content)
        assert not log_dir.exists()
    tools.save_learning_log(3, '一件目')
    tools.save_learning_log(3, '二件目')
    assert len(list(log_dir.glob('*.json'))) == 2


def test_cli_explicit_approval_and_eof(tmp_path, monkeypatch):
    log_dir = tmp_path / 'learning_log'
    monkeypatch.setattr(tools, 'LEARNING_LOG_DIR', log_dir)
    prompts = []
    def approve(prompt):
        assert not log_dir.exists()
        prompts.append(prompt)
        return 'y'
    result = run_with_approval(agent, '保存', TestModel(call_tools=['save_learning_log'], seed=3), approve)
    assert isinstance(result.output, str)
    assert prompts and len(list(log_dir.glob('*.json'))) == 1
    def eof(prompt):
        raise EOFError
    run_with_approval(agent, '保存', TestModel(call_tools=['save_learning_log'], seed=3), eof)
    assert len(list(log_dir.glob('*.json'))) == 1
