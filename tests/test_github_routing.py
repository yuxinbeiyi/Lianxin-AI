import unittest

from brain.request_router import (
    CAPABILITY_TO_TOOLS,
    RequestMode,
    classify_request,
    required_execution_tool,
)
from brain.request_tool_policy import request_tool_allowlist
from skills.github_mcp.tools import TOOL_DEFINITIONS


class GitHubRoutingTests(unittest.TestCase):
    def test_bare_github_url_keeps_fast_web_fetch(self):
        route = classify_request(
            "看看这个项目是做什么的：https://github.com/example/project"
        )
        self.assertIn("web_fetch", route.capabilities)
        self.assertNotIn("github", route.capabilities)
        self.assertEqual(
            required_execution_tool(route, {"fetch_webpage"}), "fetch_webpage"
        )
        self.assertEqual(
            {"fetch_webpage", "get_current_time"},
            request_tool_allowlist(
                "看看这个项目是做什么的：https://github.com/example/project"
            ),
        )

    def test_negated_source_request_keeps_fast_web_fetch(self):
        message = (
            "请快速阅读并介绍这个 GitHub 项目是做什么的，不需要查看提交或具体源码："
            "https://github.com/example/project"
        )
        route = classify_request(message)
        self.assertIn("web_fetch", route.capabilities)
        self.assertNotIn("github", route.capabilities)
        self.assertEqual("fetch_webpage", required_execution_tool(route, {"fetch_webpage"}, message))

    def test_recent_commits_use_github_skill(self):
        message = "帮我看看 yuxinbeiyi/Lianxin-AI 最近更新了什么"
        route = classify_request(message)
        self.assertEqual(route.mode, RequestMode.TASK_DIRECT)
        self.assertIn("github", route.capabilities)
        self.assertNotIn("web_fetch", route.capabilities)
        self.assertEqual(
            required_execution_tool(
                route, CAPABILITY_TO_TOOLS["github"]
                , message
            ),
            "github_list_commits",
        )

    def test_repository_file_uses_github_file_tool(self):
        message = (
            "读取这个仓库的 requirements-core.txt，告诉我主要依赖："
            "https://github.com/yuxinbeiyi/Lianxin-AI"
        )
        route = classify_request(message)
        self.assertIn("github", route.capabilities)
        self.assertEqual(
            required_execution_tool(route, CAPABILITY_TO_TOOLS["github"], message),
            "github_get_file",
        )

    def test_github_file_url_is_allowed_by_url_policy(self):
        message = (
            "读取这个仓库的 requirements-core.txt："
            "https://github.com/yuxinbeiyi/Lianxin-AI"
        )
        allowed = request_tool_allowlist(message)
        self.assertIn("github_get_file", allowed)
        self.assertNotIn("fetch_webpage", allowed)

    def test_directory_request_uses_github_directory_tool(self):
        message = "查看 yuxinbeiyi/Lianxin-AI 的目录结构"
        route = classify_request(message)
        self.assertIn("github", route.capabilities)
        self.assertEqual(
            required_execution_tool(route, CAPABILITY_TO_TOOLS["github"], message),
            "github_list_directory",
        )

    def test_github_search_does_not_fall_back_to_web_search(self):
        message = "请在 GitHub 搜索 5 个 Python 桌面 AI 助手，按 Star 比较"
        route = classify_request(message)
        self.assertIn("github", route.capabilities)
        self.assertEqual(
            required_execution_tool(route, CAPABILITY_TO_TOOLS["github"], message),
            "github_search_repositories",
        )

    def test_github_search_wording_without_star_is_recognized(self):
        message = "在 GitHub 找几个开源桌面助手项目"
        route = classify_request(message)
        self.assertIn("github", route.capabilities)
        self.assertEqual(
            required_execution_tool(route, CAPABILITY_TO_TOOLS["github"], message),
            "github_search_repositories",
        )

    def test_route_selects_every_registered_github_tool(self):
        route = classify_request(
            "请在 GitHub 搜索 5 个 Python 桌面 AI 助手，按 Star 比较"
        )
        selected = {
            item["function"]["name"]
            for item in TOOL_DEFINITIONS
            if item["function"]["name"] in route.tool_names
        }
        self.assertEqual(selected, CAPABILITY_TO_TOOLS["github"])


if __name__ == "__main__":
    unittest.main()
