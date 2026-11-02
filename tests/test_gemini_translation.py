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
