from src.offline_translation import translation_document,translate_full

def test_full_document_keeps_every_original_character_and_labels_unknowns():
    text='Mandatory requirements. '+('long description '*1000)
    job={'title':'Warehouse worker','description':text,'url':'https://example.com/job','private_fit_status':'review','private_fit_reason':'اللغة غير متحققة'}
    def translate(t,lang): return 'ترجمة كاملة تجريبية', 'ترجمة آلية'
    result=translation_document([job],translate).decode()
    assert text in result and job['title'] in result and job['url'] in result
    assert 'محتاجة تحقق' in result and 'ترجمة كاملة تجريبية' in result

def test_translation_failure_does_not_drop_original():
    job={'title':'Worker','description':'Original full conditions','url':'https://example.com/job'}
    result=translation_document([job],lambda t,l:(None,'تعذرت الترجمة')).decode()
    assert 'Original full conditions' in result and 'الترجمة غير متاحة' in result

def test_arabic_original_does_not_need_model():
    assert translate_full('عامل مخزن',source='ar')[0]=='عامل مخزن'

def test_document_delivery_marks_only_after_acceptance(tmp_path,monkeypatch):
    from src import notifier
    class Store:
        data_dir=str(tmp_path)
        marked=[]
        def get_unnotified_top_jobs(self,**kw):return [{'uid':'x','title':'Worker','description':'Full text','url':'https://example.com/job'}]
        def mark_notified(self,uids): self.marked+=uids;return len(uids)
    class Response:
        status_code=200
        def json(self):return {'ok':True}
    class Session:
        calls=[]
        def post(self,url,**kw):self.calls.append((url,kw));return Response()
    monkeypatch.setattr('src.offline_translation.translation_document',lambda jobs:b'full translated and original text')
    store=Store();session=Session();state={}
    cfg={'telegram':{'enabled':True,'full_arabic_translation':True},'storage':{}}
    assert notifier._send_single_digest(store,cfg,environ={'TELEGRAM_BOT_TOKEN':'offline','TELEGRAM_CHAT_ID':'offline'},session=session,destination_state=state,save_destination=lambda s:None)==1
    assert store.marked==['x'] and len(session.calls)==1
    assert session.calls[0][0].endswith('/sendDocument')
    assert 'last_digest_utc' in state
