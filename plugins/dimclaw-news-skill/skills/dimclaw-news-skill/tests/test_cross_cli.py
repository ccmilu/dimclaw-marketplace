"""search_events.py CLI 集成测试（subprocess 调用）。

用 subprocess 调用真实的 CLI 脚本，验证 stdout JSON 输出格式、
错误处理的 exit code、以及各种参数组合。

Mock 策略：
- embedding API 通过环境变量 + monkeypatch 在 subprocess 中 mock
- LanceDB 使用临时目录
- 由于 subprocess 无法直接 mock Python 对象，这里创建一个辅助脚本
  来注入 mock 后调用真实逻辑
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
VENV_PYTHON = os.path.join(
    os.path.dirname(__file__), "..", ".venv", "bin", "python"
)


def _write_helper_script(tmp_path, db_path):
    """生成一个注入 mock embedding 的辅助脚本，内部调用 search_events.main。"""
    helper = tmp_path / "cli_helper.py"
    helper.write_text(textwrap.dedent(f"""\
        import sys
        import os
        sys.path.insert(0, {repr(str(os.path.abspath(SCRIPTS_DIR)))})

        from unittest.mock import MagicMock, patch

        DIMENSIONS = 8

        def _fake_embedding(text):
            h = hash(text) & 0xFFFFFFFF
            return [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(DIMENSIONS)]

        def _make_mock_client():
            client = MagicMock()
            def _create(model, input, dimensions=None):
                resp = MagicMock()
                texts = [input] if isinstance(input, str) else list(input)
                data = []
                for i, t in enumerate(texts):
                    item = MagicMock()
                    item.index = i
                    item.embedding = _fake_embedding(t)
                    data.append(item)
                resp.data = data
                return resp
            client.embeddings.create = _create
            return client

        env_vars = {{
            "EMBEDDING_BASE_URL": "https://fake.api/v4",
            "EMBEDDING_API_KEY": "fake-key",
            "EMBEDDING_MODEL": "test-model",
            "EMBEDDING_DIMENSIONS": str(DIMENSIONS),
        }}

        with patch.dict(os.environ, env_vars):
            with patch("events_db._get_embedding_client", return_value=_make_mock_client()):
                from search_events import main
                main()
    """))
    return str(helper)


def _setup_db_with_events(tmp_path, db_path):
    """在临时 LanceDB 中预填充测试数据。返回辅助脚本路径。"""
    setup_script = tmp_path / "setup_db.py"
    setup_script.write_text(textwrap.dedent(f"""\
        import sys
        import os
        sys.path.insert(0, {repr(str(os.path.abspath(SCRIPTS_DIR)))})

        from unittest.mock import MagicMock, patch
        from datetime import datetime

        DIMENSIONS = 8

        def _fake_embedding(text):
            h = hash(text) & 0xFFFFFFFF
            return [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(DIMENSIONS)]

        def _make_mock_client():
            client = MagicMock()
            def _create(model, input, dimensions=None):
                resp = MagicMock()
                texts = [input] if isinstance(input, str) else list(input)
                data = []
                for i, t in enumerate(texts):
                    item = MagicMock()
                    item.index = i
                    item.embedding = _fake_embedding(t)
                    data.append(item)
                resp.data = data
                return resp
            client.embeddings.create = _create
            return client

        env_vars = {{
            "EMBEDDING_BASE_URL": "https://fake.api/v4",
            "EMBEDDING_API_KEY": "fake-key",
            "EMBEDDING_MODEL": "test-model",
            "EMBEDDING_DIMENSIONS": str(DIMENSIONS),
        }}

        today = datetime.now().strftime("%Y-%m-%d")

        with patch.dict(os.environ, env_vars):
            with patch("events_db._get_embedding_client", return_value=_make_mock_client()):
                from events_db import EventsDB
                db = EventsDB(db_path={repr(db_path)})
                events = [
                    {{
                        "event_id": "evt_cli_001",
                        "event_name": "AI大模型技术突破",
                        "category": "tech",
                        "first_seen": today,
                        "last_seen": today,
                        "consecutive_days": 1,
                        "latest_importance": 8,
                        "importance_trend": [8],
                        "daily_entries": {{today: {{"title": "AI突破", "url": ""}}}},
                        "keywords": ["AI"],
                        "summary": "测试摘要",
                        "related_events": [],
                    }},
                    {{
                        "event_id": "evt_cli_002",
                        "event_name": "全球气候变化会议",
                        "category": "headline",
                        "first_seen": today,
                        "last_seen": today,
                        "consecutive_days": 1,
                        "latest_importance": 7,
                        "importance_trend": [7],
                        "daily_entries": {{today: {{"title": "气候会议", "url": ""}}}},
                        "keywords": ["气候"],
                        "summary": "",
                        "related_events": [],
                    }},
                    {{
                        "event_id": "evt_cli_003",
                        "event_name": "股市大涨",
                        "category": "finance",
                        "first_seen": today,
                        "last_seen": today,
                        "consecutive_days": 1,
                        "latest_importance": 6,
                        "importance_trend": [6],
                        "daily_entries": {{today: {{"title": "股市涨", "url": ""}}}},
                        "keywords": ["股市"],
                        "summary": "",
                        "related_events": [],
                    }},
                ]
                db.batch_upsert(events)
                print(f"Inserted {{db.count()}} events")
    """))
    return str(setup_script)


@pytest.fixture
def cli_env(tmp_path):
    """准备 CLI 测试环境：临时 DB + 预填充数据。"""
    db_path = str(tmp_path / "cli_test_db")

    # 先运行设置脚本，填充数据
    setup_script = _setup_db_with_events(tmp_path, db_path)
    result = subprocess.run(
        [VENV_PYTHON, setup_script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Setup failed: {result.stderr}"

    # 写辅助脚本
    helper = _write_helper_script(tmp_path, db_path)

    return {
        "db_path": db_path,
        "helper": helper,
        "tmp_path": tmp_path,
    }


class TestCliSubprocess:
    """用 subprocess 调用 search_events.py CLI，验证输出。"""

    def test_vector_search_json_output(self, cli_env):
        """--query 模式输出合法 JSON 数组。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--query", "AI大模型",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_structural_search_json_output(self, cli_env):
        """--category 模式输出合法 JSON。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--category", "tech",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_combined_search_json_output(self, cli_env):
        """--combined 模式输出合法 JSON，无重复 event_id。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--query", "AI大模型",
                "--category", "tech",
                "--combined",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        event_ids = [r["event_id"] for r in data]
        assert len(event_ids) == len(set(event_ids)), "combined 结果有重复 event_id"

    def test_no_args_exit_code_nonzero(self, cli_env):
        """无参数 → exit code 非零。"""
        result = subprocess.run(
            [VENV_PYTHON, cli_env["helper"]],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0

    def test_combined_without_query_exit_1(self, cli_env):
        """--combined 缺 --query → exit 1。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--combined", "--category", "tech",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1

    def test_combined_without_category_exit_1(self, cli_env):
        """--combined 缺 --category → exit 1。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--combined", "--query", "test",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1

    def test_limit_parameter(self, cli_env):
        """--limit 参数限制结果数量。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--query", "事件",
                "--limit", "1",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) <= 1

    def test_vector_results_have_distance(self, cli_env):
        """向量检索结果应包含 _distance 字段。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--query", "AI大模型",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        if data:
            assert "_distance" in data[0], "向量检索结果缺少 _distance 字段"

    def test_results_have_source_tag(self, cli_env):
        """检索结果应包含 _source 标签。"""
        result = subprocess.run(
            [
                VENV_PYTHON, cli_env["helper"],
                "--query", "AI大模型",
                "--db-path", cli_env["db_path"],
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        for item in data:
            assert "_source" in item


# ============================================================
# update_events_history.py CLI 集成测试
# ============================================================

def _write_update_helper_script(tmp_path):
    """生成 update_events_history CLI 辅助脚本。"""
    helper = tmp_path / "update_helper.py"
    helper.write_text(textwrap.dedent(f"""\
        import sys
        import os
        sys.path.insert(0, {repr(str(os.path.abspath(SCRIPTS_DIR)))})

        from unittest.mock import MagicMock, patch

        DIMENSIONS = 8

        def _fake_embedding(text):
            h = hash(text) & 0xFFFFFFFF
            return [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(DIMENSIONS)]

        def _make_mock_client():
            client = MagicMock()
            def _create(model, input, dimensions=None):
                resp = MagicMock()
                texts = [input] if isinstance(input, str) else list(input)
                data = []
                for i, t in enumerate(texts):
                    item = MagicMock()
                    item.index = i
                    item.embedding = _fake_embedding(t)
                    data.append(item)
                resp.data = data
                return resp
            client.embeddings.create = _create
            return client

        env_vars = {{
            "EMBEDDING_BASE_URL": "https://fake.api/v4",
            "EMBEDDING_API_KEY": "fake-key",
            "EMBEDDING_MODEL": "test-model",
            "EMBEDDING_DIMENSIONS": str(DIMENSIONS),
        }}

        with patch.dict(os.environ, env_vars):
            with patch("events_db._get_embedding_client", return_value=_make_mock_client()):
                from update_events_history import main
                main()
    """))
    return str(helper)


class TestUpdateCliSubprocess:
    """用 subprocess 调用 update_events_history.py CLI。"""

    def test_update_new_events(self, tmp_path):
        """更新新事件到空数据库，验证统计输出。"""
        db_path = str(tmp_path / "update_db")
        update_file = str(tmp_path / "update.json")
        with open(update_file, "w") as f:
            json.dump([
                {
                    "event_id": "evt_cli_new_001",
                    "event_name": "CLI新事件",
                    "date": "2026-03-19",
                    "title": "CLI标题",
                    "url": "",
                    "category": "tech",
                    "importance": 7,
                }
            ], f, ensure_ascii=False)

        helper = _write_update_helper_script(tmp_path)
        result = subprocess.run(
            [
                VENV_PYTHON, helper,
                "--update", update_file,
                "--db-path", db_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "统计:" in result.stdout
        assert "新增事件:   1" in result.stdout

    def test_update_missing_file(self, tmp_path):
        """更新文件不存在时应输出提示并正常退出。"""
        helper = _write_update_helper_script(tmp_path)
        result = subprocess.run(
            [
                VENV_PYTHON, helper,
                "--update", str(tmp_path / "nonexistent.json"),
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "无更新事件" in result.stdout

    def test_update_empty_array(self, tmp_path):
        """更新文件为空数组时应输出提示。"""
        update_file = str(tmp_path / "empty.json")
        with open(update_file, "w") as f:
            json.dump([], f)

        helper = _write_update_helper_script(tmp_path)
        result = subprocess.run(
            [
                VENV_PYTHON, helper,
                "--update", update_file,
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "无更新事件" in result.stdout
