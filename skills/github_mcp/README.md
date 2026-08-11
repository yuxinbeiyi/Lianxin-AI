# GitHub MCP (Lianxin)

这是莲心的 GitHub Micro-Controller Provider（MCP）模块，提供一个轻量的 Python 客户端
用以访问 GitHub REST API，满足常见的助手需求：搜索仓库、查看 README、读取代码文件、
列出提交记录和创建 issue。

快速开始

1. 配置 Personal Access Token（仅在需要写操作时）：

   - 在环境变量中设置：
     - Windows (PowerShell): $env:LIANXIN_GITHUB_TOKEN = "ghp_xxx"
     - Linux/macOS (bash): export LIANXIN_GITHUB_TOKEN=ghp_xxx

   - 或者把 token 放入文件 `%USERPROFILE%/.lianxin/user_config.json`：

     {
       "github": { "token": "ghp_xxx" }
     }

2. 在 Python 中测试：

   ```python
   from skills.github_mcp.github_mcp import GitHubMCP
   m = GitHubMCP()
   print(m.search_repos('Lianxin', per_page=5))
   print(m.get_readme('yuxinbeiyi', 'Lianxin')['content'][:400])
   ```

集成建议

- 将技能注册为 Lianxin 的 tool/function，允许模型通过 function calling 语义直接触发。
- 对于大文件或目录读取，先用 `get_file` 获取文件列表或目录内容，再按需读取具体文件。
- 注意速率限制：未经认证的请求每小时限制较低；推荐在设置中显式提示用户配置 token。

安全与权限

- 不要将 token 提交到版本库；将 token 存放在本地并加入忽略。
- 当仅需公共仓库读取时，可不配置 token，但会更容易遇到速率限制。
