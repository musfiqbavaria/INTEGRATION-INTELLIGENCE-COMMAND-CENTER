from django.conf import settings
from django.db import models
from django.utils import timezone

# Events a workflow can fire on. Anything else runs on its schedule only.
TRIGGER_EVENTS=[
    ("","Schedule only"),
    ("lead.created","Lead created"),
    ("lead.consented","Lead gave consent"),
    ("campaign.sent","Email campaign finished sending"),
    ("delivery.failed","Message delivery failed"),
    ("attention.overdue","Attention item passed its deadline"),
    ("lead.dormant","Lead has gone quiet"),
    ("lead.bounced","Lead was unsubscribed after repeated bounces"),
]

class Timestamped(models.Model):
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True
class Campaign(Timestamped):
    name=models.CharField(max_length=180); channel=models.CharField(max_length=80,default="Email"); status=models.CharField(max_length=30,default="draft"); audience_size=models.PositiveIntegerField(default=0); revenue=models.DecimalField(max_digits=14,decimal_places=2,default=0); cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    @property
    def roi(self): return ((self.revenue-self.cost)/self.cost*100) if self.cost else 0
class Lead(Timestamped):
    first_name=models.CharField(max_length=80); last_name=models.CharField(max_length=80); email=models.EmailField(unique=True); phone=models.CharField(max_length=40,blank=True); company=models.CharField(max_length=160,blank=True); market=models.CharField(max_length=80,default="Ireland"); source=models.CharField(max_length=80,blank=True); status=models.CharField(max_length=30,default="new"); score=models.PositiveSmallIntegerField(default=0); consent_at=models.DateTimeField(null=True,blank=True)
class AttentionItem(Timestamped):
    severity=models.CharField(max_length=20); category=models.CharField(max_length=100); title=models.CharField(max_length=220); impact=models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True); confidence=models.PositiveSmallIntegerField(default=0); source=models.CharField(max_length=120); recommendation=models.TextField(); status=models.CharField(max_length=30,default="pending"); due_at=models.DateTimeField(null=True,blank=True)
    overdue_notified_at=models.DateTimeField(null=True,blank=True)
class ContentItem(Timestamped):
    title=models.CharField(max_length=220); type=models.CharField(max_length=30); channel=models.CharField(max_length=80); status=models.CharField(max_length=30,default="draft"); body=models.TextField(); seo_score=models.PositiveSmallIntegerField(default=0); ai_confidence=models.PositiveSmallIntegerField(default=0); scheduled_at=models.DateTimeField(null=True,blank=True); published_at=models.DateTimeField(null=True,blank=True)
class EmailCampaign(Timestamped):
    name=models.CharField(max_length=180); subject=models.CharField(max_length=220); preview_text=models.CharField(max_length=260,blank=True); content=models.TextField(); segment=models.CharField(max_length=120); status=models.CharField(max_length=30,default="draft"); recipients=models.PositiveIntegerField(default=0); opens=models.PositiveIntegerField(default=0); clicks=models.PositiveIntegerField(default=0); failures=models.PositiveIntegerField(default=0); scheduled_at=models.DateTimeField(null=True,blank=True); sent_at=models.DateTimeField(null=True,blank=True)
class Automation(Timestamped):
    name=models.CharField(max_length=180); trigger=models.CharField(max_length=220); conditions=models.JSONField(default=list); actions=models.JSONField(default=list); status=models.CharField(max_length=30,default="active"); runs=models.PositiveIntegerField(default=0); successes=models.PositiveIntegerField(default=0); failures=models.PositiveIntegerField(default=0); last_run_at=models.DateTimeField(null=True,blank=True)
    run_every_minutes=models.PositiveIntegerField(default=60,help_text="Scheduler runs this workflow when it has not run for this many minutes.")
    trigger_event=models.CharField(max_length=40,blank=True,default="",choices=TRIGGER_EVENTS,
                                   help_text="Fire immediately when this happens, instead of waiting for the schedule.")
    @property
    def is_due(self):
        if self.status!="active": return False
        if not self.last_run_at: return True
        return self.last_run_at<=timezone.now()-timezone.timedelta(minutes=self.run_every_minutes or 60)
class Integration(Timestamped):
    name=models.CharField(max_length=160); provider=models.CharField(max_length=120); category=models.CharField(max_length=80); status=models.CharField(max_length=30,default="pending"); config=models.JSONField(default=dict,blank=True); last_sync_at=models.DateTimeField(null=True,blank=True); last_error=models.TextField(blank=True)
class FinancialRecord(Timestamped):
    recorded_on=models.DateField(default=timezone.localdate); market=models.CharField(max_length=80); system=models.CharField(max_length=120); campaign=models.CharField(max_length=180); channel=models.CharField(max_length=80); revenue=models.DecimalField(max_digits=14,decimal_places=2,default=0); cost=models.DecimalField(max_digits=14,decimal_places=2,default=0); leads=models.PositiveIntegerField(default=0); customers=models.PositiveIntegerField(default=0)
class AeoEntry(Timestamped):
    question=models.TextField(); answer=models.TextField(); topic=models.CharField(max_length=160); market=models.CharField(max_length=80,default="Ireland"); language=models.CharField(max_length=20,default="en-IE"); status=models.CharField(max_length=30,default="draft"); schema_type=models.CharField(max_length=80,default="FAQPage"); authority_score=models.PositiveSmallIntegerField(default=0); citations=models.JSONField(default=list)
    published_at=models.DateTimeField(null=True,blank=True)
class AiDecision(Timestamped):
    decision_id=models.CharField(max_length=50,unique=True); engine=models.CharField(max_length=160); title=models.CharField(max_length=220); recommendation=models.TextField(); evidence=models.JSONField(default=list); confidence=models.PositiveSmallIntegerField(); impact=models.CharField(max_length=20); risk_score=models.PositiveSmallIntegerField(); governance_level=models.CharField(max_length=30); status=models.CharField(max_length=30,default="pending"); expected_outcome=models.TextField(blank=True); decided_at=models.DateTimeField(null=True,blank=True); owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
class WhatsAppTemplate(Timestamped):
    name=models.CharField(max_length=160); category=models.CharField(max_length=50); language=models.CharField(max_length=20,default="en_IE"); body=models.TextField(); status=models.CharField(max_length=30,default="draft"); sent=models.PositiveIntegerField(default=0); delivered=models.PositiveIntegerField(default=0); read_count=models.PositiveIntegerField(default=0); replies=models.PositiveIntegerField(default=0)
class Setting(Timestamped):
    group=models.CharField(max_length=80); key=models.CharField(max_length=120); value=models.TextField(); type=models.CharField(max_length=30,default="text"); is_secret=models.BooleanField(default=False)
    class Meta: constraints=[models.UniqueConstraint(fields=["group","key"],name="unique_setting")]
class AuditLog(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL); event=models.CharField(max_length=160); path=models.CharField(max_length=300,blank=True); method=models.CharField(max_length=12,blank=True); ip_address=models.GenericIPAddressField(null=True,blank=True); old_values=models.JSONField(default=dict); new_values=models.JSONField(default=dict); created_at=models.DateTimeField(auto_now_add=True)
class SupportTicket(Timestamped):
    reference=models.CharField(max_length=40,unique=True); subject=models.CharField(max_length=220); category=models.CharField(max_length=80); priority=models.CharField(max_length=20,default="medium"); description=models.TextField(); status=models.CharField(max_length=30,default="open")
class ConsentEvent(models.Model):
    email=models.EmailField(); action=models.CharField(max_length=40); source=models.CharField(max_length=120); created_at=models.DateTimeField(auto_now_add=True)
class WorkflowRun(models.Model):
    automation=models.ForeignKey(Automation,on_delete=models.CASCADE,related_name="execution_logs"); status=models.CharField(max_length=30); trigger_payload=models.JSONField(default=dict); actions_completed=models.JSONField(default=list); error=models.TextField(blank=True); started_at=models.DateTimeField(auto_now_add=True); finished_at=models.DateTimeField(null=True,blank=True)
class IntegrationCheck(models.Model):
    integration=models.ForeignKey(Integration,null=True,blank=True,on_delete=models.SET_NULL); service=models.CharField(max_length=40); status=models.CharField(max_length=30); latency_ms=models.PositiveIntegerField(default=0); message=models.CharField(max_length=300); checked_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL); checked_at=models.DateTimeField(auto_now_add=True)
class MessageDelivery(models.Model):
    CHANNELS=[("email","Email"),("whatsapp","WhatsApp")]
    channel=models.CharField(max_length=20,choices=CHANNELS); recipient=models.CharField(max_length=180); template=models.CharField(max_length=180,blank=True); external_id=models.CharField(max_length=180,blank=True); status=models.CharField(max_length=30,default="queued"); error=models.TextField(blank=True); metadata=models.JSONField(default=dict); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
