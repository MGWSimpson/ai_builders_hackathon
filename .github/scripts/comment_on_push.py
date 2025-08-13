#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.request
import urllib.error

API_BASE = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN")
REPO_FULL = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. org/repo
SHA = os.environ.get("GITHUB_SHA", "")
REF = os.environ.get("GITHUB_REF", "")
BRANCH = REF.replace("refs/heads/", "")
PR_NUMBER_ENV = os.environ.get("GITHUB_PR_NUMBER")
BEFORE_SHA = os.environ.get("GITHUB_EVENT_BEFORE")  # from push event (optional, for changelog)

if not TOKEN:
    print("Missing GITHUB_TOKEN in environment; cannot call GitHub API.", file=sys.stderr)
    sys.exit(1)
if not REPO_FULL or "/" not in REPO_FULL:
    print(f"Invalid GITHUB_REPOSITORY: {REPO_FULL!r}", file=sys.stderr)
    sys.exit(1)

OWNER, REPO = REPO_FULL.split("/", 1)

def gh_api(path, method="GET", data=None, max_retries=3):
    """Minimal GitHub API client with retries and clear errors."""
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-push-comment-action/1.0",  # GitHub requires a UA
    }
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        body = None

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 204:
                    return None
                return json.load(resp)
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", errors="ignore")
            # 4xx usually won’t recover; 5xx might
            is_retryable = e.code >= 500
            print(f"[{attempt}/{max_retries}] GitHub API {e.code} {method} {path} -> {payload}", file=sys.stderr)
            if not is_retryable or attempt == max_retries:
                raise
            time.sleep(1.2 * attempt)
        except urllib.error.URLError as e:
            # network blip, retry
            print(f"[{attempt}/{max_retries}] Network error calling {method} {path}: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(1.2 * attempt)

def find_open_pr_for_branch_or_sha():
    """Return PR number if there is an open PR for this branch/sha, else None."""
    # Fast path: if the workflow provided PR number explicitly
    if PR_NUMBER_ENV:
        try:
            return int(PR_NUMBER_ENV)
        except ValueError:
            pass

    prs = []
    page = 1
    while True:
        res = gh_api(f"/repos/{OWNER}/{REPO}/pulls?state=open&per_page=100&page={page}")
        if not res:
            break
        prs.extend(res)
        if len(res) < 100:
            break
        page += 1

    # Prefer exact commit match; fall back to branch name
    pr = next((p for p in prs if p.get("head", {}).get("sha") == SHA), None)
    if not pr:
        pr = next((p for p in prs if p.get("head", {}).get("ref") == BRANCH), None)
    return pr["number"] if pr else None

def fetch_push_commit_messages(before, after):
    """
    Returns a list of (sha7, title_line) for commits between 'before'..'after'.
    Uses the compare endpoint; returns [] if unavailable (e.g., forced push or missing 'before').
    """
    if not before or not after or before == after:
        return []
    try:
        cmp = gh_api(f"/repos/{OWNER}/{REPO}/compare/{before}...{after}")
        commits = cmp.get("commits", []) if isinstance(cmp, dict) else []
        out = []
        for c in commits:
            sha7 = c.get("sha", "")[:7]
            msg = c.get("commit", {}).get("message", "")
            title = msg.splitlines()[0] if msg else "(no message)"
            out.append((sha7, title))
        return out
    except Exception as e:
        # Don’t fail the job just because compare didn’t work.
        print(f"Warning: could not fetch compare {before}...{after}: {e}", file=sys.stderr)
        return []

def build_comment_body():
    base = f"📦 New push detected on `{BRANCH}` at `{SHA[:7]}`."
    commits = fetch_push_commit_messages(BEFORE_SHA, SHA)
    if not commits:
        return base
    # Show up to 10 commits to keep comments tidy
    lines = [base, "", "Recent commits:"]
    cap = 10
    for i, (sha7, title) in enumerate(commits[:cap], 1):
        lines.append(f"- `{sha7}` {title}")
    if len(commits) > cap:
        lines.append(f"...and {len(commits) - cap} more.")
    return "\n".join(lines)

def main():
    pr_number = find_open_pr_for_branch_or_sha()
    if not pr_number:
        print("No open PR found for this branch/commit; skipping.")
        return

    body = build_comment_body()
    gh_api(f"/repos/{OWNER}/{REPO}/issues/{pr_number}/comments", method="POST", data={"body": body})
    print(f"Commented on PR #{pr_number}")

if __name__ == "__main__":
    main()
