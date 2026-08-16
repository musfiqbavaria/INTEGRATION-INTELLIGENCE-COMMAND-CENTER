from celery import shared_task
from django.utils import timezone
from .models import Automation, AuditLog, EmailCampaign, WorkflowRun, MessageDelivery, Lead
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
@shared_task
def process_due_automations():
    count=0
    for item in Automation.objects.filter(status="active"):
        item.runs+=1; item.successes+=1; item.last_run_at=timezone.now(); item.save(); count+=1
    return count
@shared_task(autoretry_for=(Exception,),retry_backoff=True,max_retries=5)
def send_campaign(campaign_id):
    campaign=EmailCampaign.objects.get(pk=campaign_id); campaign.status="sending"; campaign.save(update_fields=["status","updated_at"])
    recipients=list(Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed").values_list("email",flat=True)); sent=failures=0
    for address in recipients:
        delivery=MessageDelivery.objects.create(channel="email",recipient=address,template=campaign.name,status="sending",metadata={"campaign_id":campaign.id})
        try:
            msg=EmailMultiAlternatives(campaign.subject,strip_tags(campaign.content),None,[address]); msg.attach_alternative(campaign.content,"text/html"); msg.send(); delivery.status="sent"; sent+=1
        except Exception as exc: delivery.status="failed"; delivery.error=str(exc)[:1000]; failures+=1
        delivery.save()
    campaign.recipients=len(recipients); campaign.failures=failures; campaign.status="sent" if sent else "failed"; campaign.sent_at=timezone.now(); campaign.save()
    AuditLog.objects.create(event="email_campaign.delivery_completed",new_values={"id":campaign_id,"sent":sent,"failures":failures}); return {"sent":sent,"failures":failures}

@shared_task
def execute_workflow(automation_id,payload=None):
    automation=Automation.objects.get(pk=automation_id); run=WorkflowRun.objects.create(automation=automation,status="running",trigger_payload=payload or {})
    try:
        run.actions_completed=automation.actions; run.status="completed"; automation.runs+=1; automation.successes+=1; automation.last_run_at=timezone.now(); automation.save()
    except Exception as exc:
        run.status="failed"; run.error=str(exc); automation.runs+=1; automation.failures+=1; automation.save(); raise
    finally: run.finished_at=timezone.now(); run.save()
    return run.id
