import unittest
from unittest.mock import patch

import config
from skills.github_mcp import tools
from skills.github_mcp.github_mcp import GitHubMCP


class GitHubSkillTests(unittest.TestCase):
    def test_config_defaults(self):
        with patch.object(config, "_load_full_config", return_value={}):
            self.assertEqual(4000, config.get_github_config()["content_preview_chars"])

    def test_tools_use_function_schema_and_are_read_only(self):
        names = {item["function"]["name"] for item in tools.TOOL_DEFINITIONS}
        self.assertEqual(names, set(tools.TOOL_EXECUTORS))
        self.assertNotIn("github_create_issue", names)
        self.assertTrue(all(item["type"] == "function" for item in tools.TOOL_DEFINITIONS))

    def test_preview_is_bounded_and_marked_untrusted(self):
        with patch("skills.github_mcp.tools.get_mcp") as get_mcp:
            get_mcp.return_value.get_readme.return_value = {"kind": "text", "path": "README.md", "html_url": "https://example.test", "content": "x" * 5000}
            result = tools.github_get_readme({"owner": "octo", "repo": "repo"})
        self.assertIn("外部仓库内容", result)
        self.assertIn('"truncated": true', result)

    def test_environment_token_has_precedence(self):
        with patch.dict("os.environ", {"LIANXIN_GITHUB_TOKEN": "env-token"}), patch("skills.github_mcp.github_mcp.get_github_config", return_value={"token": "config-token"}):
            self.assertEqual("env-token", GitHubMCP().token)

    def test_directory_result_is_compact_and_truncated(self):
        entries = [
            {"name": f"file_{index}.py", "path": f"src/file_{index}.py", "type": "file", "size": 1, "html_url": "https://example.test"}
            for index in range(101)
        ]
        with patch.object(GitHubMCP, "_contents", return_value=entries):
            result = GitHubMCP().list_directory("octo", "repo", "src")
        self.assertEqual("directory", result["kind"])
        self.assertEqual(100, len(result["items"]))
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
