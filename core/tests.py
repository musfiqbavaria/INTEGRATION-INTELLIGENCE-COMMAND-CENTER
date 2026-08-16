from django.contrib.auth.models import User
from django.test import TestCase
from .models import Lead, Automation
class ApplicationTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user("owner@example.ie",password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie",password="Strong-Test-Password-123")
    def test_core_pages(self):
        for path in ["/","/leads/","/campaigns/","/email-marketing/","/whatsapp/","/automations/","/integrations/","/aeo/","/analytics/","/integration-health/","/ai-orchestrator/","/api/dashboard/","/up"]: self.assertEqual(self.client.get(path).status_code,200)
    def test_email_metrics_not_user_editable(self):
        response=self.client.get("/email-marketing/")
        self.assertNotContains(response,'name="opens"')
        self.assertNotContains(response,'name="failures"')
    def test_operational_records_can_be_created(self):
        self.client.post("/automations/",{"action":"create","name":"Test flow","trigger":"Lead created","conditions":"consent confirmed","actions":"Send email"})
        self.assertEqual(Automation.objects.filter(name="Test flow").count(),1)
    def test_unsubscribe(self):
        Lead.objects.create(first_name="Test",last_name="Lead",email="lead@example.ie")
        response=self.client.post("/api/consent/unsubscribe",data='{"email":"lead@example.ie"}',content_type="application/json")
        self.assertEqual(response.status_code,200)
        self.assertEqual(Lead.objects.get(email="lead@example.ie").status,"unsubscribed")
