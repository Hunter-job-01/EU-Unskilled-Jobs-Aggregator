"""Hosted CPU translation smoke check; public fixtures only, no private inputs."""
import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.offline_translation import translate_full
samples=[('en','Warehouse worker. No driving licence required. No experience required. Visa sponsorship is not available.'),('de','Lagerarbeiter. Gute Deutschkenntnisse erforderlich. Führerschein Klasse B ist von Vorteil. Keine Berufserfahrung erforderlich.')]
t=time.time();results=[]
for language,text in samples:
    before=time.time();translated,status=translate_full(text,language)
    assert translated and any('\u0600' <= c <= '\u06ff' for c in translated)
    results.append({'language':language,'original':text,'arabic':translated,'status':status,'seconds':round(time.time()-before,1)})
print(json.dumps({'samples':results,'total_seconds':round(time.time()-t,1)},ensure_ascii=False,indent=2))
