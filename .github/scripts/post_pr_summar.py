#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
TOKEN = os.environ["GITHUB_TOKEN"]
REPO_FULL = os.environ["GITHUB_REPOSITORY"]
OWNER, REPO = REPO_FULL.split("/", 1)
PR_NUMBER = int(os.environ["GITHUB_PR_NUMBER"])

USER_AGENT = "pr-summary-comment/1.0"
MARKER = "<!-- pr-summary-bot -->"  # used to update the same comment

def gh_api(path, method="GET", data=None, max_retries=3):
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 204:
                    return None
                return json.load(resp)
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", errors="ignore")
            retry = 500 <= e.code < 600
            print(f"[{attempt}/{max_retries}] {method} {path} -> {e.code}: {txt}", file=sys.stderr)
            if not retry or attempt == max_retries:
                raise
            time.sleep(1.3 * attempt)
        except urllib.error.URLError as e:
            print(f"[{attempt}/{max_retries}] network error: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(1.3 * attempt)

def paginate(path):
    page = 1
    while True:
        data = gh_api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        if not data:
            break
        for item in data:
            yield item
        if len(data) < 100:
            break
        page += 1

def get_pr():
    return gh_api(f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}")

def get_pr_commits():
    return list(paginate(f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/commits"))

def get_pr_files():
    return list(paginate(f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/files"))

def find_existing_summary_comment():
    comments = list(paginate(f"/repos/{OWNER}/{REPO}/issues/{PR_NUMBER}/comments"))
    for c in reversed(comments):
        body = c.get("body", "") or ""
        if MARKER in body:
            return c["id"]
    return None

def create_comment(body):
    return gh_api(f"/repos/{OWNER}/{REPO}/issues/{PR_NUMBER}/comments", method="POST", data={"body": body})

def update_comment(comment_id, body):
    return gh_api(f"/repos/{OWNER}/{REPO}/issues/comments/{comment_id}", method="PATCH", data={"body": body})

def plural(n, word):
    return f"{n} {word}{'' if n==1 else 's'}"

def format_summary():
    pr = get_pr()
    commits = get_pr_commits()
    files = get_pr_files()

    title = pr.get("title", "(no title)")
    author = pr.get("user", {}).get("login", "unknown")
    head = pr.get("head", {})
    base = pr.get("base", {})
    head_ref = head.get("ref", "?")
    base_ref = base.get("ref", "?")
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    changed_files = pr.get("changed_files", len(files))
    commits_count = len(commits)

    # Top commit messages (first lines), latest first
    recent_commits = []
    for c in reversed(commits[-10:]):  # up to 10 recent
        sha7 = c.get("sha", "")[:7]
        msg = (c.get("commit", {}) or {}).get("message", "") or "(no message)"
        title_line = msg.splitlines()[0]
        recent_commits.append(f"- `{sha7}` {title_line}")

    # Top changed files by (add+del)
    file_summaries = []
    for f in files:
        changes = (f.get("additions", 0) or 0) + (f.get("deletions", 0) or 0)
        filename = f.get("filename", "")
        status = f.get("status", "")
        file_summaries.append((changes, status, filename, f.get("additions", 0), f.get("deletions", 0)))
    file_summaries.sort(reverse=True, key=lambda x: x[0])
    top_files = []
    for chg, status, name, add, dele in file_summaries[:10]:
        top_files.append(f"- `{name}` ({status}, +{add}/-{dele})")

    size_label = "🟩 small"
    total_delta = additions + deletions
    if total_delta > 1000: size_label = "🟥 huge"
    elif total_delta > 500: size_label = "🟧 large"
    elif total_delta > 200: size_label = "🟨 medium"

    lines = [
        MARKER,
        f"### PR Summary",
        f"**Title:** {title}",
        f"**Author:** @{author}",
        f"**Branch:** `{head_ref}` → `{base_ref}`",
        f"**Scope:** {plural(changed_files, 'file')}, {plural(commits_count, 'commit')}, **+{additions} / -{deletions}** ({size_label})",
    ]

    if recent_commits:
        lines += ["", "#### Recent commits", *recent_commits]
    if top_files:
        lines += ["", "#### Most-changed files", *top_files]

    # Linked issues (basic detection from PR body)
    body = pr.get("body") or ""
    # naive scan for #123 patterns
    linked = set()
    for token in body.replace("\n", " ").split():
        if token.startswith("#") and token[1:].isdigit():
            linked.add(token)
    if linked:
        lines += ["", f"#### Linked issues", "- " + " ".join(sorted(linked))]

    lines += ["", "_I’ll update this comment when new commits are pushed._"]
    return "\n".join(lines)

def main():
    summary = format_summary()
    existing_id = find_existing_summary_comment()
    if existing_id:
        update_comment(existing_id, summary)
        print(f"Updated PR summary comment (id {existing_id})")
    else:
        create_comment(summary)
        print("Created PR summary comment")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed to post PR summary: {e}", file=sys.stderr)
        sys.exit(1)
