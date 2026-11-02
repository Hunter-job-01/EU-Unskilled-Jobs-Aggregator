from datetime import datetime,timezone
import pytest
from src.automatic_requirements import automatic_review
from src.eligibility import evaluate,AutomaticEligibleStore

NOW=datetime(2026,11,2,12,tzinfo=timezone.utc)
TEXT='Warehouse worker. Basic English accepted. No licence required. No experience required. No qualifications required. Visa sponsorship available for overseas applicants.'
RECORD={'uid':'x','title':'Warehouse worker','url':'https://example.com/job','description':TEXT,'score':70}
PROFILE={'schema':1,'roles':['warehouse'],'languages':{'en':'A2'},'licences':[],'authorization':'needs_sponsorship'}

def test_automatic_positive_is_possible_without_manual_review():
    status,evidence=automatic_review(RECORD,TEXT,now=NOW)
    assert status=='extracted'
    assert evaluate(PROFILE,evidence,url=RECORD['url'],text=TEXT,now=NOW)=='pass'

@pytest.mark.parametrize('addition',[' Fluent German required.',' A valid driving licence is required.',' Must hold a valid work permit.',' Visa sponsorship not available.',' Temporary protection only.'])
def test_conflicting_text_never_passes(addition):
    s,_=automatic_review(RECORD,TEXT+addition,now=NOW)
    assert s in ('review','reject')

@pytest.mark.parametrize('field,value',[('requiredDrivingLicenses',['b']),('positionLanguages',[{'languageCode':'de','requiredSkillLevel':'b1'}]),('requiredYearsOfExperience',2),('requiredEducationLevelCode','6'),('requiredSkills',['forklift'])])
def test_structured_conflicts_or_uncertainty_hold(field,value):
    s,_=automatic_review(RECORD,TEXT,{field:value},now=NOW)
    assert s in ('reject','review')

def test_preferred_language_is_not_required():
    s,_=automatic_review(RECORD,TEXT,{'positionLanguages':[{'languageCode':'en','desiredSkillLevel':'c2','requiredSkillLevel':None}]},now=NOW)
    assert s=='extracted'

@pytest.mark.parametrize('piece',['No licence required.','No experience required.','No qualifications required.','Visa sponsorship available for overseas applicants.','Basic English accepted.'])
def test_missing_domain_not_assumed_waived(piece):
    s,_=automatic_review(RECORD,TEXT.replace(piece,''),now=NOW)
    assert s=='review'

def test_unsupported_language_not_passed():
    s,_=automatic_review(RECORD,TEXT,{'languageVersion':'pl'},now=NOW)
    assert s=='review'

def test_additional_mandatory_condition_holds():
    s,_=automatic_review(RECORD,TEXT+' A welding certificate is mandatory.',now=NOW)
    assert s=='review'

def test_auto_store_checks_new_unreviewed_jobs_and_preserves_public_records():
    class S:
        data_dir='/tmp'
        _store={'x':dict(RECORD)}
    s=S(); calls=[]
    def fetch(r):calls.append(r['uid']);return TEXT,{}
    view=AutomaticEligibleStore(s,PROFILE,fetch,now=NOW)
    assert calls==['x'] and 'x' in view.load()
    assert s._store=={'x':RECORD}

def test_auto_store_fetch_fail_closed():
    class S:
        data_dir='/tmp';_store={'x':dict(RECORD)}
    def fail(r):raise ValueError('unavailable')
    v=AutomaticEligibleStore(S(),PROFILE,fail,now=NOW)
    assert v.load()['x']['private_fit_status']=='review' and v.stats['unavailable']==1


def test_known_conflict_not_downgraded_on_fetch_failure():
    class S:
        data_dir='/tmp';_store={'x':dict(RECORD, description='Gute Deutschkenntnisse. B-Führerschein erforderlich.')}
    def fail(r):raise ValueError('unavailable')
    v=AutomaticEligibleStore(S(),PROFILE,fail,now=NOW)
    assert not v.load() and v.stats['reject']==1

def test_uncertain_output_has_warning_and_not_full_translation():
    from src.notifier import format_job
    text=format_job(dict(RECORD,private_fit_status='review',private_fit_reason='غير متحقق منه: تصريح العمل'))
    assert 'محتاجة تحقق' in text and 'تصريح العمل' in text and 'ليس ترجمة كاملة' in text
    assert 'Warehouse worker' in text and RECORD['url'] in text

def test_preferred_licence_not_hard_rejected():
    status,_=automatic_review(RECORD,'Warehouse worker. Driving licence preferred. No experience required.',now=NOW)
    assert status=='review'


def _pool(score_map):
    class S:
        data_dir='/tmp'
        _store={uid: dict(RECORD, uid=uid, score=sc) for uid, sc in score_map.items()}
    return S()

def test_check_pool_prioritizes_digest_eligible_scores():
    # The bounded batch must spend detail checks only on jobs that can
    # actually be selected; below-threshold jobs made eligible candidates
    # invisible when rotation ran over the whole store.
    score_map={**{f'low{i}': 10 for i in range(25)}, **{f'hi{i}': 70 for i in range(5)}}
    calls=[]
    def fetch(r): calls.append(r['uid']); return TEXT,{}
    v=AutomaticEligibleStore(_pool(score_map),PROFILE,fetch,now=NOW,min_score=50)
    assert set(calls)=={f'hi{i}' for i in range(5)}
    assert set(v.load())==set(calls)

def test_check_pool_skips_uids_delivered_to_every_destination():
    calls=[]
    def fetch(r): calls.append(r['uid']); return TEXT,{}
    v=AutomaticEligibleStore(_pool({f'u{i}': 70 for i in range(4)}),PROFILE,fetch,now=NOW,min_score=50,skip_uids={'u1','u2'})
    assert set(calls)=={'u0','u3'}
