"""Tests for enhanced news2html rendering: overview, tracking_info, alt_sources, cross_links, reading_guide."""

import importlib.util
import importlib.machinery
import copy
import os
import pytest

# Import the news2html script (no .py extension) as a module
_script_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "news2html"))
_loader = importlib.machinery.SourceFileLoader("news2html", _script_path)
_spec = importlib.util.spec_from_file_location("news2html", _script_path, loader=_loader)
news2html = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(news2html)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _base_data():
    """Minimal valid data WITHOUT any new fields (old format)."""
    return {
        "title": "每日新闻简报",
        "date": "2026-03-18",
        "main": [
            {
                "title": "测试新闻标题A",
                "url": "https://example.com/a",
                "category": "tech",
                "summary": "这是新闻A的摘要。",
                "source": "来源A",
                "time": "10:00",
            },
            {
                "title": "测试新闻标题B",
                "url": "https://example.com/b",
                "category": "headline",
                "summary": "这是新闻B的摘要。",
            },
        ],
        "brief": [
            {
                "title": "简讯C",
                "url": "https://example.com/c",
                "category": "finance",
                "source": "来源C",
            }
        ],
        "signature": "AI 助手",
        "tagline": "每天 5 分钟，了解世界。",
    }


def _enhanced_data():
    """Data with all new fields populated."""
    data = _base_data()
    data["overview"] = "今天科技界焦点集中在AI芯片与大模型进展。"
    data["reading_guide"] = "建议优先阅读科技板块的两条头条。"
    data["cross_links"] = [
        {
            "theme": "AI 基础设施竞赛",
            "related_indices": [0, 1],
            "explanation": "芯片发布和大模型扩容共同指向算力军备竞赛。",
        }
    ]
    # Add tracking_info to first main item
    data["main"][0]["tracking_info"] = {"consecutive_days": 3}
    # Add alt_sources to first main item
    data["main"][0]["alt_sources"] = ["路透社", "彭博社", "财新"]
    return data


# ---------------------------------------------------------------------------
# 1. Backward compatibility - old format JSON renders identically
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Old-format JSON (no new fields) must render identically to the original logic."""

    def test_no_overview_section(self):
        html = news2html.render_html(_base_data())
        assert "今日概览" not in html

    def test_no_tracking_badge(self):
        html = news2html.render_html(_base_data())
        assert "连续报道" not in html

    def test_no_alt_sources(self):
        html = news2html.render_html(_base_data())
        assert "多源覆盖" not in html

    def test_no_cross_links_section(self):
        html = news2html.render_html(_base_data())
        assert "今日关联" not in html

    def test_no_reading_guide(self):
        html = news2html.render_html(_base_data())
        assert "📖" not in html

    def test_old_format_still_has_core_structure(self):
        html = news2html.render_html(_base_data())
        assert "每日新闻简报" in html
        assert "测试新闻标题A" in html
        assert "测试新闻标题B" in html
        assert "简讯C" in html
        assert "AI 助手" in html
        assert "每天 5 分钟，了解世界。" in html


# ---------------------------------------------------------------------------
# 2. Overview rendering
# ---------------------------------------------------------------------------

class TestOverview:
    def test_overview_rendered_when_present(self):
        html = news2html.render_html(_enhanced_data())
        assert "今日概览" in html
        assert "今天科技界焦点集中在AI芯片与大模型进展。" in html

    def test_overview_not_rendered_when_empty_string(self):
        data = _base_data()
        data["overview"] = ""
        html = news2html.render_html(data)
        assert "今日概览" not in html

    def test_overview_not_rendered_when_none(self):
        data = _base_data()
        data["overview"] = None
        html = news2html.render_html(data)
        assert "今日概览" not in html

    def test_overview_html_structure(self):
        html = news2html.render_overview({"overview": "测试概览"})
        assert '<h2' in html
        assert '今日概览' in html
        assert '测试概览' in html

    def test_overview_empty_returns_empty_string(self):
        assert news2html.render_overview({}) == ""
        assert news2html.render_overview({"overview": ""}) == ""


# ---------------------------------------------------------------------------
# 3. Tracking info badge
# ---------------------------------------------------------------------------

class TestTrackingInfo:
    def test_tracking_badge_rendered(self):
        html = news2html.render_html(_enhanced_data())
        assert "连续报道第3天" in html

    def test_tracking_badge_not_rendered_without_field(self):
        html = news2html.render_html(_base_data())
        assert "连续报道" not in html

    def test_tracking_badge_in_render_main_item(self):
        item = {
            "title": "追踪新闻",
            "url": "https://example.com/track",
            "category": "tech",
            "summary": "追踪摘要",
            "tracking_info": {"consecutive_days": 5},
        }
        html = news2html.render_main_item(item, True)
        assert "连续报道第5天" in html
        assert "#FFF3CD" in html  # badge background color

    def test_no_tracking_badge_in_render_main_item(self):
        item = {
            "title": "普通新闻",
            "url": "https://example.com/normal",
            "category": "tech",
            "summary": "普通摘要",
        }
        html = news2html.render_main_item(item, True)
        assert "连续报道" not in html


# ---------------------------------------------------------------------------
# 4. Alt sources
# ---------------------------------------------------------------------------

class TestAltSources:
    def test_alt_sources_rendered(self):
        html = news2html.render_html(_enhanced_data())
        assert "多源覆盖" in html
        assert "路透社" in html
        assert "彭博社" in html
        assert "财新" in html

    def test_alt_sources_joined_with_slash(self):
        item = {
            "title": "多源新闻",
            "url": "https://example.com/multi",
            "category": "tech",
            "summary": "多源摘要",
            "alt_sources": ["SourceA", "SourceB"],
        }
        html = news2html.render_main_item(item, True)
        assert "SourceA / SourceB" in html

    def test_alt_sources_not_rendered_when_absent(self):
        html = news2html.render_html(_base_data())
        assert "多源覆盖" not in html

    def test_alt_sources_not_rendered_when_empty_list(self):
        data = _base_data()
        data["main"][0]["alt_sources"] = []
        html = news2html.render_html(data)
        assert "多源覆盖" not in html

    def test_alt_sources_color(self):
        item = {
            "title": "多源新闻",
            "url": "https://example.com/multi",
            "category": "tech",
            "summary": "多源摘要",
            "alt_sources": ["A"],
        }
        html = news2html.render_main_item(item, True)
        assert "#8E44AD" in html  # purple color


# ---------------------------------------------------------------------------
# 5. Cross links rendering
# ---------------------------------------------------------------------------

class TestCrossLinks:
    def test_cross_links_rendered_when_present(self):
        html = news2html.render_html(_enhanced_data())
        assert "今日关联" in html
        assert "AI 基础设施竞赛" in html
        assert "芯片发布和大模型扩容共同指向算力军备竞赛。" in html

    def test_cross_links_not_rendered_when_absent(self):
        html = news2html.render_html(_base_data())
        assert "今日关联" not in html

    def test_cross_links_not_rendered_when_empty_list(self):
        data = _base_data()
        data["cross_links"] = []
        html = news2html.render_html(data)
        assert "今日关联" not in html

    def test_render_cross_links_empty_returns_empty(self):
        assert news2html.render_cross_links({}) == ""
        assert news2html.render_cross_links({"cross_links": []}) == ""
        assert news2html.render_cross_links({"cross_links": None}) == ""

    def test_render_cross_links_multiple(self):
        data = {
            "cross_links": [
                {"theme": "主题一", "related_indices": [0], "explanation": "说明一"},
                {"theme": "主题二", "related_indices": [1, 2], "explanation": "说明二"},
            ]
        }
        html = news2html.render_cross_links(data)
        assert "主题一" in html
        assert "说明一" in html
        assert "主题二" in html
        assert "说明二" in html

    def test_cross_links_html_structure(self):
        data = {
            "cross_links": [
                {"theme": "测试主题", "related_indices": [0], "explanation": "测试说明"}
            ]
        }
        html = news2html.render_cross_links(data)
        assert '<h2' in html
        assert '今日关联' in html
        assert 'font-weight:600' in html  # theme font weight


# ---------------------------------------------------------------------------
# 6. Reading guide rendering
# ---------------------------------------------------------------------------

class TestReadingGuide:
    def test_reading_guide_rendered_in_footer(self):
        html = news2html.render_html(_enhanced_data())
        assert "建议优先阅读科技板块的两条头条。" in html

    def test_reading_guide_not_rendered_when_absent(self):
        html = news2html.render_html(_base_data())
        # The reading guide emoji should not appear
        assert "\U0001F4D6" not in html

    def test_reading_guide_before_tagline(self):
        html = news2html.render_html(_enhanced_data())
        guide_pos = html.index("建议优先阅读科技板块的两条头条。")
        tagline_pos = html.index("每天 5 分钟，了解世界。")
        assert guide_pos < tagline_pos

    def test_reading_guide_not_rendered_when_empty_string(self):
        data = _base_data()
        data["reading_guide"] = ""
        html = news2html.render_html(data)
        assert "\U0001F4D6" not in html

    def test_reading_guide_in_render_footer(self):
        data = {"reading_guide": "优先阅读头条", "tagline": "标语", "signature": "署名"}
        footer = news2html.render_footer(data)
        assert "优先阅读头条" in footer
        assert "\U0001F4D6" in footer


# ---------------------------------------------------------------------------
# 7. Integration: all enhanced fields together
# ---------------------------------------------------------------------------

class TestFullEnhancedRendering:
    def test_all_enhanced_fields_present(self):
        html = news2html.render_html(_enhanced_data())
        # Overview
        assert "今日概览" in html
        # Tracking badge
        assert "连续报道第3天" in html
        # Alt sources
        assert "多源覆盖" in html
        # Cross links
        assert "今日关联" in html
        # Reading guide
        assert "建议优先阅读" in html
        # Original content still present
        assert "测试新闻标题A" in html
        assert "测试新闻标题B" in html
        assert "简讯C" in html

    def test_section_order(self):
        """Verify overview -> main -> cross_links -> brief -> reading_guide."""
        html = news2html.render_html(_enhanced_data())
        overview_pos = html.index("今日概览")
        main_pos = html.index("测试新闻标题A")
        cross_pos = html.index("今日关联")
        brief_pos = html.index("简讯C")
        guide_pos = html.index("建议优先阅读")
        assert overview_pos < main_pos < cross_pos < brief_pos < guide_pos

    def test_html_escape_in_new_fields(self):
        """Ensure special characters are escaped in new fields."""
        data = _base_data()
        data["overview"] = '<script>alert("xss")</script>'
        data["reading_guide"] = 'Tom & Jerry <b>bold</b>'
        data["cross_links"] = [
            {
                "theme": "A & B",
                "related_indices": [0],
                "explanation": "C < D",
            }
        ]
        html = news2html.render_html(data)
        assert "&lt;script&gt;" in html
        assert "alert" in html
        assert "Tom &amp; Jerry" in html
        assert "A &amp; B" in html
        assert "C &lt; D" in html
