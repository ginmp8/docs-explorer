# docs-explorer

A Streamlit app to browse and render Markdown files from a GitHub repository with automatic authentication through local Git and Git Credential Manager.

## Features

- uses Git over HTTPS, with automatic browser-based sign-in when required by Git/GCM
- keeps a persistent local cache under `.docs-explorer/cache`
- loads the repository once per repo/branch commit and then navigates Markdown directories locally from cache
- defaults to repository root `.`
- lists all repository directories that contain Markdown files
- lists all branches available in the repository
- opens Markdown in a rendered tab by default, with a separate raw Markdown tab
- supports cleaning only the current cache or all caches

## Requirements

- Git available on PATH
- on Windows, Git for Windows with Git Credential Manager is recommended
- repository access over HTTPS

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Behavior

- **Repo HTTPS**: the GitHub repository URL, for example `https://github.com/owner/repo.git`
- **Branch**: loaded from the remote repository. `main` is preferred when available. If `main` does not exist, the app uses the repository default branch or the first branch returned.
- **Markdown directories in repo**: directory browser built from all directories that contain `.md` files. Selecting `.` browses the repository root.
- changing the Markdown directory does **not** re-download the repository when the commit hash is unchanged; navigation happens from the local cache
- **Reload from Git** forces a sync for the current repo and branch
- **Clean current cache** removes only the active cache entry
- **Clean all caches** removes everything under the cache root

## Cache location

On Windows:

```text
%LOCALAPPDATA%\.docs-explorer\cache
```

On other platforms:

```text
~/.docs-explorer/cache
```
