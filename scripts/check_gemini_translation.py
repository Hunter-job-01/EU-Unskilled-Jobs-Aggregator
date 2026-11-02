"""Hosted public-text-only check. Never loads private profile or sends Telegram."""
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.gemini_translation import Translator
from src.offline_translation import translation_document
sample='Warehouse worker. No driving licence required. Forklift certificate preferred, not required. No previous experience required. Visa sponsorship is NOT available. Salary 2,400 euros gross monthly, 40 hours weekly. German B2 required; English A2 preferred.'
with tempfile.TemporaryDirectory() as cache:
    t=Translator(cache_dir=cache,max_calls=1)
    ar,status=t(sample)
    assert ar, 'Free API translation check unavailable or refused'
    assert 'B2' in ar and 'A2' in ar and any(w in ar for w in ['غير','لا','ليست'])
    doc=translation_document([{'title':'Warehouse worker','description':sample,'url':'https://example.com/public-job'}],translate=t)
    assert sample.encode() in doc
    print(json.dumps({'model':'gemini-3.5-flash-lite','arabic':ar,'status':status,'original_preserved':True,'telegram_sent':False},ensure_ascii=False))
