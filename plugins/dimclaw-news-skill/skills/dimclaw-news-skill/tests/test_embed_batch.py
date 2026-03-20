"""_embed_batch 自动分批功能的交叉测试。

Mock 审计:
- _compute_embeddings_batch: 被 mock 为返回确定性向量（基于输入长度和索引）。
  真实 API 返回 resp.data[i].embedding，按 index 排序。
  Mock 行为：直接返回 list[list[float]]，跳过了 API 响应解析层。
  风险：中。如果 _compute_embeddings_batch 的接口签名变化会导致 mock 失效。
  缓解：测试验证了调用参数（client, model, texts, dimensions）与实现一致。

测试要点:
- 边界值：0条、1条、59条、60条、61条、64条、120条
- 返回向量的数量和顺序与输入文本一致
- 验证分批次数（60条→1次，61条→2次，120条→2次）
"""

import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

DIMENSIONS = 8


def _make_deterministic_vector(text_index: int, batch_offset: int = 0) -> list[float]:
    """根据全局索引生成确定性向量，用于验证顺序。"""
    global_idx = batch_offset + text_index
    return [float(global_idx)] * DIMENSIONS


def _make_mock_compute_batch(call_log: list):
    """创建一个 mock 的 _compute_embeddings_batch，记录调用并返回确定性向量。

    call_log: 传入一个列表，每次调用会追加 (len(texts),) 供后续断言。
    """
    cumulative_offset = [0]  # 用列表实现闭包可变

    def mock_fn(client, model, texts, dimensions):
        call_log.append(len(texts))
        result = []
        for i in range(len(texts)):
            result.append(_make_deterministic_vector(i, cumulative_offset[0]))
        cumulative_offset[0] += len(texts)
        return result

    return mock_fn


@pytest.fixture
def db_instance(tmp_path):
    """创建一个使用 mock embedding 的 EventsDB 实例。"""
    env_vars = {
        "EMBEDDING_BASE_URL": "https://fake.api/v4",
        "EMBEDDING_API_KEY": "fake-key",
        "EMBEDDING_MODEL": "test-model",
        "EMBEDDING_DIMENSIONS": str(DIMENSIONS),
    }
    mock_client = MagicMock()
    # 给 single embed 也一个基本 mock
    def _single_embed(model, input, dimensions=None):
        resp = MagicMock()
        item = MagicMock()
        item.index = 0
        item.embedding = [0.0] * DIMENSIONS
        resp.data = [item]
        return resp
    mock_client.embeddings.create = _single_embed

    with patch.dict(os.environ, env_vars):
        with patch("events_db._get_embedding_client", return_value=mock_client):
            from events_db import EventsDB
            db = EventsDB(db_path=str(tmp_path / "test_db"))
            yield db


class TestEmbedBatchAutoBatching:
    """测试 _embed_batch 自动分批逻辑。"""

    def test_zero_texts(self, db_instance):
        """0 条文本 → 返回空列表，不调用 API。"""
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch([])
        assert result == []
        assert len(call_log) == 0, "空列表不应调用 _compute_embeddings_batch"

    def test_one_text(self, db_instance):
        """1 条文本 → 调用 1 次，返回 1 个向量。"""
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(["hello"])
        assert len(result) == 1
        assert len(result[0]) == DIMENSIONS
        assert len(call_log) == 1
        assert call_log[0] == 1

    def test_59_texts_single_batch(self, db_instance):
        """59 条文本 → 调用 1 次（< batch_size=60）。"""
        texts = [f"text_{i}" for i in range(59)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 59
        assert len(call_log) == 1
        assert call_log[0] == 59

    def test_60_texts_single_batch(self, db_instance):
        """60 条文本 → 恰好 1 次调用（== batch_size）。"""
        texts = [f"text_{i}" for i in range(60)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 60
        assert len(call_log) == 1
        assert call_log[0] == 60

    def test_61_texts_two_batches(self, db_instance):
        """61 条文本 → 2 次调用（60 + 1）。"""
        texts = [f"text_{i}" for i in range(61)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 61
        assert len(call_log) == 2
        assert call_log[0] == 60
        assert call_log[1] == 1

    def test_64_texts_two_batches(self, db_instance):
        """64 条文本 → 2 次调用（60 + 4）。"""
        texts = [f"text_{i}" for i in range(64)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 64
        assert len(call_log) == 2
        assert call_log[0] == 60
        assert call_log[1] == 4

    def test_120_texts_two_batches(self, db_instance):
        """120 条文本 → 2 次调用（60 + 60）。"""
        texts = [f"text_{i}" for i in range(120)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 120
        assert len(call_log) == 2
        assert call_log[0] == 60
        assert call_log[1] == 60

    def test_121_texts_three_batches(self, db_instance):
        """121 条文本 → 3 次调用（60 + 60 + 1）。"""
        texts = [f"text_{i}" for i in range(121)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 121
        assert len(call_log) == 3
        assert call_log == [60, 60, 1]

    def test_order_preserved_across_batches(self, db_instance):
        """跨批次时向量顺序应与输入文本顺序一致。"""
        texts = [f"text_{i}" for i in range(65)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)

        # 验证每个向量对应正确的全局索引
        for i, vec in enumerate(result):
            expected = [float(i)] * DIMENSIONS
            assert vec == expected, f"向量 {i} 不匹配: 期望 {expected}, 实际 {vec}"

    def test_passes_correct_args_to_compute(self, db_instance):
        """验证传递给 _compute_embeddings_batch 的参数正确。"""
        texts = ["text_0", "text_1"]
        with patch("events_db._compute_embeddings_batch") as mock_compute:
            mock_compute.return_value = [[0.0] * DIMENSIONS, [0.0] * DIMENSIONS]
            db_instance._embed_batch(texts)

            mock_compute.assert_called_once_with(
                db_instance._client,
                db_instance._model,
                texts,
                db_instance._dimensions,
            )

    def test_passes_correct_args_multi_batch(self, db_instance):
        """多批次时每次传递的 texts 子集正确。"""
        texts = [f"text_{i}" for i in range(62)]
        with patch("events_db._compute_embeddings_batch") as mock_compute:
            mock_compute.return_value = [[0.0] * DIMENSIONS] * 60  # 第一批
            # 需要不同返回值
            mock_compute.side_effect = [
                [[0.0] * DIMENSIONS] * 60,  # 第一批 60 条
                [[0.0] * DIMENSIONS] * 2,   # 第二批 2 条
            ]
            db_instance._embed_batch(texts)

            assert mock_compute.call_count == 2
            # 第一次调用的 texts 参数
            first_call_texts = mock_compute.call_args_list[0][0][2]
            assert len(first_call_texts) == 60
            assert first_call_texts[0] == "text_0"
            assert first_call_texts[59] == "text_59"
            # 第二次调用的 texts 参数
            second_call_texts = mock_compute.call_args_list[1][0][2]
            assert len(second_call_texts) == 2
            assert second_call_texts[0] == "text_60"
            assert second_call_texts[1] == "text_61"

    def test_public_embed_batch_delegates_to_private(self, db_instance):
        """公开的 embed_batch 方法应委托给 _embed_batch。"""
        texts = [f"text_{i}" for i in range(5)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result_public = db_instance.embed_batch(texts)
        assert len(result_public) == 5

    def test_large_batch_180_texts(self, db_instance):
        """180 条文本 → 3 次调用（60 + 60 + 60）。"""
        texts = [f"text_{i}" for i in range(180)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        assert len(result) == 180
        assert call_log == [60, 60, 60]

    def test_vector_dimensions_consistent(self, db_instance):
        """所有返回向量的维度应与 DIMENSIONS 一致。"""
        texts = [f"text_{i}" for i in range(65)]
        call_log = []
        mock_fn = _make_mock_compute_batch(call_log)
        with patch("events_db._compute_embeddings_batch", side_effect=mock_fn):
            result = db_instance._embed_batch(texts)
        for i, vec in enumerate(result):
            assert len(vec) == DIMENSIONS, f"向量 {i} 维度错误: {len(vec)} != {DIMENSIONS}"
