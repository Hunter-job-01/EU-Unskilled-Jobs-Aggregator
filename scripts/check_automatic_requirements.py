"""Bounded live source/triage check, no notification transport or public writes."""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.config import load_config
from src.storage import JobStore
from src.eligibility import AutomaticEligibleStore,load_private
from src.source_evidence import fetch_job_details
p,_=load_private(os.environ)
s=JobStore(load_config()['storage'])
v=AutomaticEligibleStore(s,p,fetch_job_details,max_checks=8)
print('Live automatic triage aggregate only:',v.stats)
print('No Telegram send function called. No candidate-specific public records written.')
