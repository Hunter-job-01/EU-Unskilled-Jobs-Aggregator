"""Offline proof using real secret config; no candidate values printed."""
import json
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import load_config
from src.storage import JobStore
from src.eligibility import load_private, EligibleStore
from src.notifier import send_digest
from src.private_state import unseal

cfg = load_config()
assert cfg['telegram']['private_fit_required'] is True
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
