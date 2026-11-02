import json
from src.gemini_translation import Translator,public_text,segments,validate

def test_redacts_contacts_not_numbers():
    s=public_text('Salary 2,400 euros,40 hours\nEmail person@example.com\nDeine Recruiterin Christina\nhttps://example.com\n+201234567890')
    assert '2,400' in s and 'Christina' not in s and '@' not in s and 'https:' not in s and '+20' not in s

def test_completeness_numbers_and_scripts():
    src=segments('Salary 2.400\nNo licence required')
    good={'translations':[{'id':0,'arabic':'الراتب 2,400'},{'id':1,'arabic':'لا تلزم رخصة'}]}
    assert validate(src,good)
    for bad in [ {'translations':good['translations'][:1]}, {'translations':[{'id':0,'arabic':'الراتب 240'},{'id':1,'arabic':'لا רخصة'}]} ]:
        try:validate(src,bad);assert False
        except ValueError:pass

def test_no_key_no_network(tmp_path):
    assert Translator(key='',cache_dir=tmp_path)('Worker')[0] is None

def test_quota_stops_no_retry(tmp_path):
    class Session:
        count=0
        def post(self,*a,**k):self.count+=1;return type('R',(),{'status_code':429})()
    session=Session();t=Translator(key='dummy',cache_dir=tmp_path,session=session,sleep=lambda n:None)
    assert t('Worker')[0] is None and t('Another worker')[0] is None and session.count==1

def test_public_only_request_and_cache(tmp_path):
    class R:
        status_code=200
        def json(self):return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'translations':[{'id':0,'arabic':'عامل مخزن'}]})}]}}]}
    class Session:
        count=0
        def post(self,*a,**kw):self.count+=1;self.body=kw['json'];return R()
    s=Session();t=Translator(key='dummy',cache_dir=tmp_path,session=s)
    assert t('Warehouse worker')[0]=='عامل مخزن';assert t('Warehouse worker')[0]=='عامل مخزن';assert s.count==1
    assert 'profile' not in json.dumps(s.body).lower()

def test_english_target_success_and_fail_closed(tmp_path):
    class R:
        def __init__(self, text): self.status_code=200; self._t=text
        def json(self): return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':self._t}]}}]}
    class Session:
        def __init__(self, text): self.count=0; self._t=text
        def post(self,*a,**kw): self.count+=1; return R(self._t)
    good=json.dumps({'translations':[{'id':0,'english':'Warehouse worker 40 hours'}]})
    s=Session(good); t=Translator(key='dummy',cache_dir=tmp_path,session=s,sleep=lambda n:None)
    out,st=t('Magazynier 40 godzin',target='en')
    assert out=='Warehouse worker 40 hours'
    # numeric fidelity fail-closed
    bad=json.dumps({'translations':[{'id':0,'english':'Warehouse worker 45 hours'}]})
    t2=Translator(key='dummy',cache_dir=tmp_path/'b',session=Session(bad),sleep=lambda n:None)
    assert t2('Magazynier 40 godzin',target='en')[0] is None
    # non-Latin output rejected for en
    bad2=json.dumps({'translations':[{'id':0,'english':'عامل مخزن'}]})
    t3=Translator(key='dummy',cache_dir=tmp_path/'c',session=Session(bad2),sleep=lambda n:None)
    assert t3('Magazynier',target='en')[0] is None

def test_shared_cap_across_language_batches(tmp_path):
    class R:
        status_code=200
        def json(self): return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'translations':[{'id':0,'arabic':'عامل'},{'id':1,'arabic':'سائق'}]})}]}}]}
    class RE:
        status_code=200
        def json(self): return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'translations':[{'id':0,'english':'Worker'}]})}]}}]}
    class Session:
        def __init__(self): self.count=0
        def post(self,*a,**kw):
            self.count+=1
            return RE() if b'english' in json.dumps(kw['json']).encode() else R()
    s=Session(); t=Translator(key='dummy',cache_dir=tmp_path,session=s,max_calls=5,sleep=lambda n:None)
    ar,_=t('Pracownik\nKierowca')
    en,_=t('Magazynier',target='en')
    assert ar and en and s.count==2 and t.remaining==3  # one shared cap

def test_en_and_ar_cache_entries_do_not_collide(tmp_path):
    class R:
        def __init__(self,payload): self.status_code=200; self._p=payload
        def json(self): return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':self._p}]}}]}
    class Session:
        def post(self,*a,**kw):
            body=json.dumps(kw['json'])
            if 'english' in body:
                return R(json.dumps({'translations':[{'id':0,'english':'Warehouse worker'}]}))
            return R(json.dumps({'translations':[{'id':0,'arabic':'عامل مخزن'}]}))
    t=Translator(key='dummy',cache_dir=tmp_path,session=Session(),sleep=lambda n:None)
    ar,_=t('Magazynier')
    en,_=t('Magazynier',target='en')
    assert ar=='عامل مخزن' and en=='Warehouse worker'  # distinct cache keys
