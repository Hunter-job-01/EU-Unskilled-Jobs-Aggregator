from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import pytest
from src.eligibility import evaluate, source_digest, load_private, PrivateConfigError, EligibleStore
from src.private_state import seal, unseal
from cryptography.fernet import Fernet

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
TEXT = 'Warehouse work. Basic English A2 accepted. No licence required. No experience needed. No diploma needed. Overseas work permit sponsorship provided.'
PROFILE = {'schema': 1, 'languages': {'en': 'A2'}, 'licences': [], 'roles': ['warehouse'], 'authorization': 'needs_sponsorship'}

def review():
    quotes = dict(role='Warehouse work.', language='Basic English A2 accepted.', licence='No licence required.', experience='No experience needed.', education='No diploma needed.', authorization='Overseas work permit sponsorship provided.')
    facts = {d: {'quote': q, 'status': 'waived'} for d,q in quotes.items()}
    facts['role'].update(status='required', value='warehouse')
    facts['language'].update(status='required', value=[{'en': 'A2'}])
    facts['authorization'].update(status='required', value='overseas_sponsorship_explicit')
    return {'url':'https://example.com/job', 'checked_at':NOW.isoformat(), 'source_sha256':source_digest(TEXT), 'facts':facts}

def check(r, p=None, **kw):
    return evaluate(p or PROFILE, r, url='https://example.com/job', text=kw.get('text',TEXT), now=kw.get('now',NOW))

def test_explicit_full_evidence_passes():
    assert check(review()) == 'pass'

@pytest.mark.parametrize('domain',['role','language','licence','experience','education','authorization'])
def test_unknown_never_passes(domain):
    r=review();r['facts'][domain]['status']='unknown'
    assert check(r)=='review'

@pytest.mark.parametrize('status',['preferred','waived'])
def test_optional_licence_does_not_disqualify(status):
    r=review();r['facts']['licence'].update(status=status,value=['B'])
    assert check(r)=='pass'

@pytest.mark.parametrize('domain,value',[('language',[{'de':'A2'}]),('language',[{'en':'B1'}]),('licence',['B']),('licence',['forklift']),('authorization','existing_permit_required'),('authorization','no_sponsorship'),('role','chemistry')])
def test_explicit_conflicts_reject(domain,value):
    r=review();r['facts'][domain].update(status='required',value=value)
    assert check(r)=='reject'

def test_language_or_not_and():
    r=review();r['facts']['language']['value']=[{'de':'B1'},{'en':'A2'}]
    assert check(r)=='pass'
    r['facts']['language']['value']=[{'de':'B1','en':'A2'}]
    assert check(r)=='reject'

def test_experience_unknown_not_invented():
    r=review();r['facts']['experience'].update(status='required',value={'role':'warehouse','months':6})
    assert check(r)=='review'

def test_temporary_protection_is_not_sponsorship():
    r=review();r['facts']['authorization']['value']='temporary_protection_support'
    assert check(r)=='review'

@pytest.mark.parametrize('alteration',['expired','future','wrong_url','changed','missing_quote','missing_domain'])
def test_bad_evidence_holds(alteration):
    r=review();kw={}
    if alteration=='expired': kw['now']=NOW+timedelta(days=2)
    if alteration=='future': kw['now']=NOW-timedelta(days=1)
    if alteration=='wrong_url': r['url']='https://example.com/other'
    if alteration=='changed': kw['text']=TEXT+' Czech B2 required.'
    if alteration=='missing_quote': r['facts']['language']['quote']='No language required'
    if alteration=='missing_domain': del r['facts']['education']
    assert check(r,**kw)=='review'

def test_invalid_profile_does_not_leak():
    with pytest.raises(PrivateConfigError) as e: load_private({'JOB_FIT_PROFILE':'secret-name'})
    assert 'secret-name' not in str(e.value)

def test_empty_reviews_allowed_but_no_generic_fallback():
    p,r=load_private({'JOB_FIT_PROFILE':json.dumps(PROFILE)})
    assert p==PROFILE and r=={}

def test_state_migration_and_encryption():
    key=Fernet.generate_key().decode();state={'destinations':{'abc':{'last_digest_utc':'2026-11-02','notified_uids':{'private-job':'2026-11-02'}}}}
    assert unseal(state,key)==state
    encrypted=seal(state,key)
    assert 'private-job' not in json.dumps(encrypted)
    assert unseal(encrypted,key)==state
    with pytest.raises(ValueError): unseal(encrypted,Fernet.generate_key().decode())

def test_profile_not_written_to_store():
    class S:
        data_dir='/tmp'
        _store={'x':{'uid':'x','url':'https://example.com/job','score':100}}
    store=S();before=deepcopy(store._store)
    result=EligibleStore(store,PROFILE,{},lambda url:TEXT)
    assert result.load()=={}
    assert store._store==before

def test_notifier_missing_profile_cannot_send(tmp_path, monkeypatch):
    from src.notifier import send_digest
    class S:
        data_dir=str(tmp_path)
        _store={}
    class Session:
        def post(self,*a,**kw): raise AssertionError('must not send')
    cfg={'telegram':{'private_fit_required':True},'storage':{}}
    assert send_digest(S(),cfg,environ={'TELEGRAM_BOT_TOKEN':'not-a-real-token','TELEGRAM_CHAT_IDS':'1,2'},session=Session())==0

def test_filtered_empty_view_preserves_prior_marks(tmp_path):
    from src.notifier import _DestinationStore
    class S:
        data_dir=str(tmp_path)
        _store={'old':{'uid':'old'}}
    view=EligibleStore(S(),PROFILE,{},lambda u:'')
    state={'notified_uids':{'old':'2026-11-01'}}
    dest=_DestinationStore(view,state,lambda s:None)
    dest.mark_notified([])
    assert 'old' in state['notified_uids']

def test_bad_encryption_key_fails_closed(tmp_path, monkeypatch):
    from src.notifier import send_digest
    key=Fernet.generate_key().decode()
    (tmp_path/'telegram_state.json').write_text(json.dumps(seal({'destinations':{}},key)))
    monkeypatch.setenv('JOB_FIT_STATE_KEY',Fernet.generate_key().decode())
    class S:
        data_dir=str(tmp_path)
        _store={}
    class Session:
        def post(self,*a,**kw):raise AssertionError('must never send')
    env={'JOB_FIT_PROFILE':json.dumps(PROFILE),'JOB_FIT_STATE_KEY':Fernet.generate_key().decode(),'TELEGRAM_BOT_TOKEN':'offline','TELEGRAM_CHAT_IDS':'1,2'}
    assert send_digest(S(),{'telegram':{'private_fit_required':True}},environ=env,session=Session())==0
