#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_release.py
示例：基于模板的 GitHub 发布流程验证脚本
依赖：requests, python-dotenv
pip install -r requirements.txt
"""
import os
import sys
import requests
import base64
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from datetime import datetime, timezone

# --------------------------
# 具体配置（示例，可通过替换此 dict 或将其加载为外部文件进行覆盖）
# --------------------------
CONFIG: Dict = {
    "ENV_CONFIG": {
        "env_file_name": ".mcp_env",
        "github_token_var": "MCP_GITHUB_TOKEN",
        "github_org_var": "GITHUB_EVAL_ORG",
    },
    "GITHUB_API_CONFIG": {
        "api_accept_format": "application/vnd.github.v3+json",
        "success_status_code": 200,
        "not_found_status_code": 404,
        "api_per_page": 100,
        "file_encoding": "utf-8",
        "merge_commit_parent_count": 1,
    },
    "VERIFICATION_FLOW_CONFIG": {
        "step_number_format": {
            "env_check": "1/6",
            "branch_check": "2/6",
            "file_check": "3/6",
            "pr_search": "4/6",
            "pr_merge_target": "5/6",
            "merge_method": "6/6",
        },
        "separator_length": 60,
        "success_message": "🎉 发布流程所有验证步骤通过！",
        "exit_code": {"success": 0, "failure": 1},
        # report filename placed at repository path ./mcpmark/release_verification/
        "report_file": "verification_report.txt",
    },
    "TARGET_RESOURCE_CONFIG": {
        "target_repo": "my-project",  # 请改为实际目标仓库名
        "branches": {"release_branch": "main", "base_branch": "main"},
    },
    "FILE_VERIFICATION_CONFIG": {
        "required_files": [
            {
                "name": "编码配置文件",
                "path": "src/encoding.rs",
                "branch": "main",
                "required_content": 'FormattingToken::MetaSep => "<|meta_sep|>"',
                "min_size": 500,
            },
            {
                "name": "注册表文件",
                "path": "src/registry.rs",
                "branch": "main",
                "required_contents": [
                    '(FormattingToken::MetaSep, "<|meta_sep|>")',
                    '(FormattingToken::MetaEnd, "<|meta_end|>")',
                ],
                "min_size": 500,
            },
            {
                "name": "版本配置文件",
                "path": "Cargo.toml",
                "branch": "main",
                "required_content": 'version = "1.1.0"',
                "min_size": 200,
            },
            {
                "name": "变更日志",
                "path": "CHANGELOG.md",
                "branch": "main",
                "required_keywords": [
                    "## [1.1.0] - 2025-08-07",
                    "MetaSep token mapping bug",
                    "Fixed MetaSep token",
                ],
                "min_size": 300,
            },
        ]
    },
    "PR_VERIFICATION_CONFIG": {
        "pr_title_keyword": "Release v1.1.0",
        "pr_state": "closed",
        "required_merge_method": "Squash and Merge",
    },
    # output directory relative to repository root where report will be written
    "OUTPUT_DIR": "mcpmark/release_verification"
}


# --------------------------
# 小工具函数
# --------------------------
def _load_env() -> Tuple[Optional[str], Optional[str]]:
    env_file = CONFIG["ENV_CONFIG"]["env_file_name"]
    # 如果存在 env 文件就加载它（允许在 CI 中通过环境变量注入）
    if os.path.exists(env_file):
        load_dotenv(env_file)
    github_token = os.environ.get(CONFIG["ENV_CONFIG"]["github_token_var"])
    github_org = os.environ.get(CONFIG["ENV_CONFIG"]["github_org_var"])
    return github_token, github_org


def _build_headers(github_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"token {github_token}",
        "Accept": CONFIG["GITHUB_API_CONFIG"]["api_accept_format"],
        "User-Agent": "GitHub-Release-Verification-Tool",
    }


def _call_github_api(endpoint: str, headers: Dict[str, str], org: str, repo: str, params: Optional[Dict] = None) -> Tuple[bool, Optional[Dict]]:
    url = f"https://api.github.com/repos/{org}/{repo}/{endpoint}"
    try:
        r = requests.get(url, headers=headers, timeout=15, params=params)
        if r.status_code == CONFIG["GITHUB_API_CONFIG"]["success_status_code"]:
            return True, r.json()
        elif r.status_code == CONFIG["GITHUB_API_CONFIG"]["not_found_status_code"]:
            print(f"[API 提示] 资源 {endpoint} 未找到（404）", file=sys.stderr)
            return False, None
        else:
            print(f"[API 错误] {endpoint} 返回状态 {r.status_code}：{r.text[:300]}", file=sys.stderr)
            return False, None
    except Exception as e:
        print(f"[API 异常] 调用 {endpoint} 出错：{e}", file=sys.stderr)
        return False, None


def _check_branch_exists(branch_name: str, headers: Dict[str, str], org: str, repo: str) -> bool:
    success, _ = _call_github_api(f"branches/{branch_name}", headers, org, repo)
    return success


def _get_file_content(file_path: str, branch: str, headers: Dict[str, str], org: str, repo: str) -> Optional[str]:
    success, data = _call_github_api(f"contents/{file_path}?ref={branch}", headers, org, repo)
    if not success or not data:
        return None
    try:
        content_b64 = data.get("content", "").replace("\n", "")
        return base64.b64decode(content_b64).decode(CONFIG["GITHUB_API_CONFIG"]["file_encoding"], errors="replace")
    except Exception as e:
        print(f"[文件解码错误] {file_path}: {e}", file=sys.stderr)
        return None


def _find_merged_pr(pr_title_keyword: str, base_branch: str, pr_state: str, headers: Dict[str, str], org: str, repo: str) -> Optional[Dict]:
    # Try first page(s) looking for matching merged PR
    page = 1
    per_page = CONFIG["GITHUB_API_CONFIG"]["api_per_page"]
    while True:
        endpoint = f"pulls"
        params = {"state": pr_state, "base": base_branch, "per_page": per_page, "page": page}
        success, pr_list = _call_github_api(f"{endpoint}", headers, org, repo, params=params)
        if not success or not pr_list:
            return None
        if not isinstance(pr_list, list):
            return None
        for pr in pr_list:
            title = pr.get("title", "")
            merged_at = pr.get("merged_at")
            if pr_title_keyword.lower() in title.lower() and merged_at:
                return pr
        # Stop if fewer than per_page results returned
        if len(pr_list) < per_page:
            break
        page += 1
    return None


def _verify_squash_merge(pr_number: int, headers: Dict[str, str], org: str, repo: str) -> Tuple[str, int]:
    """
    Return tuple: (method_str, parent_count)
    method_str: "Squash and Merge" | "OTHER" | "not found"
    parent_count: integer number of parents (0 if not found)
    """
    success, pr_detail = _call_github_api(f"pulls/{pr_number}", headers, org, repo)
    if not success or not pr_detail:
        return "not found", 0
    merge_commit_sha = pr_detail.get("merge_commit_sha")
    if not merge_commit_sha:
        return "not found", 0
    success, commit_detail = _call_github_api(f"commits/{merge_commit_sha}", headers, org, repo)
    if not success or not commit_detail:
        return "not found", 0
    parent_count = len(commit_detail.get("parents", []))
    commit_msg = commit_detail.get("commit", {}).get("message", "")
    expected_parent_count = CONFIG["GITHUB_API_CONFIG"]["merge_commit_parent_count"]
    if parent_count == expected_parent_count and f"#{pr_number}" in commit_msg:
        return "Squash and Merge", parent_count
    return "OTHER", parent_count


# --------------------------
# 验证步骤实现
# --------------------------
def _verify_environment() -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    print(f"\n{CONFIG['VERIFICATION_FLOW_CONFIG']['step_number_format']['env_check']} 验证环境配置...")
    token, org = _load_env()
    if not token:
        print(f"[环境错误] 未检测到环境变量 {CONFIG['ENV_CONFIG']['github_token_var']}", file=sys.stderr)
        return None, None, None
    if not org:
        print(f"[环境错误] 未检测到环境变量 {CONFIG['ENV_CONFIG']['github_org_var']}", file=sys.stderr)
        return None, None, None
    headers = _build_headers(token)
    print(f"[环境就绪] org={org}, token=已配置")
    return token, org, headers


def _verify_branches(headers: Dict[str, str], org: str, repo: str) -> bool:
    print(f"\n{CONFIG['VERIFICATION_FLOW_CONFIG']['step_number_format']['branch_check']} 验证分支存在性...")
    b = CONFIG["TARGET_RESOURCE_CONFIG"]["branches"]
    if not _check_branch_exists(b["release_branch"], headers, org, repo):
        print(f"[分支错误] 发布分支 {b['release_branch']} 不存在", file=sys.stderr)
        return False
    if not _check_branch_exists(b["base_branch"], headers, org, repo):
        print(f"[分支错误] 基础分支 {b['base_branch']} 不存在", file=sys.stderr)
        return False
    print(f"[分支验证通过] release={b['release_branch']}, base={b['base_branch']}")
    return True


def _verify_required_files(headers: Dict[str, str], org: str, repo: str) -> Tuple[bool, List[str]]:
    files = CONFIG["FILE_VERIFICATION_CONFIG"]["required_files"]
    print(f"\n{CONFIG['VERIFICATION_FLOW_CONFIG']['step_number_format']['file_check']} 验证关键文件（{len(files)}）...")
    ok = True
    failed_paths: List[str] = []
    for f in files:
        print(f"\n  验证 {f['name']} -> {f['path']} @ {f['branch']}")
        # Ignore system files explicitly (though target files are named)
        if f["path"] in [".DS_Store", "Thumbs.db"]:
            print(f"  [忽略] 系统文件 {f['path']}")
            continue
        content = _get_file_content(f["path"], f["branch"], headers, org, repo)
        if not content:
            print(f"  [文件错误] 无法读取 {f['path']}", file=sys.stderr)
            ok = False
            failed_paths.append(f['path'])
            continue
        if len(content) < f["min_size"]:
            print(f"  [文件错误] {f['path']} 大小 {len(content)} < {f['min_size']}", file=sys.stderr)
            ok = False
            failed_paths.append(f['path'])
            continue
        # 内容校验（单、多、关键词）
        if "required_content" in f:
            if f["required_content"] not in content:
                print(f"  [内容错误] 缺少: {f['required_content'][:80]}...", file=sys.stderr)
                ok = False
                failed_paths.append(f['path'])
                continue
        if "required_contents" in f:
            missing = [c for c in f["required_contents"] if c not in content]
            if missing:
                print(f"  [内容错误] 缺失多项: {missing}", file=sys.stderr)
                ok = False
                failed_paths.append(f['path'])
                continue
        if "required_keywords" in f:
            missing = [k for k in f["required_keywords"] if k not in content]
            if missing:
                print(f"  [内容错误] 缺失关键词: {missing}", file=sys.stderr)
                ok = False
                failed_paths.append(f['path'])
                continue
        print(f"  [文件验证通过] {f['path']}")
    return ok, failed_paths


def _verify_release_pr(headers: Dict[str, str], org: str, repo: str) -> Optional[Dict]:
    print(f"\n{CONFIG['VERIFICATION_FLOW_CONFIG']['step_number_format']['pr_search']} 查找发布 PR...")
    pr_cfg = CONFIG["PR_VERIFICATION_CONFIG"]
    base = CONFIG["TARGET_RESOURCE_CONFIG"]["branches"]["base_branch"]
    pr = _find_merged_pr(pr_cfg["pr_title_keyword"], base, pr_cfg["pr_state"], headers, org, repo)
    if not pr:
        print(f"[PR 错误] 未找到符合条件的已合并 PR（包含 '{pr_cfg['pr_title_keyword']}'）", file=sys.stderr)
        return None
    print(f"[PR 找到] #{pr.get('number')} - {pr.get('title')}")
    return pr


# --------------------------
# 报告生成
# --------------------------
def _write_report(report_dir: str, payload_lines: List[str]) -> bool:
    # ensure dir exists
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, CONFIG["VERIFICATION_FLOW_CONFIG"]["report_file"])
    try:
        with open(report_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(payload_lines))
        print(f"[报告已写入] {report_file}")
        return True
    except Exception as e:
        print(f"[报告写入错误] {e}", file=sys.stderr)
        return False


def run_verification(cfg: Dict) -> bool:
    global CONFIG
    CONFIG = cfg
    sep = "=" * CONFIG["VERIFICATION_FLOW_CONFIG"]["separator_length"]
    print(sep)
    print("开始执行 GitHub 发布流程验证（示例）")
    print(sep)

    _, org, headers = _verify_environment()
    if not org or not headers:
        # prepare fail report
        repo = CONFIG["TARGET_RESOURCE_CONFIG"]["target_repo"]
        lines = [
            "verification result: FAIL",
            f"repository: {org or 'UNKNOWN'}/{repo}",
            f"release branch: {CONFIG['TARGET_RESOURCE_CONFIG']['branches']['release_branch']}  base branch: {CONFIG['TARGET_RESOURCE_CONFIG']['branches']['base_branch']}",
            "release PR: not found",
            "merge method: not found  parents: 0",
            "files checked: 0/0  failed files: none",
            f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        ]
        out_dir = CONFIG.get("OUTPUT_DIR", "")
        if out_dir:
            _write_report(out_dir, lines)
        return False

    repo = CONFIG["TARGET_RESOURCE_CONFIG"]["target_repo"]

    # Step 2: branches
    if not _verify_branches(headers, org, repo):
        result = "FAIL"
        pr_line = "release PR: not found"
        merge_line = "merge method: not found  parents: 0"
        files_checked_line = "files checked: 0/0  failed files: none"
        lines = [
            f"verification result: {result}",
            f"repository: {org}/{repo}",
            f"release branch: {CONFIG['TARGET_RESOURCE_CONFIG']['branches']['release_branch']}  base branch: {CONFIG['TARGET_RESOURCE_CONFIG']['branches']['base_branch']}",
            pr_line,
            merge_line,
            files_checked_line,
            f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        ]
        _write_report(CONFIG.get("OUTPUT_DIR", ""), lines)
        return False

    # Step 3: files
    files_ok, failed_files = _verify_required_files(headers, org, repo)

    # Step 4: find PR
    pr = _verify_release_pr(headers, org, repo)
    pr_line = "release PR: not found"
    merge_line = "merge method: not found  parents: 0"
    pr_number = None
    pr_title = ""
    method_str = "not found"
    parents = 0
    if pr:
        pr_number = pr.get("number")
        pr_title = pr.get("title", "").replace("\n", " ")
        pr_line = f"release PR: #{pr_number}  title: {pr_title}"
        # Step 5 & 6: merge target and method
        # Verify merge target
        success, pr_detail = _call_github_api(f"pulls/{pr_number}", headers, org, repo)
        if success and pr_detail:
            actual_base = pr_detail.get("base", {}).get("ref")
            expected_base = CONFIG['TARGET_RESOURCE_CONFIG']['branches']['base_branch']
            # if target mismatch, set fail later
            if actual_base != expected_base:
                print(f"[合并目标错误] PR #{pr_number} 合并到「{actual_base}」，预期「{expected_base}」", file=sys.stderr)
        # verify merge method
        method_str, parents = _verify_squash_merge(pr_number, headers, org, repo)
        merge_line = f"merge method: {method_str}  parents: {parents}"
    else:
        # pr not found -> failure
        pass

    # decide overall PASS/FAIL
    passed_count = sum(1 for _ in CONFIG["FILE_VERIFICATION_CONFIG"]["required_files"]) - len(failed_files)
    total_required = len(CONFIG["FILE_VERIFICATION_CONFIG"]["required_files"])
    files_checked_line = f"files checked: {total_required - len(failed_files)}/{total_required}  failed files: {','.join(failed_files) if failed_files else 'none'}"

    overall_ok = all([
        files_ok,
        pr is not None,
        (pr is None or (method_str == "Squash and Merge")),
    ])

    result = "PASS" if overall_ok else "FAIL"

    lines = [
        f"verification result: {result}",
        f"repository: {org}/{repo}",
        f"release branch: {CONFIG['TARGET_RESOURCE_CONFIG']['branches']['release_branch']}  base branch: {CONFIG['TARGET_RESOURCE_CONFIG']['branches']['base_branch']}",
        pr_line,
        merge_line,
        files_checked_line,
        f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    ]

    out_dir = CONFIG.get("OUTPUT_DIR", "")
    if out_dir:
        _write_report(out_dir, lines)

    # ---- 新增：当验证通过时在控制台打印验证信息（report 内容） ----
    if overall_ok:
        print("\n" + sep)
        # success message from config (if any)
        success_msg = CONFIG['VERIFICATION_FLOW_CONFIG'].get('success_message')
        if success_msg:
            print(success_msg)
        # print the report lines to stdout
        for line in lines:
            print(line)
        print(sep + "\n")

    return overall_ok


if __name__ == "__main__":
    # 运行。若要外部自定义配置，可在此处加载 JSON 并传入 run_verification
    ok = run_verification(CONFIG)
    sys.exit(CONFIG["VERIFICATION_FLOW_CONFIG"]["exit_code"]["success"] if ok else CONFIG["VERIFICATION_FLOW_CONFIG"]["exit_code"]["failure"])
