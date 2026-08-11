import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from brain.mcp.mcp_registry import _expand_external_command
from brain.request_router import RequestMode, classify_request, required_execution_tool


ROOT = Path(__file__).resolve().parents[1]


class ToolExecutionContractTests(unittest.TestCase):
    def test_web_content_with_error_word_is_still_successful(self):
        from brain.request_tool_policy import has_successful_tool_call

        audit = [{
            "name": "fetch_webpage", "authorized": True, "is_error": False,
            "result": "README: Error handling and failed-job recovery are documented here.",
        }]
        self.assertTrue(has_successful_tool_call(audit, {"fetch_webpage"}))

    def test_explicit_web_search_phrases_open_and_force_search(self):
        samples = (
            "帮我上网搜一下 Kimi V3 的体验页面和 API 服务",
            "现在就去网上帮我搜索一下",
            "请使用 Tavily 工具去查 Kimi API",
            "请你使用 web_search 搜索 Kimi V3",
        )
        for text in samples:
            with self.subTest(text=text):
                route = classify_request(text)
                self.assertEqual(RequestMode.TASK_DIRECT, route.mode)
                self.assertIn("web_search", route.capabilities)
                self.assertEqual(
                    "web_search",
                    required_execution_tool(route, {"web_search", "fetch_webpage"}),
                )

    def test_url_reading_remains_fetch_first(self):
        route = classify_request("搜索并读取 https://example.com 的正文")
        self.assertEqual(
            "fetch_webpage",
            required_execution_tool(route, {"web_search", "fetch_webpage"}),
        )

    def test_desktop_does_not_disable_tools_from_legacy_route(self):
        source = (ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_on_route_ready"
        )
        method_source = ast.get_source_segment(source, method)

        self.assertIn("disable_tools=False", method_source)
        self.assertNotIn("disable_tools=is_chat", method_source)

    def test_qq_does_not_disable_tools_from_legacy_route(self):
        source = (ROOT / "workers" / "qq_bridge_worker.py").read_text(encoding="utf-8")

        self.assertNotIn("disable_tools=is_chat", source)
        self.assertIn("实际工具决策交由 AgentCore", source)

    def test_agent_has_execution_contract_retry(self):
        source = (ROOT / "brain" / "agent.py").read_text(encoding="utf-8")

        self.assertIn("required_execution_tool", source)
        self.assertIn("未执行，强制补救", source)
        self.assertIn("刚才的文字不是最终回复", source)

    @unittest.skipUnless(os.name == "nt", "Windows-specific external MCP command normalization")
    def test_windows_mcp_uses_executable_npx_cmd(self):
        with patch("brain.mcp.mcp_registry.shutil.which", return_value=r"C:\\npm\\npx.cmd"):
            command = _expand_external_command(["npx", "-y", "tavily-mcp"])
        self.assertEqual(r"C:\\npm\\npx.cmd", command[0])


if __name__ == "__main__":
    unittest.main()
