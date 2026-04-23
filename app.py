import gc
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

DEFAULT_REPO_URL = "https://github.com/ginmp8/docs-explorer.git"
PREFERRED_DEFAULT_BRANCH = "main"
DEFAULT_DIRECTORY = "."
APP_HIDDEN_DIRNAME = ".docs-explorer"
CACHE_DIRNAME = "cache"
CACHE_META_FILENAME = "meta.json"


def run_git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def git_available() -> tuple[bool, str]:
    try:
        result = run_git(["--version"])
        return True, result.stdout.strip()
    except FileNotFoundError:
        return False, "Git was not found on PATH."
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return False, stderr or "Could not execute git --version."


def get_credential_helper() -> str:
    try:
        result = run_git(["config", "--global", "--get", "credential.helper"])
        value = result.stdout.strip()
        return value or "(not defined in global config)"
    except subprocess.CalledProcessError:
        return "(not defined in global config)"


def normalize_repo_url(repo_url: str) -> str:
    return repo_url.strip()


def normalize_directory(directory: str) -> str:
    normalized = directory.strip().strip("/")
    return normalized or "."


def make_cache_key(repo_url: str, branch: str) -> str:
    raw = f"{normalize_repo_url(repo_url)}|{branch.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_cache_root() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_HIDDEN_DIRNAME / CACHE_DIRNAME
    return Path.home() / APP_HIDDEN_DIRNAME / CACHE_DIRNAME


def get_cache_paths(repo_url: str, branch: str) -> dict[str, Path]:
    cache_key = make_cache_key(repo_url, branch)
    root = get_cache_root()
    cache_dir = root / cache_key
    repo_dir = cache_dir / "repo"
    meta_file = cache_dir / CACHE_META_FILENAME
    return {
        "cache_key": Path(cache_key),
        "cache_dir": cache_dir,
        "repo_dir": repo_dir,
        "meta_file": meta_file,
    }


def load_meta(meta_file: Path) -> dict[str, Any]:
    if not meta_file.exists():
        return {}
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_meta(meta_file: Path, data: dict[str, Any]) -> None:
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def get_remote_commit(repo_url: str, branch: str) -> str:
    result = run_git(["ls-remote", repo_url, f"refs/heads/{branch}"])
    line = result.stdout.strip()
    if not line:
        raise RuntimeError(f"Remote branch '{branch}' was not found in '{repo_url}'.")
    return line.split()[0]


def get_local_head_commit(repo_dir: Path) -> str:
    result = run_git(["rev-parse", "HEAD"], cwd=str(repo_dir))
    return result.stdout.strip()


def is_valid_repo_dir(repo_dir: Path) -> bool:
    return repo_dir.exists() and (repo_dir / ".git").exists()


def _remove_readonly(func, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        raise exc_info[1]


def remove_tree(path: Path, retries: int = 3) -> None:
    if not path.exists():
        return

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            shutil.rmtree(path, onerror=_remove_readonly)
            if not path.exists():
                return
        except Exception as exc:
            last_error = exc
            gc.collect()
            if attempt < retries - 1:
                time.sleep(0.25)

    if path.exists():
        if last_error is not None:
            raise RuntimeError(f"Could not remove '{path}': {last_error}") from last_error
        raise RuntimeError(f"Could not remove '{path}'.")


def remove_existing_repo_dir(repo_dir: Path) -> None:
    if not repo_dir.exists():
        return
    remove_tree(repo_dir)
    if repo_dir.exists():
        raise RuntimeError(f"Could not clear the existing cache in '{repo_dir}'.")


def clone_repo_into_cache(repo_url: str, branch: str, repo_dir: Path) -> None:
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    remove_existing_repo_dir(repo_dir)
    run_git(
        [
            "clone",
            "--branch",
            branch,
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            repo_url,
            str(repo_dir),
        ]
    )
    run_git(["sparse-checkout", "disable"], cwd=str(repo_dir))


def refresh_repo(repo_url: str, branch: str, repo_dir: Path) -> None:
    if not is_valid_repo_dir(repo_dir):
        clone_repo_into_cache(repo_url, branch, repo_dir)
        return

    try:
        run_git(["remote", "set-url", "origin", repo_url], cwd=str(repo_dir))
        run_git(["fetch", "--depth", "1", "origin", branch], cwd=str(repo_dir))
        run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=str(repo_dir))
        run_git(["reset", "--hard", f"origin/{branch}"], cwd=str(repo_dir))
        run_git(["clean", "-fd"], cwd=str(repo_dir))
        run_git(["sparse-checkout", "disable"], cwd=str(repo_dir))
    except subprocess.CalledProcessError:
        remove_existing_repo_dir(repo_dir)
        clone_repo_into_cache(repo_url, branch, repo_dir)



def clear_all_caches() -> int:
    st.session_state.pop("repo_ctx", None)
    st.session_state.pop("selected_md_path", None)
    gc.collect()

    cache_root = get_cache_root()
    if not cache_root.exists():
        return 0

    removed = 0
    failed: list[str] = []
    for child in list(cache_root.iterdir()):
        try:
            if child.is_dir():
                remove_tree(child)
                removed += 1
            else:
                child.unlink(missing_ok=True)
                removed += 1
        except Exception as exc:
            failed.append(f"{child}: {exc}")

    if failed:
        raise RuntimeError("Could not remove some caches:\n" + "\n".join(failed))

    return removed


def clear_current_cache() -> bool:
    repo_ctx = st.session_state.get("repo_ctx")
    st.session_state.pop("repo_ctx", None)
    st.session_state.pop("selected_md_path", None)
    gc.collect()

    if not repo_ctx:
        return False

    cache_dir = Path(repo_ctx["cache_dir"])
    if not cache_dir.exists():
        return False

    remove_tree(cache_dir)
    return True


def build_repo_context(repo_url: str, branch: str, force_refresh: bool = False) -> dict[str, Any]:
    repo_url = normalize_repo_url(repo_url)
    branch = branch.strip()

    if not repo_url.startswith("https://"):
        raise RuntimeError("Use an HTTPS GitHub URL to allow automatic authentication through Git/GCM.")
    
    paths = get_cache_paths(repo_url, branch)
    cache_dir = paths["cache_dir"]
    repo_dir = paths["repo_dir"]
    meta_file = paths["meta_file"]
    cache_key = paths["cache_key"].name

    remote_commit = get_remote_commit(repo_url, branch)
    meta = load_meta(meta_file)
    local_commit = None
    cache_hit = False

    should_refresh = force_refresh or not is_valid_repo_dir(repo_dir)
    if not should_refresh:
        try:
            local_commit = get_local_head_commit(repo_dir)
            cached_commit = meta.get("last_remote_commit")
            should_refresh = cached_commit != remote_commit or local_commit != remote_commit
            cache_hit = not should_refresh
        except subprocess.CalledProcessError:
            should_refresh = True

    if should_refresh:
        with st.spinner("Syncing repository files... If this is your first time, Git may open the browser for authentication."):
            refresh_repo(repo_url, branch, repo_dir)
        local_commit = get_local_head_commit(repo_dir)

    meta = {
        "repo_url": repo_url,
        "branch": branch,
        "last_remote_commit": remote_commit,
        "local_commit": local_commit,
        "cache_key": cache_key,
        "last_sync_utc": datetime.now(timezone.utc).isoformat(),
    }
    save_meta(meta_file, meta)

    return {
        "cache_key": cache_key,
        "cache_dir": str(cache_dir),
        "repo_dir": str(repo_dir),
        "meta_file": str(meta_file),
        "remote_commit": remote_commit,
        "local_commit": local_commit,
        "cache_hit": cache_hit,
        "repo_url": repo_url,
        "branch": branch,
        "last_sync_utc": meta["last_sync_utc"],
    }


def current_config_changed(repo_ctx: dict[str, Any] | None) -> bool:
    if not repo_ctx:
        return False
    return any(
        [
            normalize_repo_url(st.session_state.get("repo_url", "")) != repo_ctx.get("repo_url", ""),
            st.session_state.get("branch", "").strip() != repo_ctx.get("branch", ""),
        ]
    )


def load_repo(force_refresh: bool = False) -> None:
    if force_refresh or current_config_changed(st.session_state.get("repo_ctx")):
        st.session_state.pop("repo_ctx", None)

    if st.session_state.get("repo_ctx"):
        return

    repo_url = st.session_state["repo_url"]
    branch = st.session_state["branch"]
    st.session_state["repo_ctx"] = build_repo_context(repo_url, branch, force_refresh=force_refresh)


def list_markdown_paths_for_directory(repo_dir: str, selected_directory: str) -> list[str]:
    result = run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_dir)
    normalized_scope = normalize_directory(selected_directory)
    prefix = f"{normalized_scope.rstrip('/')}/" if normalized_scope != "." else ""
    selected_paths: list[str] = []

    for line in result.stdout.splitlines():
        repo_path = line.strip().replace("\\", "/")
        if not repo_path.lower().endswith(".md"):
            continue
        if normalized_scope == ".":
            selected_paths.append(repo_path)
        elif repo_path.startswith(prefix):
            selected_paths.append(repo_path[len(prefix):])

    return sorted(selected_paths, key=str.lower)


def get_repo_markdown_directories(repo_dir: str) -> list[str]:
    result = run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_dir)
    directories = {"."}

    for line in result.stdout.splitlines():
        repo_path = line.strip().replace("\\", "/")
        if not repo_path.lower().endswith(".md"):
            continue
        current = Path(repo_path).parent
        while True:
            current_str = str(current).replace("\\", "/")
            directories.add(current_str if current_str not in ("", ".") else ".")
            if current_str in ("", "."):
                break
            current = current.parent

    return sorted(directories, key=lambda value: (value != ".", value.lower()))


def get_branches(repo_url: str) -> tuple[list[str], str]:
    heads_result = run_git(["ls-remote", "--heads", repo_url])
    branches: list[str] = []
    for line in heads_result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branches.append(ref.removeprefix("refs/heads/"))

    branches = sorted(set(branches), key=str.lower)
    if not branches:
        raise RuntimeError(f"No branches were found in '{repo_url}'.")

    default_branch: str | None = None
    try:
        default_result = run_git(["ls-remote", "--symref", repo_url, "HEAD"])
        for line in default_result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ref:") and line.endswith("HEAD"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
                    default_branch = parts[1].removeprefix("refs/heads/")
                    break
    except subprocess.CalledProcessError:
        default_branch = None

    preferred = (
        PREFERRED_DEFAULT_BRANCH
        if PREFERRED_DEFAULT_BRANCH in branches
        else default_branch
        if default_branch in branches
        else branches[0]
    )
    return branches, preferred


def read_markdown(repo_dir: str, selected_directory: str, rel_path: str) -> str:
    normalized_directory = normalize_directory(selected_directory)
    base_path = Path(repo_dir) if normalized_directory == "." else Path(repo_dir) / normalized_directory
    path = base_path / rel_path
    return path.read_text(encoding="utf-8")


def bootstrap_defaults() -> None:
    st.session_state.setdefault("repo_url", DEFAULT_REPO_URL)
    st.session_state.setdefault("branch", PREFERRED_DEFAULT_BRANCH)
    st.session_state.setdefault("directory", DEFAULT_DIRECTORY)


def render_sidebar(repo_ctx: dict[str, Any] | None, available_dirs: list[str], branches: list[str]) -> None:
    with st.sidebar:
        st.header("Configuration")
        st.text_input("Repo HTTPS", key="repo_url")
        st.selectbox("Branch", branches, key="branch")

        if available_dirs:
            current_directory = normalize_directory(st.session_state.get("directory", DEFAULT_DIRECTORY))
            if current_directory not in available_dirs:
                st.session_state["directory"] = "." if "." in available_dirs else available_dirs[0]
            st.selectbox("Markdown directories in repo", available_dirs, key="directory")
        else:
            st.info("No Markdown directories were found in the repository yet.")

        st.caption("Select . to browse the repository root.")

        if st.button("Reload from Git", use_container_width=True):
            try:
                load_repo(force_refresh=True)
                st.success("Files reloaded.")
            except Exception as exc:
                st.error(str(exc))

        if st.button("Clean current cache", use_container_width=True):
            try:
                removed = clear_current_cache()
                st.session_state["skip_auto_load_once"] = True
                st.session_state.pop("directory", None)
                st.session_state.pop("selected_md_path", None)
                st.session_state["cache_action_message"] = (
                    "Current cache removed." if removed else "No current cache to remove."
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        if st.button("Clean all caches", use_container_width=True):
            try:
                removed = clear_all_caches()
                st.session_state["skip_auto_load_once"] = True
                st.session_state.pop("directory", None)
                st.session_state.pop("selected_md_path", None)
                st.session_state["cache_action_message"] = f"Caches removed: {removed}"
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        ok, version_text = git_available()
        st.divider()
        st.subheader("Git environment")
        st.write(version_text)
        st.write(f"credential.helper: `{get_credential_helper()}`")

        if repo_ctx:
            st.divider()
            st.subheader("Cache")
            st.write(f"cache_key: `{repo_ctx['cache_key']}`")
            st.write(f"cache_hit: `{repo_ctx['cache_hit']}`")
            st.write(f"remote_commit: `{repo_ctx['remote_commit']}`")
            st.write(f"local_commit: `{repo_ctx['local_commit']}`")
            st.write(f"last_sync_utc: `{repo_ctx['last_sync_utc']}`")
            st.write(f"cache_dir: `{repo_ctx['cache_dir']}`")
            st.write(f"cache_root: `{get_cache_root()}`")


def main() -> None:
    st.set_page_config(page_title="docs-explorer", layout="wide")
    bootstrap_defaults()

    st.title("docs-explorer")
    st.caption("Explore and render Markdown files from GitHub with automatic authentication through Git and persistent local cache.")

    ok, version_text = git_available()
    if not ok:
        st.error(version_text)
        st.info("Install Git for Windows. For automatic login, the machine needs Git with Git Credential Manager.")
        st.stop()

    branch_error: Exception | None = None
    branches: list[str] = []
    try:
        branches, preferred_branch = get_branches(st.session_state["repo_url"])
        current_branch = st.session_state.get("branch", "").strip()
        if current_branch not in branches:
            st.session_state["branch"] = preferred_branch
    except Exception as exc:
        branch_error = exc

    repo_ctx = st.session_state.get("repo_ctx")
    if current_config_changed(repo_ctx):
        st.session_state.pop("repo_ctx", None)
        repo_ctx = None

    cache_action_message = st.session_state.pop("cache_action_message", None)
    if cache_action_message:
        st.success(cache_action_message)

    load_error: Exception | None = None
    if branch_error is None and not st.session_state.pop("skip_auto_load_once", False):
        if repo_ctx is None:
            try:
                load_repo()
                repo_ctx = st.session_state.get("repo_ctx")
            except Exception as exc:
                load_error = exc

    available_dirs: list[str] = []
    if repo_ctx:
        try:
            available_dirs = get_repo_markdown_directories(repo_ctx["repo_dir"])
        except Exception:
            available_dirs = []

    render_sidebar(repo_ctx, available_dirs, branches)

    if branch_error is not None:
        st.error(str(branch_error))
        st.info("If the repository is private and this is your first HTTPS operation, Git/GCM may open the browser for login.")
        st.stop()

    if load_error is not None:
        if isinstance(load_error, subprocess.CalledProcessError):
            stderr = (load_error.stderr or "").strip()
            stdout = (load_error.stdout or "").strip()
            st.error("Failed to synchronize the repository.")
            if stderr:
                st.code(stderr, language="text")
            elif stdout:
                st.code(stdout, language="text")
            st.info(
                "If the repository is private and this is the first HTTPS operation, Git/GCM should open the browser for login. After that, click 'Reload from Git'."
            )
        else:
            st.error(str(load_error))
        st.stop()

    if repo_ctx is None:
        st.info("Use 'Reload from Git' whenever you want to load the repository.")
        st.stop()

    selected_directory = normalize_directory(st.session_state.get("directory", DEFAULT_DIRECTORY))
    if available_dirs and selected_directory not in available_dirs:
        selected_directory = "." if "." in available_dirs else available_dirs[0]
        st.session_state["directory"] = selected_directory

    md_paths = list_markdown_paths_for_directory(repo_ctx["repo_dir"], selected_directory)
    if not md_paths:
        st.warning("No .md files were found in the selected directory.")
        st.stop()

    selected_md = st.session_state.get("selected_md_path")
    if selected_md not in md_paths:
        selected_md = "README.md" if "README.md" in md_paths else md_paths[0]
        st.session_state["selected_md_path"] = selected_md

    selected = st.selectbox("Markdown files", md_paths, key="selected_md_path")
    content = read_markdown(repo_ctx["repo_dir"], selected_directory, selected)

    render_tab, raw_tab = st.tabs(["Rendered", "Raw Markdown"])
    with render_tab:
        st.markdown(content)
    with raw_tab:
        st.code(content, language="markdown")

    with st.expander("Local context"):
        st.write(f"Cached repo: `{repo_ctx['repo_dir']}`")
        st.write(f"Selected directory: `{selected_directory}`")
        st.write(f"Markdown files found: `{len(md_paths)}`")
        st.write(f"Markdown directories found in repo: `{len(available_dirs)}`")


if __name__ == "__main__":
    main()
