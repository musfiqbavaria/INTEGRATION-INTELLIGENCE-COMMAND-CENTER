import json, secrets
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import *
from .services import test_integration, run_ai_engine, send_whatsapp_template
from .tasks import send_campaign, execute_workflow

MODELS={"leads":Lead,"campaigns":Campaign,"content":ContentItem,"email-marketing":EmailCampaign,"automations":Automation,"integrations":Integration,"finance":FinancialRecord,"aeo":AeoEntry,"intelligence":AiDecision,"whatsapp":WhatsAppTemplate,"settings":Setting,"audit":AuditLog,"support":SupportTicket}
DISPLAY_SKIP={"id","password","config","old_values","new_values"}

def _cell(field,value):
    """One table cell: a display string plus a kind that drives alignment and badges."""
    if value is None or value=="": return {"value":"—","kind":"muted"}
    if field.name in {"status","severity","priority"}: return {"value":str(value),"kind":"status"}
    if isinstance(field,models.DateTimeField):
        return {"value":(timezone.localtime(value) if timezone.is_aware(value) else value).strftime("%d %b %Y · %H:%M"),"kind":"when"}
    if isinstance(field,models.DateField): return {"value":value.strftime("%d %b %Y"),"kind":"when"}
    if isinstance(field,models.BooleanField): return {"value":"Yes" if value else "No","kind":"text"}
    if isinstance(field,models.JSONField):
        if isinstance(value,dict): return {"value":", ".join(f"{k}: {v}" for k,v in value.items()) or "—","kind":"text"}
        if isinstance(value,(list,tuple)): return {"value":", ".join(str(v) for v in value) or "—","kind":"text"}
        return {"value":str(value),"kind":"text"}
    if isinstance(field,models.DecimalField): return {"value":f"{value:,.2f}","kind":"num"}
    if isinstance(field,(models.IntegerField,models.FloatField)): return {"value":f"{value:,}","kind":"num"}
    text=str(value)
    return {"value":text[:180]+("…" if len(text)>180 else ""),"kind":"text"}
TITLES={"leads":"Leads & Customers","campaigns":"Campaigns","content":"Content Center","email-marketing":"Email Marketing","automations":"Automation & Workflows","integrations":"Integration Center","finance":"Finance & Profitability","aeo":"AI Optimized Marketing (AEO)","intelligence":"AI Intelligence Suite","whatsapp":"WhatsApp Center","settings":"Settings & Preferences","audit":"Audit Logs","support":"Help Desk & Support"}

def login_view(request):
    if request.method=="POST":
        user=authenticate(request,username=request.POST.get("email"),password=request.POST.get("password"))
        if user: login(request,user); return redirect("dashboard")
        messages.error(request,"Invalid email or password")
    return render(request,"login.html")
@require_POST
def logout_view(request): logout(request); return redirect("login")

@login_required
def dashboard(request):
    totals=FinancialRecord.objects.aggregate(revenue=Sum("revenue"),cost=Sum("cost")); revenue=totals["revenue"] or 0; cost=totals["cost"] or 0
    return render(request,"dashboard.html",{"attention":AttentionItem.objects.order_by("-created_at")[:8],"campaigns":Campaign.objects.order_by("-created_at")[:5],"leads":Lead.objects.count(),"revenue":revenue,"profit":revenue-cost,"decisions":AiDecision.objects.filter(status="pending").count()})

@login_required
def module(request,slug):
    readonly=slug=="analytics"
    if readonly: model=FinancialRecord
    elif slug in {"users","help"}: model=User if slug=="users" else SupportTicket
    else: model=MODELS.get(slug)
    if not model: return HttpResponse("Not found",status=404)
    if request.method=="POST":
        action=request.POST.get("action","create")
        if action=="delete": model.objects.filter(pk=request.POST.get("id")).delete()
        elif action in {"approve","reject","pause","activate","publish"}:
            obj=get_object_or_404(model,pk=request.POST.get("id")); obj.status={"activate":"active","publish":"published"}.get(action,action+"d" if action in {"approve","reject"} else action); 
            if hasattr(obj,"decided_at"): obj.decided_at=timezone.now()
            obj.save()
        else:
            data={k:v for k,v in request.POST.items() if k not in {"csrfmiddlewaretoken","action"} and any(f.name==k for f in model._meta.fields)}
            for f in model._meta.fields:
                if f.name in data and isinstance(f,(models.IntegerField,models.DecimalField)): data[f.name]=data[f.name] or 0
                if f.name in data and isinstance(f,models.JSONField): data[f.name]=[x.strip() for x in data[f.name].split(",") if x.strip()]
            try: model.objects.create(**data); messages.success(request,"Record created")
            except Exception as exc: messages.error(request,f"Could not create record: {exc}")
        return redirect("module",slug=slug)
    items=model.objects.order_by("-pk")[:100]
    display_fields=[f for f in model._meta.fields if f.name not in DISPLAY_SKIP]
    # Record-keeping timestamps move to the end so identifying columns lead the table.
    display_fields.sort(key=lambda f: f.name in {"created_at","updated_at","date_joined","last_login"})
    rows=[{"pk":obj.pk,"cells":[_cell(f,getattr(obj,f.name,None)) for f in display_fields]} for obj in items]
    fields=[] if readonly else [f for f in model._meta.fields if f.editable and not f.auto_created and f.name not in {"id","created_at","updated_at","owner","user"}]
    total=model.objects.count()
    context={"title":TITLES.get(slug,slug.title()),"slug":slug,"rows":rows,"columns":[f.verbose_name for f in display_fields],
             "fields":fields,"readonly":readonly,"total_count":total,"shown_count":len(rows)}
    if readonly:
        agg=FinancialRecord.objects.aggregate(revenue=Sum("revenue"),cost=Sum("cost"),leads=Sum("leads"),customers=Sum("customers"))
        revenue=agg["revenue"] or 0; cost=agg["cost"] or 0
        context.update({"title":"Analytics & Reports","totals":{"revenue":revenue,"cost":cost,"profit":revenue-cost,
                        "leads":agg["leads"] or 0,"customers":agg["customers"] or 0,
                        "margin":round((revenue-cost)/revenue*100,1) if revenue else 0}})
    return render(request,"module.html",context)

@login_required
def email_center(request):
    if request.method=="POST":
        action=request.POST.get("action")
        if action=="create":
            EmailCampaign.objects.create(name=request.POST["name"],subject=request.POST["subject"],preview_text=request.POST.get("preview_text",""),content=request.POST["content"],segment=request.POST.get("segment","Consented leads"),status="draft")
            messages.success(request,"Email campaign saved as draft")
        elif action=="send":
            campaign=get_object_or_404(EmailCampaign,pk=request.POST.get("id"))
            if not Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed").exists(): messages.error(request,"No consented recipients are available")
            else: send_campaign.delay(campaign.id); messages.success(request,"Campaign queued for authenticated SMTP delivery")
        elif action=="delete": EmailCampaign.objects.filter(pk=request.POST.get("id"),status="draft").delete()
        return redirect("email-center")
    return render(request,"email_center.html",{"campaigns":EmailCampaign.objects.order_by("-created_at"),"deliveries":MessageDelivery.objects.filter(channel="email").order_by("-created_at")[:25],"consented":Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed").count()})

@login_required
def whatsapp_center(request):
    if request.method=="POST":
        action=request.POST.get("action")
        if action=="create":
            WhatsAppTemplate.objects.create(name=request.POST["name"],category=request.POST["category"],language=request.POST.get("language","en_IE"),body=request.POST["body"],status="draft")
            messages.success(request,"Template saved for Meta approval")
        elif action=="send":
            template=get_object_or_404(WhatsAppTemplate,pk=request.POST.get("template_id"),status="approved"); recipient=request.POST["recipient"].replace(" ","").replace("+","")
            delivery=MessageDelivery.objects.create(channel="whatsapp",recipient=recipient,template=template.name,status="sending")
            try:
                delivery.external_id=send_whatsapp_template(recipient,template.name,template.language); delivery.status="sent"; template.sent+=1; template.save(); messages.success(request,"WhatsApp template sent through Meta Cloud API")
            except Exception as exc: delivery.status="failed"; delivery.error=str(exc)[:1000]; messages.error(request,str(exc))
            delivery.save()
        return redirect("whatsapp-center")
    return render(request,"whatsapp_center.html",{"templates":WhatsAppTemplate.objects.order_by("-created_at"),"deliveries":MessageDelivery.objects.filter(channel="whatsapp").order_by("-created_at")[:25]})

@login_required
def automation_center(request):
    if request.method=="POST":
        action=request.POST.get("action")
        if action=="create": Automation.objects.create(name=request.POST["name"],trigger=request.POST["trigger"],conditions=[x.strip() for x in request.POST.get("conditions","").splitlines() if x.strip()],actions=[x.strip() for x in request.POST.get("actions","").splitlines() if x.strip()],status="paused")
        elif action=="run": execute_workflow.delay(int(request.POST["id"])); messages.success(request,"Workflow execution queued")
        elif action in {"activate","pause"}: Automation.objects.filter(pk=request.POST["id"]).update(status="active" if action=="activate" else "paused")
        return redirect("automation-center")
    return render(request,"automation_center.html",{"automations":Automation.objects.order_by("-created_at"),"runs":WorkflowRun.objects.select_related("automation").order_by("-started_at")[:30]})

@login_required
def integrations_center(request):
    if request.method=="POST":
        service=request.POST.get("service")
        started=timezone.now()
        try: message=test_integration(service); status="connected"; messages.success(request,message)
        except Exception as exc: message=str(exc); status="failed"; messages.error(request,message)
        latency=int((timezone.now()-started).total_seconds()*1000); integration=Integration.objects.filter(category__iexact=service).first(); IntegrationCheck.objects.create(integration=integration,service=service,status=status,latency_ms=latency,message=message[:300],checked_by=request.user)
        if integration: integration.status=status; integration.last_sync_at=timezone.now(); integration.last_error="" if status=="connected" else message; integration.save()
        return redirect("integrations-center")
    return render(request,"integrations_center.html",{"integrations":Integration.objects.order_by("category"),"checks":IntegrationCheck.objects.order_by("-checked_at")[:30]})

@login_required
def aeo_center(request):
    if request.method=="POST":
        action=request.POST.get("action")
        if action=="create": AeoEntry.objects.create(question=request.POST["question"],answer=request.POST["answer"],topic=request.POST["topic"],market=request.POST.get("market","Ireland"),language=request.POST.get("language","en-IE"),schema_type=request.POST.get("schema_type","FAQPage"),citations=[x.strip() for x in request.POST.get("citations","").splitlines() if x.strip()],status="review")
        elif action=="generate":
            decision=run_ai_engine("AEO Answer Engine",f"Create a concise authoritative answer with evidence for: {request.POST['question']}",request.user)
            AeoEntry.objects.create(question=request.POST["question"],answer=decision.recommendation,topic="AI generated",status="review",authority_score=decision.confidence,citations=decision.evidence); messages.success(request,"AI answer created for owner review")
        elif action=="publish": AeoEntry.objects.filter(pk=request.POST["id"]).update(status="published",published_at=timezone.now())
        return redirect("aeo-center")
    return render(request,"aeo_center.html",{"entries":AeoEntry.objects.order_by("-created_at")})

@login_required
def integration_health(request):
    result=None
    if request.method=="POST":
        try: result=test_integration(request.POST["kind"]); messages.success(request,result)
        except Exception as exc: messages.error(request,str(exc))
    return render(request,"health.html")
@login_required
def orchestrator(request):
    if request.method=="POST":
        try: decision=run_ai_engine(request.POST["engine"],request.POST["prompt"],request.user); messages.success(request,f"Decision {decision.decision_id} created for owner review")
        except Exception as exc: messages.error(request,str(exc))
    return render(request,"orchestrator.html",{"decisions":AiDecision.objects.order_by("-created_at")[:20]})
@login_required
def api_dashboard(request): return JsonResponse({"leads":Lead.objects.count(),"campaigns":Campaign.objects.count(),"pending_decisions":AiDecision.objects.filter(status="pending").count()})
@csrf_exempt
def whatsapp_webhook(request):
    from django.conf import settings
    if request.method=="GET":
        if request.GET.get("hub.verify_token")==settings.WHATSAPP_VERIFY_TOKEN: return HttpResponse(request.GET.get("hub.challenge",""))
        return HttpResponse("Forbidden",status=403)
    AuditLog.objects.create(event="whatsapp.webhook",new_values={"received":True}); return JsonResponse({"received":True})
@csrf_exempt
@require_POST
def unsubscribe(request):
    try: payload=json.loads(request.body or b"{}")
    except json.JSONDecodeError: payload=request.POST
    email=payload.get("email","").strip().lower()
    if not email: return JsonResponse({"error":"email required"},status=422)
    Lead.objects.filter(email=email).update(consent_at=None,status="unsubscribed"); ConsentEvent.objects.create(email=email,action="unsubscribe",source="api"); return JsonResponse({"status":"unsubscribed"})
def health(request): return JsonResponse({"status":"ok","service":"Emerald Rozalia Marketing Centre","runtime":"Python 3.14.3"})
