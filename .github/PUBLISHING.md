# GitHub Actions Workflow for Publishing

This package uses GitHub Actions to automatically publish to PyPI when a new release is created.

## Setup Instructions

### Option 1: Trusted Publishing (Recommended)

Trusted publishing uses OpenID Connect (OIDC) tokens for secure, passwordless authentication.

1. **Configure PyPI Trusted Publisher**:
   - Go to https://pypi.org/manage/account/publishing/
   - Add a new publisher with these settings:
     - PyPI Project Name: `cli-runner-mcp`
     - Owner: `<your-github-username-or-org>`
     - Repository name: `<your-repo-name>`
     - Workflow name: `publish.yml`
     - Environment name: `pypi`

2. **Configure TestPyPI** (optional, for testing):
   - Go to https://test.pypi.org/manage/account/publishing/
   - Add the same publisher settings with environment name: `testpypi`

3. **Create GitHub Environments**:
   - Go to your repository → Settings → Environments
   - Create environment named `pypi`
   - Optionally create `testpypi` environment

### Option 2: API Token (Alternative)

If you prefer using API tokens:

1. **Generate PyPI API Token**:
   - Go to https://pypi.org/manage/account/token/
   - Create a new API token with scope for `cli-runner-mcp` project

2. **Add Secret to GitHub**:
   - Go to repository → Settings → Secrets and variables → Actions
   - Add new repository secret:
     - Name: `PYPI_API_TOKEN`
     - Value: Your PyPI API token

3. **Update workflow** to use token instead of trusted publishing:
   ```yaml
   - name: Publish to PyPI
     uses: pypa/gh-action-pypi-publish@release/v1
     with:
       password: ${{ secrets.PYPI_API_TOKEN }}
   ```

## Creating a Release

1. **Update version** in `pyproject.toml`:
   ```toml
   version = "0.2.0"
   ```

2. **Commit and push** changes:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 0.2.0"
   git push
   ```

3. **Create a git tag**:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

4. **Create GitHub Release**:
   - Go to repository → Releases → Create a new release
   - Choose the tag (e.g., `v0.2.0`)
   - Add release title and description
   - Click "Publish release"

5. **Workflow automatically runs**:
   - Builds the package
   - Publishes to TestPyPI (if configured)
   - Publishes to PyPI
   - Check Actions tab to monitor progress

## Workflow Features

- **Automatic Build**: Builds wheel and source distribution
- **Dual Publishing**: Publishes to both TestPyPI and PyPI
- **Secure**: Uses trusted publishing (OIDC) for authentication
- **Artifact Storage**: Stores built distributions as artifacts
- **Python 3.x**: Uses latest stable Python version for building

## Testing Before Release

To test the workflow without publishing:

1. Create a test release as a draft
2. Check the Actions tab for workflow runs
3. Delete the draft if needed

## Troubleshooting

- **Permission denied**: Ensure trusted publisher is configured or API token is set
- **Project not found**: Create the project on PyPI first or use TestPyPI
- **Version conflict**: Ensure version in `pyproject.toml` is new
- **Workflow not triggering**: Check that release is "published", not "draft"
