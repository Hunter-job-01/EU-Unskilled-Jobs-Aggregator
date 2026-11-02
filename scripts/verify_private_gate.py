"""Offline proof using real secret config; no candidate values printed.

Also scans the tracked public tree for secret-shaped values (bot tokens,
API keys, private keys). Shape-based only: no real secret value is ever
written into this file, and candidate text is never printed. The scan is
check-only; it does not touch GitHub Pages or repository visibility.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config import load_config  # noqa: E402
from src.storage import JobStore  # noqa: E402
from src.eligibility import load_private, EligibleStore  # noqa: E402
from src.notifier import send_digest  # noqa: E402
from src.private_state import unseal  # noqa: E402

# Shape patterns for secrets that must never appear in the public tree.
# Deliberately shape-based: listing real leaked values here would republish them.
SECRET_PATTERNS = {
    "telegram_bot_token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "gemini_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "google_oauth_client_secret": re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{28}\b"),
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|github_pat|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
}

_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2"}


def scan_public_tree(root: Path) -> list:
    """Return [(relpath, pattern_name)] for secret-shaped values in tracked files."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=str(root), capture_output=True, text=True, check=True
    ).stdout.split()
    findings = []
    for rel in files:
        path = root / rel
        if path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, rx in SECRET_PATTERNS.items():
            if rx.search(text):
                findings.append((rel, name))
    return findings


def main() -> None:
    cfg = load_config()
    assert cfg['telegram']['private_fit_required'] is True
    cfg['telegram']['automatic_requirements'] = False  # offline safety proof has no live detail requests
    profile, reviews = load_private(os.environ)
    state = unseal(json.loads(Path('data/telegram_state.json').read_text()), os.environ['JOB_FIT_STATE_KEY'])
    assert len(state['destinations']) == 2
    assert all(d.get('notified_uids') and d.get('last_digest_utc') for d in state['destinations'].values())
    store = JobStore(cfg['storage'])
    records = store.load()
    # With no positively reviewed evidence, no job can pass, regardless of score.
    view = EligibleStore(store, profile, {}, lambda url: (_ for _ in ()).throw(AssertionError('No request expected')))
    assert not view.load()
    class NoTransport:
        def post(self, *args, **kwargs):
            raise AssertionError('Unverified vacancy must never be sent')
    with tempfile.TemporaryDirectory() as tmp:
        cfg['storage']['data_dir'] = tmp
        private = JobStore(cfg['storage'])
        private.load()
        private._store = dict(records)
        Path(tmp, 'telegram_state.json').write_text(Path('data/telegram_state.json').read_text())
        env = dict(os.environ, JOB_FIT_REVIEWS='{}', TELEGRAM_BOT_TOKEN='offline-verification-only', TELEGRAM_CHAT_IDS='offline-a,offline-b')
        assert send_digest(private, cfg, environ=env, session=NoTransport()) == 0
        after = unseal(json.loads(Path(tmp, 'telegram_state.json').read_text()), os.environ['JOB_FIT_STATE_KEY'])
        assert all(after['destinations'][k] == v for k,v in state['destinations'].items())
    print('PASS: private configuration readable; encrypted prior audience gates preserved; unknown jobs withheld; zero transport calls; no source requests.')


if __name__ == "__main__":
    findings = scan_public_tree(REPO_ROOT)
    assert not findings, "secret-shaped values in public tree: " + ", ".join(
        "{} ({})".format(rel, name) for rel, name in findings
    )
    print("PASS: no secret-shaped values in tracked public files.")
    main()
