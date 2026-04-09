"""GitHub service: clone, pull, branch, apply fixes, commit, push, create PR."""

import logging
import re
from pathlib import Path
from typing import Optional, Tuple

import httpx
from git import GitCommandError, Repo as GitRepo
from git.exc import InvalidGitRepositoryError

from app.config import get_settings
from app.models.repo import Repo
from app.models.fix import Fix

logger = logging.getLogger(__name__)
settings = get_settings()

REPOS_BASE = Path("repos")

GITHUB_API = "https://api.github.com"


# ─── PAT helpers ─────────────────────────────────────────────────────────────


def _get_pat_with_source(repo: Repo) -> Tuple[Optional[str], str]:
    """
    Resolve which PAT to use for this repo and return (token, source_label).

    Preference order:
      1. The repo's own `pat` column (set via the Edit Repo modal in the UI)
      2. The global `GITHUB_DEFAULT_PAT` from .env, as a last-resort fallback

    The source label is included in error messages so the user knows exactly
    which PAT to fix when something fails.
    """
    if repo.pat:
        return repo.pat, "per-repo PAT"
    if settings.github_default_pat:
        return settings.github_default_pat, "GITHUB_DEFAULT_PAT (.env fallback)"
    return None, "no PAT configured"


def _get_pat(repo: Repo) -> Optional[str]:
    pat, _ = _get_pat_with_source(repo)
    return pat


def _get_auth_url(url: str, pat: str) -> str:
    """Inject the PAT into a GitHub HTTPS URL for git operations."""
    # Use 'x-access-token' as the username — works for both classic and
    # fine-grained PATs and avoids leaking the literal token format in remotes.
    return re.sub(r"https://", f"https://x-access-token:{pat}@", url)


def _parse_owner_repo(github_url: str) -> Optional[Tuple[str, str]]:
    """
    Parse owner/repo from a GitHub HTTPS URL. Tolerates trailing slash and .git.
    """
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", github_url)
    if not m:
        return None
    return m.group(1), m.group(2)


# ─── Service ─────────────────────────────────────────────────────────────────


class GitHubService:

    # ── Cloning ──────────────────────────────────────────────────────────────

    def clone_or_pull(self, repo: Repo) -> Path:
        """
        Ensure the repo is cloned at REPOS_BASE/<repo.id>. If already cloned,
        fetch + reset to origin/<branch>. Returns the absolute clone path.
        """
        REPOS_BASE.mkdir(parents=True, exist_ok=True)
        clone_dir = (REPOS_BASE / repo.id).resolve()

        url = repo.github_url
        pat, source = _get_pat_with_source(repo)
        auth_url = _get_auth_url(url, pat) if pat else url
        logger.info(f"[github] clone_or_pull using {source} for {repo.name}")

        if (clone_dir / ".git").exists():
            try:
                git_repo = GitRepo(str(clone_dir))
                origin = git_repo.remotes.origin
                origin.set_url(auth_url)
                origin.fetch()
                try:
                    git_repo.git.checkout(repo.branch)
                except GitCommandError:
                    git_repo.git.checkout("-B", repo.branch, f"origin/{repo.branch}")
                git_repo.git.reset("--hard", f"origin/{repo.branch}")
                logger.info(f"Updated existing clone {clone_dir} → origin/{repo.branch}")
                return clone_dir
            except (InvalidGitRepositoryError, GitCommandError) as e:
                logger.warning(f"Existing clone unusable ({e}); re-cloning")
                import shutil as _shutil
                _shutil.rmtree(clone_dir, ignore_errors=True)

        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        GitRepo.clone_from(auth_url, str(clone_dir), branch=repo.branch)
        logger.info(f"Cloned {repo.github_url}@{repo.branch} → {clone_dir}")
        return clone_dir

    # Backwards-compatible alias
    def clone_repo(self, repo: Repo) -> Path:
        return self.clone_or_pull(repo)

    # ── PAT validation ───────────────────────────────────────────────────────

    async def validate_pat(self, repo: Repo) -> dict:
        """
        Validate that the resolved PAT for this repo can authenticate with
        GitHub AND has write access to the target repo. Returns:
            {ok: bool, source: str, user: str|None, scopes: str|None,
             can_write: bool, message: str}

        Never raises — failures come back in the dict so the caller can decide
        whether to fail the operation with a useful error message.
        """
        pat, source = _get_pat_with_source(repo)
        if not pat:
            return {
                "ok": False, "source": source,
                "user": None, "scopes": None, "can_write": False,
                "message": (
                    f"No PAT is configured for repo '{repo.name}'. "
                    "Set one via the Edit (pencil) icon on the repo card, "
                    "or set GITHUB_DEFAULT_PAT in backend/.env."
                ),
            }

        owner_repo = _parse_owner_repo(repo.github_url)
        if not owner_repo:
            return {
                "ok": False, "source": source,
                "user": None, "scopes": None, "can_write": False,
                "message": f"Could not parse owner/repo from URL: {repo.github_url}",
            }
        owner, repo_name = owner_repo

        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # 1. Validate the token + identify the user
                user_resp = await client.get(f"{GITHUB_API}/user", headers=headers)
                if user_resp.status_code == 401:
                    return {
                        "ok": False, "source": source,
                        "user": None, "scopes": None, "can_write": False,
                        "message": (
                            f"The {source} is invalid or expired. "
                            f"Update it via the Edit icon on the repo card."
                        ),
                    }
                if user_resp.status_code != 200:
                    return {
                        "ok": False, "source": source,
                        "user": None, "scopes": None, "can_write": False,
                        "message": f"GitHub /user returned {user_resp.status_code}: {user_resp.text[:200]}",
                    }

                user = user_resp.json().get("login")
                # X-OAuth-Scopes is set for classic PATs; fine-grained PATs
                # don't return it (their scopes are per-repo).
                scopes = user_resp.headers.get("x-oauth-scopes") or ""

                # 2. Check repo-level write access
                repo_resp = await client.get(
                    f"{GITHUB_API}/repos/{owner}/{repo_name}",
                    headers=headers,
                )
                if repo_resp.status_code == 404:
                    return {
                        "ok": False, "source": source,
                        "user": user, "scopes": scopes, "can_write": False,
                        "message": (
                            f"The {source} (user '{user}') cannot see "
                            f"{owner}/{repo_name}. Check the URL or grant the PAT "
                            f"access to this repo."
                        ),
                    }
                if repo_resp.status_code != 200:
                    return {
                        "ok": False, "source": source,
                        "user": user, "scopes": scopes, "can_write": False,
                        "message": f"GitHub /repos returned {repo_resp.status_code}",
                    }

                permissions = (repo_resp.json().get("permissions") or {})
                can_write = bool(permissions.get("push") or permissions.get("admin"))
                if not can_write:
                    return {
                        "ok": False, "source": source,
                        "user": user, "scopes": scopes, "can_write": False,
                        "message": (
                            f"The {source} (user '{user}') only has read access to "
                            f"{owner}/{repo_name}. To push branches and create PRs, "
                            f"the PAT needs write access. For classic PATs, ensure "
                            f"the 'repo' scope. For fine-grained PATs, set 'Contents: "
                            f"Read & Write' AND 'Pull requests: Read & Write'."
                        ),
                    }

                return {
                    "ok": True, "source": source,
                    "user": user, "scopes": scopes, "can_write": True,
                    "message": (
                        f"OK — using {source} (user '{user}') with write access "
                        f"to {owner}/{repo_name}."
                    ),
                }

        except httpx.HTTPError as exc:
            return {
                "ok": False, "source": source,
                "user": None, "scopes": None, "can_write": False,
                "message": f"Could not reach GitHub API: {exc}",
            }

    # ── Branch / commit / push ───────────────────────────────────────────────

    def create_fix_branch(self, repo: Repo, scan_id: str) -> str:
        """
        Create (or reset) a branch named sonar-fix/<scan_id[:8]> from the base
        branch. Always uses 'checkout -B' so re-applying fixes for the same
        scan starts from a clean slate.
        """
        clone_dir = REPOS_BASE / repo.id
        git_repo = GitRepo(str(clone_dir))
        branch_name = f"sonar-fix/{scan_id[:8]}"
        # -B = create or reset to the start point
        git_repo.git.checkout("-B", branch_name, repo.branch)
        logger.info(f"[github] create_fix_branch {branch_name} from {repo.branch}")
        return branch_name

    def apply_fixes(self, repo: Repo, fixes: list[Fix]) -> int:
        """Write each fix's `fixed_code` back to disk. Returns the count written."""
        clone_dir = REPOS_BASE / repo.id
        count = 0
        for fix in fixes:
            try:
                file_path = clone_dir / fix.file_path.lstrip("/")
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(fix.fixed_code, encoding="utf-8")
                count += 1
            except Exception as exc:
                logger.warning(f"Could not write fix for {fix.file_path}: {exc}")
        logger.info(f"[github] apply_fixes wrote {count}/{len(fixes)} files")
        return count

    def commit_fixes(self, repo: Repo, fixes: list[Fix]) -> None:
        """
        Stage all changes and commit. Sets local git user.email and user.name
        on the repo so commits work even in fresh clones with no global git
        identity. Raises RuntimeError with an actionable message if there are
        no changes to commit.
        """
        clone_dir = REPOS_BASE / repo.id
        git_repo = GitRepo(str(clone_dir))

        # Force a local identity for this clone — fresh clones don't have one
        # and the global git config may not be set on the server either.
        with git_repo.config_writer() as cw:
            cw.set_value("user", "email", "sonaragent@noreply.local")
            cw.set_value("user", "name", "SonarAgent")

        git_repo.git.add("-A")

        # Are there actually changes staged?
        try:
            staged_diff = git_repo.index.diff("HEAD")
        except Exception:
            staged_diff = []
        if not staged_diff:
            raise RuntimeError(
                "No file changes to commit — the generated fixes did not modify "
                "any files on disk. Check that the issue file paths in SonarQube "
                "match the actual paths in the cloned repo."
            )

        severities = sorted({
            f.issue.severity for f in fixes if hasattr(f, "issue") and f.issue
        })
        sev_str = ", ".join(severities) if severities else "mixed"
        git_repo.index.commit(
            f"fix: auto-fix {len(fixes)} SonarQube issue(s) [{sev_str}]\n\n"
            "Generated by SonarAgent."
        )
        logger.info(f"[github] commit_fixes committed {len(fixes)} fixes")

    def push_branch(self, repo: Repo, branch_name: str) -> None:
        """
        Push the fix branch to origin with --force-with-lease (safer than
        plain --force; refuses to push if the remote moved unexpectedly).
        Raises RuntimeError with an actionable message on auth failure.
        """
        clone_dir = REPOS_BASE / repo.id
        git_repo = GitRepo(str(clone_dir))
        pat, source = _get_pat_with_source(repo)
        if not pat:
            raise RuntimeError(
                f"Cannot push fix branch — no PAT available. "
                f"Set one via the Edit icon on the repo card."
            )

        git_repo.remotes.origin.set_url(_get_auth_url(repo.github_url, pat))
        try:
            git_repo.git.push(
                "origin", branch_name,
                "--set-upstream", "--force-with-lease",
            )
            logger.info(f"[github] pushed branch {branch_name} using {source}")
        except GitCommandError as exc:
            err = str(exc)
            # git error messages embed the auth credential URL — strip it
            err_clean = re.sub(r"https://[^@]*@", "https://", err)[-1500:]
            if "denied" in err.lower() or "403" in err or "permission" in err.lower():
                raise RuntimeError(
                    f"Push denied. The {source} does not have write access to "
                    f"{repo.github_url}. For classic PATs, ensure 'repo' scope. "
                    f"For fine-grained PATs, ensure 'Contents: Read & Write'. "
                    f"Update the PAT via the Edit icon on the repo card."
                    f"\n\nGit said: {err_clean}"
                )
            raise RuntimeError(f"git push failed using {source}: {err_clean}")

    # ── Pull request creation ────────────────────────────────────────────────

    async def create_pr(
        self, repo: Repo, branch_name: str, title: str, body: str,
    ) -> dict:
        """
        Create a GitHub pull request — or return an existing one if a PR for
        this head branch already exists.

        Returns a structured dict (never raises):
            {
              "ok":       bool,
              "url":      str | None,    # PR HTML URL if created or found
              "existed":  bool,          # True if we returned an existing PR
              "source":   str,           # which PAT was used
              "error":    str | None,    # actionable error message
            }
        """
        pat, source = _get_pat_with_source(repo)
        if not pat:
            return {
                "ok": False, "url": None, "existed": False, "source": source,
                "error": (
                    f"No PAT configured for repo '{repo.name}'. "
                    "Set one via the Edit icon on the repo card."
                ),
            }

        owner_repo = _parse_owner_repo(repo.github_url)
        if not owner_repo:
            return {
                "ok": False, "url": None, "existed": False, "source": source,
                "error": f"Could not parse owner/repo from URL: {repo.github_url}",
            }
        owner, repo_name = owner_repo

        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Has a PR already been opened for this branch? If so, reuse it.
            try:
                existing = await client.get(
                    f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls",
                    headers=headers,
                    params={"head": f"{owner}:{branch_name}", "state": "open"},
                )
                if existing.status_code == 200:
                    items = existing.json()
                    if items:
                        url = items[0].get("html_url")
                        logger.info(f"[github] reusing existing PR {url}")
                        return {
                            "ok": True, "url": url, "existed": True,
                            "source": source, "error": None,
                        }
            except httpx.HTTPError as exc:
                logger.debug(f"Could not check existing PRs: {exc}")

            # 2. Create a new PR
            try:
                resp = await client.post(
                    f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls",
                    headers=headers,
                    json={
                        "title": title,
                        "head":  branch_name,
                        "base":  repo.branch,
                        "body":  body,
                        "maintainer_can_modify": True,
                    },
                )
            except httpx.HTTPError as exc:
                return {
                    "ok": False, "url": None, "existed": False, "source": source,
                    "error": f"Network error calling GitHub API: {exc}",
                }

            if resp.status_code == 201:
                url = resp.json().get("html_url")
                logger.info(f"[github] created PR {url} using {source}")
                return {
                    "ok": True, "url": url, "existed": False,
                    "source": source, "error": None,
                }

            # 3. Build an actionable error message based on status code +
            #    GitHub's response body.
            try:
                err = resp.json()
                msg = err.get("message", "") or ""
                errors = err.get("errors", []) or []
                if errors:
                    err_msgs = []
                    for e in errors:
                        if isinstance(e, dict):
                            err_msgs.append(e.get("message") or str(e))
                        else:
                            err_msgs.append(str(e))
                    msg = f"{msg} — {'; '.join(err_msgs)}"
                doc_url = err.get("documentation_url") or ""
            except Exception:
                msg = resp.text[:400]
                doc_url = ""

            hint_by_status = {
                401: (
                    f"The {source} is INVALID or expired. Update it via the "
                    f"Edit (pencil) icon on the repo card."
                ),
                403: (
                    f"The {source} does not have permission to create PRs on "
                    f"{owner}/{repo_name}. Common causes:\n"
                    f"  • PAT scope is missing — for classic PATs ensure 'repo'; "
                    f"for fine-grained PATs ensure 'Pull requests: Read & Write' "
                    f"AND 'Contents: Read & Write'.\n"
                    f"  • If the org enforces SAML SSO, you must explicitly "
                    f"authorize the PAT for the org (PAT settings → Configure SSO).\n"
                    f"  • The PAT belongs to a user without write access on this repo."
                ),
                404: (
                    f"GitHub returned 404 — the {source} cannot see "
                    f"{owner}/{repo_name}, or the base branch '{repo.branch}' "
                    f"does not exist."
                ),
                422: (
                    f"GitHub rejected the PR request. Common causes:\n"
                    f"  • The branch '{branch_name}' was not pushed to origin yet.\n"
                    f"  • There are no commits between '{branch_name}' and "
                    f"'{repo.branch}' (no actual changes).\n"
                    f"  • A PR already exists for this branch (we tried to detect "
                    f"this above but the lookup may have failed)."
                ),
            }
            hint = hint_by_status.get(resp.status_code, "")

            full_error = f"GitHub API {resp.status_code}: {msg}"
            if hint:
                full_error += f"\n\n{hint}"
            if doc_url:
                full_error += f"\n\nDocs: {doc_url}"

            logger.error(
                f"[github] PR creation failed — status={resp.status_code} "
                f"source={source} body={resp.text[:500]}"
            )
            return {
                "ok": False, "url": None, "existed": False,
                "source": source, "error": full_error,
            }


github_service = GitHubService()