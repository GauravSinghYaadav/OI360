"""
Commits & pushes the data/ folder (NSE.json.gz, symbols.json, history/*) to
GitHub after every collection cycle, so the repo always holds the latest
snapshot and the full 15-min history archive.

Requires these environment variables on Render (or wherever this runs):
  GITHUB_TOKEN     - a fine-grained PAT with "contents: write" on the repo
  GITHUB_REPO_URL  - e.g. https://github.com/yourname/oi-dashboard.git
  GIT_USER_NAME / GIT_USER_EMAIL - optional, used for the commit author

If GITHUB_TOKEN / GITHUB_REPO_URL are not set, sync() just logs and returns
(so local/mock runs don't fail).
"""
import subprocess
import sys

import config


def _run(cmd, cwd=config.BASE_DIR, check=True):
    print("  $", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print("   ", result.stdout.strip())
    if result.returncode != 0:
        print("   !", result.stderr.strip(), file=sys.stderr)
        if check:
            raise RuntimeError(result.stderr.strip())
    return result


def _authenticated_remote():
    if not config.GITHUB_REPO_URL.startswith("https://"):
        raise RuntimeError("GITHUB_REPO_URL must be an https:// URL for token auth")
    return config.GITHUB_REPO_URL.replace("https://", f"https://{config.GITHUB_TOKEN}@")


def sync(commit_message: str):
    if not config.GITHUB_TOKEN or not config.GITHUB_REPO_URL:
        print("  GitHub sync skipped: GITHUB_TOKEN / GITHUB_REPO_URL not set.")
        return

    _run(["git", "config", "user.name", config.GIT_USER_NAME])
    _run(["git", "config", "user.email", config.GIT_USER_EMAIL])

    # make sure origin points somewhere token-authenticated, without printing the token
    remote = _authenticated_remote()
    existing = _run(["git", "remote"], check=False).stdout.split()
    if "origin" in existing:
        _run(["git", "remote", "set-url", "origin", remote])
    else:
        _run(["git", "remote", "add", "origin", remote])

    _run(["git", "add", "data/"])
    status = _run(["git", "status", "--porcelain", "data/"], check=False)
    if not status.stdout.strip():
        print("  No data changes to commit.")
        return

    _run(["git", "commit", "-m", commit_message])
    _run(["git", "push", "origin", "HEAD:main"])
    print("  Pushed data update to GitHub.")


if __name__ == "__main__":
    sync("manual sync")
