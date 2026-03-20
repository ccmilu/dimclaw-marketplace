"""交叉测试 send-email 脚本 —— 重点测试 --file 参数。

Mock 审计:
- smtplib.SMTP_SSL: mock 掉整个 SMTP 连接，避免真实网络调用。
  风险: 低。只验证 send_email() 的参数传递逻辑，不验证 SMTP 协议。
- 文件 I/O: 使用 pytest tmp_path fixture 创建真实临时文件，不 mock open()。
  风险: 无。真实文件操作更可靠。
"""

import importlib.machinery
import importlib.util
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# send-email 文件名含连字符且无 .py 扩展名，需要显式指定 loader
SCRIPT_PATH = (
    "/Users/jason/Desktop/AI提效/资讯/.claude/skills/news-aggregator-skill/scripts/send-email"
)
VENV_PYTHON = (
    "/Users/jason/Desktop/AI提效/资讯/.claude/skills/news-aggregator-skill/.venv/bin/python"
)


def _load_send_email_module():
    """动态加载 send-email 脚本为模块（无 .py 扩展名需显式指定 loader）。"""
    loader = importlib.machinery.SourceFileLoader("send_email", SCRIPT_PATH)
    spec = importlib.util.spec_from_loader("send_email", loader)
    mod = importlib.util.module_from_spec(spec)
    # 阻止 __main__ 执行
    mod.__name__ = "send_email"
    spec.loader.exec_module(mod)
    return mod


# ─── send_email() 函数单元测试 ───


class TestSendEmailFunction:
    """测试 send_email() 函数本身（mock SMTP）。"""

    def setup_method(self):
        self.mod = _load_send_email_module()

    @patch("smtplib.SMTP_SSL")
    def test_send_plain_text(self, mock_smtp_cls):
        """纯文本发送 happy path。"""
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        result = self.mod.send_email("test@example.com", "主题", "正文内容")
        assert result == 0
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch("smtplib.SMTP_SSL")
    def test_send_html(self, mock_smtp_cls):
        """HTML 格式发送。"""
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        result = self.mod.send_email(
            "test@example.com", "主题", "<h1>标题</h1>", is_html=True
        )
        assert result == 0

    @patch("smtplib.SMTP_SSL")
    def test_send_multiple_recipients(self, mock_smtp_cls):
        """多收件人（逗号分隔）。"""
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        result = self.mod.send_email(
            "a@example.com, b@example.com", "主题", "正文"
        )
        assert result == 0
        # 验证 sendmail 收到的收件人列表
        call_args = mock_server.sendmail.call_args
        to_list = call_args[0][1]
        assert len(to_list) == 2
        assert "a@example.com" in to_list
        assert "b@example.com" in to_list

    @patch("smtplib.SMTP_SSL", side_effect=Exception("连接失败"))
    def test_send_failure(self, mock_smtp_cls):
        """SMTP 连接失败返回 1。"""
        result = self.mod.send_email("test@example.com", "主题", "正文")
        assert result == 1


# ─── CLI 集成测试（subprocess 调用） ───


class TestSendEmailCLI:
    """通过 subprocess 测试 CLI 参数解析，mock SMTP 在脚本内部。"""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        """运行 send-email 脚本，使用 -c 方式注入 SMTP mock。"""
        wrapper = textwrap.dedent(f"""\
            import sys
            sys.argv = {['send-email'] + args!r}
            from unittest.mock import patch, MagicMock
            import importlib.machinery, importlib.util
            mock_server = MagicMock()
            with patch('smtplib.SMTP_SSL', return_value=mock_server):
                loader = importlib.machinery.SourceFileLoader("__main__", {SCRIPT_PATH!r})
                spec = importlib.util.spec_from_loader("__main__", loader)
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                except SystemExit as e:
                    sys.exit(e.code if e.code is not None else 0)
        """)
        return subprocess.run(
            [VENV_PYTHON, "-c", wrapper],
            capture_output=True, text=True, timeout=10,
        )

    def test_file_reads_html(self, tmp_path):
        """--file 从文件读取 HTML 内容并发送。"""
        html_file = tmp_path / "test.html"
        html_file.write_text("<h1>测试邮件</h1><p>内容</p>", encoding="utf-8")

        result = self._run([
            "--html", "--file", str(html_file),
            "test@example.com", "测试主题",
        ])
        assert result.returncode == 0
        assert "邮件已发送" in result.stdout

    def test_file_not_found(self, tmp_path):
        """--file 指定不存在的文件时友好报错。"""
        result = self._run([
            "--file", str(tmp_path / "nonexistent.html"),
            "test@example.com", "主题",
        ])
        assert result.returncode == 1
        assert "文件不存在" in result.stderr

    def test_file_and_body_conflict(self, tmp_path):
        """--file 和正文参数同时提供时报错。"""
        html_file = tmp_path / "test.html"
        html_file.write_text("<p>test</p>", encoding="utf-8")

        result = self._run([
            "--file", str(html_file),
            "test@example.com", "主题", "多余的正文",
        ])
        assert result.returncode == 1
        assert "不能同时提供" in result.stderr

    def test_no_file_no_body(self):
        """既没有 --file 也没有正文参数时报错。"""
        result = self._run(["test@example.com", "主题"])
        assert result.returncode == 1
        assert "必须提供正文参数或 --file 参数" in result.stderr

    def test_backward_compat_with_body(self):
        """向后兼容：不带 --file，直接提供正文参数。"""
        result = self._run([
            "test@example.com", "主题", "纯文本正文",
        ])
        assert result.returncode == 0
        assert "邮件已发送" in result.stdout

    def test_backward_compat_html_with_body(self):
        """向后兼容：--html + 正文参数（不用 --file）。"""
        result = self._run([
            "--html", "test@example.com", "主题", "<b>加粗</b>",
        ])
        assert result.returncode == 0
        assert "邮件已发送" in result.stdout

    def test_html_flag_with_file(self, tmp_path):
        """--html 和 --file 组合使用。"""
        html_file = tmp_path / "newsletter.html"
        html_file.write_text(
            "<html><body><h1>Newsletter</h1></body></html>",
            encoding="utf-8",
        )
        result = self._run([
            "--html", "--file", str(html_file),
            "test@example.com", "每周简报",
        ])
        assert result.returncode == 0
        assert "邮件已发送" in result.stdout

    def test_file_with_large_content(self, tmp_path):
        """--file 读取大文件内容。"""
        large_html = "<html><body>" + "<p>段落</p>" * 1000 + "</body></html>"
        html_file = tmp_path / "large.html"
        html_file.write_text(large_html, encoding="utf-8")

        result = self._run([
            "--html", "--file", str(html_file),
            "test@example.com", "大邮件测试",
        ])
        assert result.returncode == 0
