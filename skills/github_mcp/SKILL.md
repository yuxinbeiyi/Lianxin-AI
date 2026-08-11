---
name: GitHub MCP
description: "允许莲心通过 GitHub API 搜索仓库、查看 README、读取文件、列出 commits 以及创建 issue 的技能。"
version: 0.1
auto_activate: true
---

# GitHub MCP

此技能为莲心提供对 GitHub 的基本读写能力。激活后技能会尝试从环境变量或本地配置读取 GitHub Token：

- 优先使用环境变量 `LIANXIN_GITHUB_TOKEN`。
- 否则尝试读取 `~/.lianxin/user_config.json` 下 `{"github": {"token": "..."}}` 字段。

如果没有配置 token，则只支持对公共仓库的只读操作（受 GitHub 速率限制）。

能力点：
- search_repos(query)
- get_readme(owner, repo)
- get_file(owner, repo, path)
- list_commits(owner, repo)
- create_issue(owner, repo, title, body, labels)

将此技能与 AgentCore 的工具调用绑定可以让 LLM 在对话中直接调用这些接口来完成相关任务。
