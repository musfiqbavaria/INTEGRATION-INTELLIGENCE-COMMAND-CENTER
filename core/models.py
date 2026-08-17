from django.conf import settings
from django.db import models
from django.utils import timezone

# Events a workflow can fire on. Anything else runs on its schedule only.
TRIGGER_EVENTS=[
    ("","Schedule only"),
    ("lead.created","Lead created"),
    ("lead.consented","Lead gave consent"),
    ("lead.engaged","Lead opened or clicked a campaign"),
    ("campaign.sent","Email campaign finished sending"),
    ("campaign.underperforming","Campaign engagement below target"),
    ("conversion.recorded","Lead converted with revenue"),
    ("delivery.failed","Message delivery failed"),
    ("attention.overdue","Attention item passed its deadline"),
    ("attention.escalated","Attention item breached its deadline again"),
    ("lead.dormant","Lead has gone quiet"),
    ("lead.bounced","Lead was unsubscribed after repeated bounces"),
]

class Timestamped(models.Model):
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True

class Organisation(Timestamped):
    """A trading entity: one company, in one country, reporting in one currency.

    The console runs single-entity by default. The data migration and
    `seed_system` create one organisation and attach every existing record to
    it, so nothing changes visibly until a second entity is added.
    """
    name=models.CharField(max_length=180); code=models.CharField(max_length=12,unique=True)
    country=models.CharField(max_length=2,default="IE"); base_currency=models.CharField(max_length=3,default="EUR")
    locale=models.CharField(max_length=12,default="en-IE"); timezone=models.CharField(max_length=64,default="Europe/Dublin")
    is_active=models.BooleanField(default=True)
    class Meta: ordering=["name"]
    def __str__(self): return self.name

class OrgOwned(models.Model):
    """Tenant key. Nullable so the column could be added without downtime; the
    data migration backfills it and `current_organisation` sets it on writes."""
    organisation=models.ForeignKey(Organisation,null=True,blank=True,on_delete=models.PROTECT,related_name="+")
    class Meta: abstract=True

class FxRate(models.Model):
    """One day's rate converting `currency` into `base`. Financial reporting
    reads the newest rate on or before the record's own date, so historical
    figures do not silently move when today's rate changes."""
    base=models.CharField(max_length=3,default="EUR"); currency=models.CharField(max_length=3)
    rate=models.DecimalField(max_digits=18,decimal_places=8); as_of=models.DateField(default=timezone.localdate)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["base","currency","as_of"],name="unique_fx_rate")]
        ordering=["-as_of"]
    def __str__(self): return f"{self.currency}->{self.base} @ {self.as_of}"

class Campaign(Timestamped,OrgOwned):
    name=models.CharField(max_length=180); channel=models.CharField(max_length=80,default="Email"); status=models.CharField(max_length=30,default="draft"); audience_size=models.PositiveIntegerField(default=0); revenue=models.DecimalField(max_digits=14,decimal_places=2,default=0); cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    @property
    def roi(self): return ((self.revenue-self.cost)/self.cost*100) if self.cost else 0
class Lead(Timestamped,OrgOwned):
    first_name=models.CharField(max_length=80); last_name=models.CharField(max_length=80); email=models.EmailField(unique=True); phone=models.CharField(max_length=40,blank=True); company=models.CharField(max_length=160,blank=True); market=models.CharField(max_length=80,default="Ireland"); source=models.CharField(max_length=80,blank=True); status=models.CharField(max_length=30,default="new"); score=models.PositiveSmallIntegerField(default=0); consent_at=models.DateTimeField(null=True,blank=True)
    class Meta: indexes=[models.Index(fields=["status"]),models.Index(fields=["consent_at"]),models.Index(fields=["updated_at"])]
class AttentionItem(Timestamped,OrgOwned):
    severity=models.CharField(max_length=20); category=models.CharField(max_length=100); title=models.CharField(max_length=220); impact=models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True); confidence=models.PositiveSmallIntegerField(default=0); source=models.CharField(max_length=120); recommendation=models.TextField(); status=models.CharField(max_length=30,default="pending"); due_at=models.DateTimeField(null=True,blank=True)
    overdue_notified_at=models.DateTimeField(null=True,blank=True)
    # Escalation ladder. `sla_hours` sets the deadline when none is given;
    # `escalation_level` counts how many times the deadline has been re-breached.
    sla_hours=models.PositiveSmallIntegerField(default=0)
    escalation_level=models.PositiveSmallIntegerField(default=0)
    escalated_at=models.DateTimeField(null=True,blank=True)
    alerted_at=models.DateTimeField(null=True,blank=True)
    class Meta: indexes=[models.Index(fields=["status","severity"]),models.Index(fields=["due_at"]),models.Index(fields=["category"])]
class ContentItem(Timestamped,OrgOwned):
    title=models.CharField(max_length=220); type=models.CharField(max_length=30); channel=models.CharField(max_length=80); status=models.CharField(max_length=30,default="draft"); body=models.TextField(); seo_score=models.PositiveSmallIntegerField(default=0); ai_confidence=models.PositiveSmallIntegerField(default=0); scheduled_at=models.DateTimeField(null=True,blank=True); published_at=models.DateTimeField(null=True,blank=True)
class EmailCampaign(Timestamped,OrgOwned):
    name=models.CharField(max_length=180); subject=models.CharField(max_length=220); preview_text=models.CharField(max_length=260,blank=True); content=models.TextField(); segment=models.CharField(max_length=120); status=models.CharField(max_length=30,default="draft"); recipients=models.PositiveIntegerField(default=0); opens=models.PositiveIntegerField(default=0); clicks=models.PositiveIntegerField(default=0); failures=models.PositiveIntegerField(default=0); scheduled_at=models.DateTimeField(null=True,blank=True); sent_at=models.DateTimeField(null=True,blank=True)
    # Engagement review: raised once, after the campaign has had time to land.
    unsubscribes=models.PositiveIntegerField(default=0)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    class Meta: indexes=[models.Index(fields=["status","scheduled_at"]),models.Index(fields=["sent_at"])]
    @property
    def open_rate(self): return round(self.opens/self.recipients*100,1) if self.recipients else 0
    @property
    def click_rate(self): return round(self.clicks/self.recipients*100,1) if self.recipients else 0
class Automation(Timestamped,OrgOwned):
    name=models.CharField(max_length=180); trigger=models.CharField(max_length=220); conditions=models.JSONField(default=list); actions=models.JSONField(default=list); status=models.CharField(max_length=30,default="active"); runs=models.PositiveIntegerField(default=0); successes=models.PositiveIntegerField(default=0); failures=models.PositiveIntegerField(default=0); last_run_at=models.DateTimeField(null=True,blank=True)
    run_every_minutes=models.PositiveIntegerField(default=60,help_text="Scheduler runs this workflow when it has not run for this many minutes.")
    trigger_event=models.CharField(max_length=40,blank=True,default="",choices=TRIGGER_EVENTS,
                                   help_text="Fire immediately when this happens, instead of waiting for the schedule.")
    @property
    def is_due(self):
        if self.status!="active": return False
        if not self.last_run_at: return True
        return self.last_run_at<=timezone.now()-timezone.timedelta(minutes=self.run_every_minutes or 60)
class Integration(Timestamped,OrgOwned):
    name=models.CharField(max_length=160); provider=models.CharField(max_length=120); category=models.CharField(max_length=80); status=models.CharField(max_length=30,default="pending"); config=models.JSONField(default=dict,blank=True); last_sync_at=models.DateTimeField(null=True,blank=True); last_error=models.TextField(blank=True)
class FinancialRecord(Timestamped,OrgOwned):
    recorded_on=models.DateField(default=timezone.localdate); market=models.CharField(max_length=80); system=models.CharField(max_length=120); campaign=models.CharField(max_length=180); channel=models.CharField(max_length=80); revenue=models.DecimalField(max_digits=14,decimal_places=2,default=0); cost=models.DecimalField(max_digits=14,decimal_places=2,default=0); leads=models.PositiveIntegerField(default=0); customers=models.PositiveIntegerField(default=0)
    # Set by the attribution roll-up so a generated row can be recomputed
    # without disturbing figures an operator entered by hand.
    campaign_link=models.ForeignKey(Campaign,null=True,blank=True,on_delete=models.SET_NULL,related_name="financials")
    email_campaign=models.ForeignKey(EmailCampaign,null=True,blank=True,on_delete=models.SET_NULL,related_name="financials")
    generated=models.BooleanField(default=False)
    currency=models.CharField(max_length=3,default="EUR")
    class Meta: indexes=[models.Index(fields=["recorded_on"]),models.Index(fields=["channel"]),models.Index(fields=["market"])]
class Conversion(Timestamped,OrgOwned):
    """Money earned from a lead, and the campaign credited with earning it.

    This is the attribution primitive the console never had: `FinancialRecord`
    held totals nobody could trace back to a campaign, which is exactly why the
    Executive Dashboard printed reconciliation warnings.
    """
    ATTRIBUTIONS=[("unattributed","Unattributed"),("last-touch","Last touch"),("manual","Manual")]
    lead=models.ForeignKey(Lead,null=True,blank=True,on_delete=models.SET_NULL,related_name="conversions")
    email=models.EmailField(blank=True)
    amount=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    currency=models.CharField(max_length=3,default="EUR")
    # Null, not zero, when no FX rate is on file: reporting must be able to
    # tell "this earned nothing" apart from "nobody has loaded a rate yet".
    base_amount=models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True,help_text="`amount` converted into the organisation's base currency.")
    market=models.CharField(max_length=80,default="Ireland"); channel=models.CharField(max_length=80,blank=True)
    campaign=models.ForeignKey(Campaign,null=True,blank=True,on_delete=models.SET_NULL,related_name="conversions")
    email_campaign=models.ForeignKey(EmailCampaign,null=True,blank=True,on_delete=models.SET_NULL,related_name="conversions")
    attribution=models.CharField(max_length=30,default="unattributed",choices=ATTRIBUTIONS)
    reference=models.CharField(max_length=80,blank=True)
    occurred_at=models.DateTimeField(default=timezone.now)
    rolled_up_at=models.DateTimeField(null=True,blank=True)
    class Meta: indexes=[models.Index(fields=["occurred_at"]),models.Index(fields=["rolled_up_at"])]
class AeoEntry(Timestamped,OrgOwned):
    question=models.TextField(); answer=models.TextField(); topic=models.CharField(max_length=160); market=models.CharField(max_length=80,default="Ireland"); language=models.CharField(max_length=20,default="en-IE"); status=models.CharField(max_length=30,default="draft"); schema_type=models.CharField(max_length=80,default="FAQPage"); authority_score=models.PositiveSmallIntegerField(default=0); citations=models.JSONField(default=list)
    published_at=models.DateTimeField(null=True,blank=True)
class AiDecision(Timestamped,OrgOwned):
    decision_id=models.CharField(max_length=50,unique=True); engine=models.CharField(max_length=160); title=models.CharField(max_length=220); recommendation=models.TextField(); evidence=models.JSONField(default=list); confidence=models.PositiveSmallIntegerField(); impact=models.CharField(max_length=20); risk_score=models.PositiveSmallIntegerField(); governance_level=models.CharField(max_length=30); status=models.CharField(max_length=30,default="pending"); expected_outcome=models.TextField(blank=True); decided_at=models.DateTimeField(null=True,blank=True); owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
class WhatsAppTemplate(Timestamped,OrgOwned):
    name=models.CharField(max_length=160); category=models.CharField(max_length=50); language=models.CharField(max_length=20,default="en_IE"); body=models.TextField(); status=models.CharField(max_length=30,default="draft"); sent=models.PositiveIntegerField(default=0); delivered=models.PositiveIntegerField(default=0); read_count=models.PositiveIntegerField(default=0); replies=models.PositiveIntegerField(default=0)
class Setting(Timestamped,OrgOwned):
    group=models.CharField(max_length=80); key=models.CharField(max_length=120); value=models.TextField(); type=models.CharField(max_length=30,default="text"); is_secret=models.BooleanField(default=False)
    class Meta: constraints=[models.UniqueConstraint(fields=["organisation","group","key"],name="unique_setting_per_org")]
class AuditLog(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL); event=models.CharField(max_length=160); path=models.CharField(max_length=300,blank=True); method=models.CharField(max_length=12,blank=True); ip_address=models.GenericIPAddressField(null=True,blank=True); old_values=models.JSONField(default=dict); new_values=models.JSONField(default=dict); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=["created_at"]),models.Index(fields=["event"])]
class SupportTicket(Timestamped,OrgOwned):
    reference=models.CharField(max_length=40,unique=True); subject=models.CharField(max_length=220); category=models.CharField(max_length=80); priority=models.CharField(max_length=20,default="medium"); description=models.TextField(); status=models.CharField(max_length=30,default="open")
class ConsentEvent(models.Model):
    email=models.EmailField(); action=models.CharField(max_length=40); source=models.CharField(max_length=120); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=["email"]),models.Index(fields=["created_at"])]
class WorkflowRun(models.Model):
    automation=models.ForeignKey(Automation,on_delete=models.CASCADE,related_name="execution_logs"); status=models.CharField(max_length=30); trigger_payload=models.JSONField(default=dict); actions_completed=models.JSONField(default=list); error=models.TextField(blank=True); started_at=models.DateTimeField(auto_now_add=True); finished_at=models.DateTimeField(null=True,blank=True)
    class Meta: indexes=[models.Index(fields=["started_at"]),models.Index(fields=["status"])]
class IntegrationCheck(models.Model):
    integration=models.ForeignKey(Integration,null=True,blank=True,on_delete=models.SET_NULL); service=models.CharField(max_length=40); status=models.CharField(max_length=30); latency_ms=models.PositiveIntegerField(default=0); message=models.CharField(max_length=300); checked_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL); checked_at=models.DateTimeField(auto_now_add=True)
class MessageDelivery(models.Model):
    CHANNELS=[("email","Email"),("whatsapp","WhatsApp")]
    channel=models.CharField(max_length=20,choices=CHANNELS); recipient=models.CharField(max_length=180); template=models.CharField(max_length=180,blank=True); external_id=models.CharField(max_length=180,blank=True); status=models.CharField(max_length=30,default="queued"); error=models.TextField(blank=True); metadata=models.JSONField(default=dict); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    # Engagement. `token` identifies this delivery in the tracking pixel, the
    # click redirect and the one-click unsubscribe URL, so none of those three
    # need the recipient's address in the link.
    token=models.CharField(max_length=64,blank=True,default="",db_index=True)
    opened_at=models.DateTimeField(null=True,blank=True); clicked_at=models.DateTimeField(null=True,blank=True)
    open_count=models.PositiveIntegerField(default=0); click_count=models.PositiveIntegerField(default=0)
    class Meta: indexes=[models.Index(fields=["channel","status"]),models.Index(fields=["recipient"]),models.Index(fields=["created_at"])]
class EngagementEvent(models.Model):
    """One recorded open, click or unsubscribe. Kept alongside the counters on
    MessageDelivery so a rate can always be re-derived from the raw events."""
    KINDS=[("open","Open"),("click","Click"),("unsubscribe","Unsubscribe")]
    delivery=models.ForeignKey(MessageDelivery,on_delete=models.CASCADE,related_name="engagement")
    kind=models.CharField(max_length=20,choices=KINDS)
    url=models.CharField(max_length=600,blank=True)
    user_agent=models.CharField(max_length=300,blank=True)
    ip_address=models.GenericIPAddressField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=["kind","created_at"])]
