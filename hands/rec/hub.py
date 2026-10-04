#!/usr/bin/env python3
"""Upload a hand recorder export to the hand dataset on Hugging Face (DESIGN.md "Upload").

The window runs this as a child process (so Cancel can end it, and huggingface_hub stays out
of the window's process); it also runs from the command line. It uses huggingface_hub (in the
dev container: python3-huggingface-hub, from setup/dev-container.sh) with the login that
`hub.py login` (the window's Log in) or `hf auth login` saved. Nobody types a token anywhere.

`login` is huggingface_hub's browser login (OAuth device code, as `hf auth login` does): it gets a
link and a short code, the person enters the code in their browser and approves, and the token goes
straight from Hugging Face into huggingface_hub's token file. This process saves it; it never
prints it, and the window never sees it.

An upload:
  1. checks the export with validate.py, and stops on errors;
  2. stops if this export (same SHA256SUMS) was uploaded before, unless --again;
  3. stops while CONSENT.md or UPLOAD.md is a draft, unless FT_HANDREC_ALLOW_UPLOAD=1;
  4. checks the login (whoami: a read-only token can't open a pull request) and access to the
     dataset (auth_check: a gated dataset's terms must be accepted first);
  5. opens the pull request first (a draft, empty), so its link can be shown while the files go,
     and records it under "uploads" in session.json with "status": "started";
  6. upload_folder(..., revision="refs/pr/N") to contributions/<contributor>/<session>; a retry of
     the same export goes on in the same pull request while it's still open;
  7. marks the record {"repo", "pr_url", "pr_num", "uploaded", "export_sha", "status": "done"}.
--dry-run does all of it except the network calls (4 to 6) and recording (5, 7), and says what
it would upload.

The dataset is HF_DATASET; FT_HANDREC_DATASET overrides it (a test repo, for rehearsals).

usage: hub.py [--base DIR] whoami [--json]
       hub.py [--base DIR] login [--json]
       hub.py [--base DIR] upload SESSION [--dry-run] [--again] [--json]
With --json, each line of output is one JSON object: {"phase", "text", "fraction"} as it goes,
with {"phase": "opened", "pr_url"} once the pull request exists, then {"done": {...}} or
{"error": {"kind", "text", "link", "errors"}}. login says {"phase": "code", "url", "code",
"expires_in"} once it has the link, then {"done": {"name", "role"}}. Exit status 0: done.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import takes  # noqa: E402  (next to this file)

# The dataset contributions go to (DESIGN.md "Licensing and consent").
HF_DATASET = "DeeJanuz/frametop-hands"
DATASET_ENV = "FT_HANDREC_DATASET"
ALLOW_ENV = "FT_HANDREC_ALLOW_UPLOAD"
CONSENT_PATH = os.path.join(HERE, "CONSENT.md")
UPLOAD_PATH = os.path.join(HERE, "UPLOAD.md")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
TOKENS_URL = "https://huggingface.co/settings/tokens"
# Quiet huggingface_hub: no progress bars on stderr, no telemetry or update hints.
HF_ENV = {"HF_HUB_DISABLE_PROGRESS_BARS": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_UPDATE_CHECK": "1"}


class HubError(Exception):
    """What went wrong, for people: kind (below), text, a link to open, validation errors.
    Kinds: missing (no huggingface_hub), login, read-token, terms, not-found, permission,
    network, hub, invalid, duplicate, closed, no-export."""

    def __init__(self, kind, text, link="", errors=None, extra=None):
        super().__init__(text)
        self.kind, self.text, self.link = kind, text, link
        self.errors = errors or []
        self.extra = extra or {}

    def as_dict(self):
        return dict(self.extra, kind=self.kind, text=self.text, link=self.link, errors=self.errors)


def dataset_id():
    """HF_DATASET, or FT_HANDREC_DATASET when set."""
    repo = os.environ.get(DATASET_ENV, "").strip() or HF_DATASET
    if not REPO_RE.match(repo):
        raise HubError("not-found", f"{DATASET_ENV}={repo!r} isn't a dataset id like owner/name")
    return repo


def dataset_url(repo=None):
    return "https://huggingface.co/datasets/" + (repo or dataset_id())


def is_draft(path):
    try:
        with open(path, encoding="utf-8") as f:
            return "DRAFT" in f.readline()
    except OSError:
        return False


def texts_draft():
    return is_draft(CONSENT_PATH) or is_draft(UPLOAD_PATH)


def upload_allowed():
    """Real uploads: once the texts aren't drafts, or for the maintainer's rehearsal against a
    test repo (FT_HANDREC_ALLOW_UPLOAD=1)."""
    return not texts_draft() or os.environ.get(ALLOW_ENV) == "1"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- records in session.json

def export_sha(export_dir):
    """The export's identity: the SHA256 of its SHA256SUMS ("" if there's none)."""
    try:
        with open(os.path.join(export_dir, "SHA256SUMS"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def uploads(store, session):
    meta = takes.read_json(os.path.join(store.session_dir(session), "session.json"))
    return [u for u in meta.get("uploads") or [] if isinstance(u, dict)]


def previous_upload(store, session, sha):
    """The latest finished upload of this same export, or None. (Records from before
    2026-10-03 have no status: they were written only when an upload finished.)"""
    same = [u for u in uploads(store, session) if sha and u.get("export_sha") == sha
            and u.get("status", "done") == "done"]
    return same[-1] if same else None


def unfinished_upload(store, session, sha, repo):
    """The latest upload of this same export to repo that opened its pull request and didn't
    finish, or None."""
    same = [u for u in uploads(store, session) if sha and u.get("export_sha") == sha and u.get("repo") == repo
            and u.get("status") == "started" and u.get("pr_num")]
    return same[-1] if same else None


def record_upload(store, session, record):
    """Add record to session.json's "uploads". The file keeps its time: an upload doesn't
    change the recordings, so the export mustn't count as out of date because of it
    (takes.Store.sessions compares times)."""
    path = os.path.join(store.session_dir(session), "session.json")
    try:
        st = os.stat(path)
    except OSError:
        st = None
    meta = takes.read_json(path)
    records = meta.setdefault("uploads", [])
    same = [i for i, u in enumerate(records) if isinstance(u, dict) and record.get("pr_num")
            and u.get("pr_num") == record["pr_num"] and u.get("repo") == record.get("repo")]
    if same:
        records[same[-1]] = record   # the same pull request: started, then done
    else:
        records.append(record)
    takes.write_json(path, meta)
    if st:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


# ---------------------------------------------------------------- the pull request's text

def describe(summary, report, sha, nbytes):
    """(commit message, commit description) from validate's summary of the manifest."""
    s = summary
    objects = ", ".join(s.get("objects") or []) or "none"
    if s.get("own_objects"):
        objects += f" (+{s['own_objects']} of their own)"
    message = f"Hands: session {s.get('session')} from {s.get('contributor')}"
    lines = ["Contribution to the Frametop hand dataset, uploaded from the hand recorder.", "",
             f"- Session: {s.get('session')}",
             f"- Contributor: {s.get('contributor')}",
             f"- Takes: {s.get('takes')} ({s.get('minutes')} minutes, {s.get('sets')} frame sets)",
             f"- Lighting: {s.get('lighting') or 'not given'}",
             f"- Objects: {objects}",
             f"- Controllers: {s.get('controllers') or 'not given'}",
             f"- Consent version: {s.get('consent_version')}",
             f"- Tool: {s.get('tool')}",
             f"- Size: {takes.human_bytes(nbytes)}",
             f"- SHA256 of SHA256SUMS: {sha}", "",
             "Checked with hands/rec/validate.py: no errors" + (f", {len(report.warnings)} warnings:"
                                                                 if report.warnings else ".")]
    lines += [f"- {w}" for w in report.warnings]
    return message, "\n".join(lines) + "\n"


# ---------------------------------------------------------------- talking to the Hub

def _hf():
    os.environ.update({k: v for k, v in HF_ENV.items() if k not in os.environ})
    try:
        import huggingface_hub
    except ImportError:
        raise HubError("missing", "huggingface_hub isn't installed in the dev container: run setup/dev-container.sh "
                                  "(or: pip install --user huggingface_hub)") from None
    return huggingface_hub


def explain(e, repo):
    """A huggingface_hub (or network) exception as a HubError."""
    if isinstance(e, HubError):
        return e
    try:
        from huggingface_hub import errors as hf
    except ImportError:
        hf = None
    url = dataset_url(repo)
    if hf is not None:
        if isinstance(e, hf.LocalTokenNotFoundError):
            return HubError("login", "You're not logged in to Hugging Face. Press Log in.")
        if isinstance(e, hf.GatedRepoError):
            return HubError("terms", f"Accept the dataset's terms first: open {url}, read them and accept them, "
                                     "then try again.", url)
        if isinstance(e, hf.RepositoryNotFoundError):
            return HubError("not-found", f"The dataset {repo} wasn't found, or it's private and your account has "
                                         "no access to it.", url)
        if isinstance(e, hf.HfHubHTTPError):
            status = getattr(getattr(e, "response", None), "status_code", None)
            first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
            if status == 401:
                return HubError("login", "Hugging Face didn't accept your login (it may have expired or been "
                                         "revoked). Log in again.")
            if status == 403:
                return HubError("permission", "Your login isn't allowed to open a pull request. Log in again, "
                                              "and allow everything the Hugging Face page asks for.")
            return HubError("hub", f"Hugging Face refused the upload ({status or 'no status'}): {first}")
    try:
        import httpx
        net = (httpx.TransportError, OSError)
    except ImportError:
        net = (OSError,)
    if isinstance(e, net):
        return HubError("network", f"Couldn't reach huggingface.co ({type(e).__name__}: {e}). Check the network "
                                   "and try again: files already sent usually aren't sent twice.")
    return HubError("hub", f"{type(e).__name__}: {e}")


def whoami():
    """{"name", "role"} for the saved login; HubError if there's none or it doesn't work.
    role: "write", "read", "fineGrained" or ""."""
    hf = _hf()
    try:
        info = hf.HfApi().whoami()
    except Exception as e:
        raise explain(e, dataset_id()) from None
    token = ((info.get("auth") or {}).get("accessToken") or {})
    return {"name": info.get("name", ""), "role": token.get("role", "")}


def login(progress=None):
    """The browser login: progress("code", text, url=, code=, expires_in=) once the link is ready,
    then wait (up to the code's expiry: 5 minutes on 2026-10-03) for the person to approve it, and save
    the token. Returns whoami(). Uses huggingface_hub's own device code helpers (1.x)."""
    tell = progress or (lambda phase, text, **extra: None)
    _hf()
    try:
        from huggingface_hub._login import _save_oauth_token
        from huggingface_hub.errors import DeviceCodeError
        from huggingface_hub.utils._oauth_device import poll_device_token, request_device_code
    except ImportError:
        raise HubError("missing", "This huggingface_hub has no browser login: update the dev container "
                                  "(setup/dev-container.sh).") from None
    try:
        info = request_device_code()
        tell("code", "Approve the login in your browser", url=info["verification_uri_complete"],
             code=info["user_code"], expires_in=info["expires_in"])
        _save_oauth_token(poll_device_token(info))
    except DeviceCodeError as e:
        raise HubError("login", f"The login didn't go through: {e}. Press Log in to try again.") from None
    except Exception as e:
        raise explain(e, dataset_id()) from None
    return whoami()


def upload(store, session, dry_run=False, again=False, progress=None, log=None):
    """Upload exports/<session> (the steps in this file's docstring). progress(phase, text,
    fraction or None); log(text) for the dry run's account. Returns the result; raises HubError."""
    tell = progress or (lambda phase, text, fraction=None, **extra: None)
    say = log or (lambda text: None)
    import validate
    repo = dataset_id()
    try:
        export = store.export_dir(session)
    except ValueError as e:
        raise HubError("no-export", str(e)) from None
    if not os.path.isfile(os.path.join(export, "manifest.json")):
        raise HubError("no-export", f"No export of session {session}: export it first")

    tell("check", "Checking the export", 0.0)
    report = validate.validate(export, progress=lambda f, text: tell("check", text, f))
    if not report.ok:
        raise HubError("invalid", f"The export has {len(report.errors)} problems, so it can't be uploaded. Export "
                                  "the session again; if that doesn't help, report it.", errors=report.errors)
    summary = report.summary
    contributor = summary.get("contributor", "")
    sha = export_sha(export)
    before = previous_upload(store, session, sha)
    if before and not again:
        raise HubError("duplicate", f"This export was uploaded already, on {before.get('uploaded', '?')}: "
                                    f"{before.get('pr_url', '')}", before.get("pr_url", ""), extra={"previous": before})
    if not dry_run and not upload_allowed():
        raise HubError("closed", "Contributions aren't open yet: the texts are drafts waiting for a legal review. "
                                 f"(For a rehearsal against a test repo: {ALLOW_ENV}=1.)")
    path_in_repo = f"contributions/{contributor}/{session}"
    message, description = describe(summary, report, sha, report.bytes)
    files = []
    for root, _, names in os.walk(export):
        files += [os.path.relpath(os.path.join(root, n), export) for n in names]
    plan = {"repo": repo, "repo_type": "dataset", "folder_path": export, "path_in_repo": path_in_repo,
            "create_pr": True, "commit_message": message, "commit_description": description,
            "files": len(files), "bytes": report.bytes, "warnings": report.warnings}

    if dry_run:
        say(f"dry run: would check the login (whoami) and access to {repo} (auth_check)")
        say(f"dry run: would upload_folder {len(files)} files, {takes.human_bytes(report.bytes)}, "
            f"from {export} to datasets/{repo}/{path_in_repo}, as a pull request")
        for rel in sorted(files):
            say(f"  {rel}  {takes.human_bytes(os.path.getsize(os.path.join(export, rel)))}")
        say(f"dry run: commit message: {message}")
        say("dry run: commit description:\n" + description.rstrip())
        record = {"repo": repo, "pr_url": "", "uploaded": now_iso(), "export_sha": sha, "status": "done"}
        say(f"dry run: would record in session.json's uploads: {json.dumps(record)}")
        tell("done", "Dry run: nothing was uploaded", 1.0)
        return dict(plan, dry_run=True, pr_url="", record=record)

    hf = _hf()
    api = hf.HfApi()
    tell("login", "Checking your Hugging Face login", None)
    try:
        who = api.whoami()
    except Exception as e:
        raise explain(e, repo) from None
    role = ((who.get("auth") or {}).get("accessToken") or {}).get("role", "")
    if role == "read":
        raise HubError("read-token", "Your saved login is a read-only token, so it can't open a pull request. "
                                     "Log in again.")
    tell("access", f"Checking access to {repo}", None)
    try:
        api.auth_check(repo, repo_type="dataset")
    except Exception as e:
        raise explain(e, repo) from None
    # The pull request first, so its link shows while the files go (and the person can plug the
    # headset in and leave it). A retry of this export goes on in its pull request if it's open.
    tell("open", "Opening your pull request", None)
    pr = None
    before = unfinished_upload(store, session, sha, repo)
    if before:
        try:
            d = api.get_discussion_details(repo, int(before["pr_num"]), repo_type="dataset")
            if d.is_pull_request and d.status in ("draft", "open"):
                pr = d
        except Exception:
            pr = None
    if pr is None:
        try:
            pr = api.create_pull_request(repo, message, description=description, repo_type="dataset")
        except Exception as e:
            raise explain(e, repo) from None
    pr_url = getattr(pr, "url", "") or f"{dataset_url(repo)}/discussions/{pr.num}"
    record = {"repo": repo, "pr_url": pr_url, "pr_num": pr.num, "started": now_iso(), "export_sha": sha,
              "status": "started"}
    record_upload(store, session, record)
    tell("opened", f"Pull request #{pr.num} is open. Uploading {len(files)} files ({takes.human_bytes(report.bytes)})",
         None, pr_url=pr_url)
    try:
        api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=export, path_in_repo=path_in_repo,
                          revision=f"refs/pr/{pr.num}", commit_message=message, commit_description=description)
    except Exception as e:
        raise explain(e, repo) from None
    try:   # a pull request opened through the API stays a draft until it's marked open
        if api.get_discussion_details(repo, pr.num, repo_type="dataset").status == "draft":
            api.change_discussion_status(repo, pr.num, "open", repo_type="dataset")
    except Exception:
        pass   # the maintainer can open it
    record = dict(record, uploaded=now_iso(), status="done")
    record_upload(store, session, record)
    tell("done", "Uploaded", 1.0)
    return dict(plan, dry_run=False, pr_url=pr_url, pr_num=pr.num, user=who.get("name", ""), record=record)


# ---------------------------------------------------------------- the command line

def main():
    ap = argparse.ArgumentParser(description="Upload a hand recorder export to the hand dataset on Hugging Face.")
    ap.add_argument("--base", default=takes.DEFAULT_BASE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("whoami", help="show the saved Hugging Face login")
    w.add_argument("--json", action="store_true")
    li = sub.add_parser("login", help="log in to Hugging Face in the browser")
    li.add_argument("--json", action="store_true")
    u = sub.add_parser("upload", help="upload exports/SESSION as a pull request")
    u.add_argument("session")
    u.add_argument("--dry-run", action="store_true", help="no network: say what would be uploaded")
    u.add_argument("--again", action="store_true", help="upload even if this export was uploaded before")
    u.add_argument("--json", action="store_true", help="JSON lines, for the window")
    a = ap.parse_args()

    def emit(obj):
        print(json.dumps(obj), flush=True)

    try:
        if a.cmd == "whoami":
            who = whoami()
            if a.json:
                emit({"done": who})
            else:
                print(f"logged in as {who['name']} (token role: {who['role'] or 'unknown'})")
            return 0
        if a.cmd == "login":
            def code(phase, text, **extra):
                if a.json:
                    emit(dict(extra, phase=phase, text=text))
                else:
                    print(f"Open {extra['url']} and approve the code {extra['code']}. Waiting...", flush=True)
            who = login(code)
            if a.json:
                emit({"done": who})
            else:
                print(f"logged in as {who['name']}")
            return 0
        if a.json:
            def progress(phase, text, fraction=None, **extra):
                emit(dict(extra, phase=phase, text=text, fraction=fraction))

            def log(text):
                emit({"log": text})
        else:
            last = {}

            def progress(phase, text, fraction=None, **extra):
                if extra.get("pr_url"):
                    print(f"pull request: {extra['pr_url']}", flush=True)
                line = text if fraction is None else f"{fraction * 100:5.1f}% {text}"
                if (phase, text) != last.get("key") or fraction in (0.0, 1.0):
                    print(line, file=sys.stderr, flush=True)
                last["key"] = (phase, text)

            def log(text):
                print(text, flush=True)
        if hasattr(os, "setpriority"):
            try:
                os.setpriority(os.PRIO_PROCESS, 0, 19)   # validation reads and hashes everything: stay out of VR's way
            except OSError:
                pass
        result = upload(takes.Store(a.base), a.session, dry_run=a.dry_run, again=a.again, progress=progress, log=log)
        if a.json:
            emit({"done": result})
        elif result["dry_run"]:
            print(f"dry run done: {result['files']} files would go to datasets/{result['repo']}/{result['path_in_repo']}")
        else:
            print(f"uploaded: {result['pr_url']}")
        return 0
    except HubError as e:
        if a.json:
            emit({"error": e.as_dict()})
        else:
            print(f"error ({e.kind}): {e.text}", file=sys.stderr)
            for line in e.errors:
                print(f"  {line}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
