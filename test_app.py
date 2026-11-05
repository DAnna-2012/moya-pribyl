import os
import unittest
import uuid
from unittest.mock import patch
from main import app

class FormTests(unittest.TestCase):
    def setUp(self):
        self.client=app.test_client()
        self.env=patch.dict(os.environ,{'LEADS_ENABLED':'true'});self.env.start()
        self.payload={'id':str(uuid.uuid4()),'name':'Тест','contact':'test@example.invalid','source':'test','consent':True,'consent_version':'2026-11-05-v1'}
    def tearDown(self): self.env.stop()
    def test_closed_by_switch(self):
        with patch.dict(os.environ,{'LEADS_ENABLED':'false'}),patch('main.store_lead') as store:
            self.assertEqual(self.client.post('/api/leads',json=self.payload).status_code,503);store.assert_not_called()
    def test_empty_contact_and_consent(self):
        for change in ({'contact':''},{'consent':False},{'consent_version':'old'},{'name':'x'*101},{'website':'spam'}):
            with patch('main.store_lead') as store:
                self.assertEqual(self.client.post('/api/leads',json=self.payload|change).status_code,400);store.assert_not_called()
    def test_database_failure_is_not_success(self):
        with patch('main.store_lead',side_effect=RuntimeError('private details')):
            response=self.client.post('/api/leads',json=self.payload)
            self.assertEqual(response.status_code,503);self.assertNotIn('private details',response.text);self.assertNotIn('ok',response.json)
    def test_success_after_storage(self):
        with patch('main.store_lead') as store:
            response=self.client.post('/api/leads',json=self.payload)
            self.assertEqual(response.status_code,201);store.assert_called_once();self.assertEqual(response.json['id'],self.payload['id'])
    def test_cross_origin_rejected(self):
        with patch('main.store_lead') as store:
            self.assertEqual(self.client.post('/api/leads',json=self.payload,headers={'Origin':'https://evil.example'}).status_code,403);store.assert_not_called()
    def test_no_public_export_or_source(self):
        self.assertEqual(self.client.get('/admin/leads.csv').status_code,401)
        self.assertEqual(self.client.get('/main.py').status_code,404)
    def test_documents_and_frontend(self):
        for path in ['/','/privacy.html','/consent.html','/health']:
            self.assertEqual(self.client.get(path).status_code,200)
        page=self.client.get('/').text
        self.assertNotIn('script.google.com',page)
        self.assertIn("if(!response.ok||result.ok!==true)",page)
        self.assertLess(page.index('if(!response.ok'),page.index("'lead_submit'"))

if __name__=='__main__':unittest.main()
