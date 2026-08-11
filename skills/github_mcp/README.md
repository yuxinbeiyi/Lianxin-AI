# GitHub Skill

The first release gives Lianxin read-only GitHub access through four tools:

- search public repositories
- read a repository README preview
- read a UTF-8 text-file preview
- list recent commits

All repository text is external, untrusted content and is marked as such before it reaches the model. File previews default to 4,000 characters and never exceed 6,000 characters.

## Configuration

Public repositories can be read without authentication. To increase GitHub API limits or access a repository you explicitly authorize, set a fine-grained, minimum-permission token locally:

```powershell
$env:LIANXIN_GITHUB_TOKEN = "github_pat_..."
```

The environment variable takes precedence over the local Lianxin configuration. Never commit a token. `github_create_issue` remains a future, separately authorized feature and is not exposed to the model in this release.

## Verification

```powershell
python -m unittest tests.test_github_mcp -v
python -c "import brain.skill_manager as sm; print(sm.activate_skill('GitHub'))"
```
