#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
import urllib.error

API_BASE = os.environ.get("GITHUB_API_URL", "https://api.github.com")
TOKEN = os.environ["GITHUB_TOKEN"]

def gh_api(path, method="GET", data=None):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    else:
        body = None
    try:
        with urllib.request.urlopen(req, data=body) as resp:
            if resp.status == 204:
                return None
            return json.load(resp)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="ignore")
        print(f"GitHub API error {e.code} for {path}: {msg}", file=sys.stderr)
        raise

def main():
    repo_full = os.environ["GITHUB_REPOSITORY"]  # e.g. org/repo
    owner, repo = repo_full.split("/", 1)
    sha = os.environ.get("GITHUB_SHA", "")
    ref = os.environ.get("GITHUB_REF", "")
    branch = ref.replace("refs/heads/", "")

    # If provided (e.g., from pull_request_target), we can comment directly.
    pr_number_env = os.environ.get("GITHUB_PR_NUMBER")

    if pr_number_env:
        pr_number = int(pr_number_env)
    else:
        # List open PRs and find by exact commit, then by branch name.
        prs = []
        page = 1
        while True:
            res = gh_api(f"/repos/{owner}/{repo}/pulls?state=open&per_page=100&page={page}")
            if not res:
                break
            prs.extend(res)
            if len(res) < 100:
                break
            page += 1

        pr = next((p for p in prs if p.get("head", {}).get("sha") == sha), None)
        if not pr:
            pr = next((p for p in prs if p.get("head", {}).get("ref") == branch), None)

        if not pr:
            print("No open PR found for this branch/commit; skipping.")
            return

        pr_number = pr["number"]

    body = f"📦 New push detected on `{branch}` at `{sha[:7]}`."
    gh_api(f"/repos/{owner}/{repo}/issues/{pr_number}/comments", method="POST", data={"body": body})
    print(f"Commented on PR #{pr_number}")

if __name__ == "__main__":
    main()
