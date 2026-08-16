import json, secrets
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum, Count, Q
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

SEVERITIES=["critical","high","medium","low","safe"]
# Owner-attention card definitions. `category` matches AttentionItem.category so
# the counts come from the database rather than being written into the template.
ATTENTION_CARDS=[
    {"key":"critical","tone":"critical","icon":"🛡","title":"HIGH-RISK DECISIONS","desc":"Decisions requiring immediate owner approval with high impact or risk","category":"High-Risk Decision"},
    {"key":"high","tone":"high","icon":"🎯","title":"HIGH-IMPACT OPPORTUNITIES","desc":"Opportunities with high potential impact and positive ROI","category":"High-Impact Opportunity"},
    {"key":"conflict","tone":"conflict","icon":"🧠","title":"AI ENGINE CONFLICTS","desc":"Conflicts detected between AI engines or decision outcomes","category":"AI Engine Conflict"},
    {"key":"low","tone":"low","icon":"❓","title":"LOW-CONFIDENCE SITUATIONS","desc":"AI confidence is low or data is incomplete, needs owner review","category":"Low-Confidence Situation"},
    {"key":"safe","tone":"safe","icon":"⚖","title":"GOVERNANCE ETHICAL ALERTS","desc":"Policy, compliance, ethical or governance alerts","category":"Governance / Ethical Alert"},
]
IMPACT_BANDS=[("Very High",20000),("High",10000),("Medium",5000),("Low",1000),("Very Low",0)]

def _spark(values,width=112,height=26):
    """SVG polyline points for a KPI sparkline. A flat line means insufficient history."""
    values=[float(v or 0) for v in values]
    if len(values)<2 or max(values)==min(values):
        return f"0,{height/2:.1f} {width},{height/2:.1f}"
    lo,hi=min(values),max(values); step=width/(len(values)-1)
    return " ".join(f"{i*step:.1f},{height-((v-lo)/(hi-lo))*height:.1f}" for i,v in enumerate(values))

def _delta(current,previous):
    """Percentage change between two periods, or None when there is no baseline."""
    if not previous: return None
    return round((float(current)-float(previous))/float(previous)*100,1)

def _impact_band(value):
    """Bucket a monetary impact into the risk-matrix rows."""
    v=float(value or 0)
    for label,floor in IMPACT_BANDS:
        if v>=floor: return label
    return IMPACT_BANDS[-1][0]

@login_required
def dashboard(request):
    now=timezone.now(); today=timezone.localdate()
    window_start=today-timezone.timedelta(days=30); prior_start=today-timezone.timedelta(days=60)

    records=FinancialRecord.objects.all()
    totals=records.aggregate(revenue=Sum("revenue"),cost=Sum("cost"),leads=Sum("leads"),customers=Sum("customers"))
    revenue=totals["revenue"] or 0; cost=totals["cost"] or 0; profit=revenue-cost
    attributed_leads=totals["leads"] or 0; customers=totals["customers"] or 0
    conversion=round(customers/attributed_leads*100,1) if attributed_leads else 0

    # 30-day window against the preceding 30 days, for the "vs Last 30 Days" deltas.
    current=records.filter(recorded_on__gte=window_start).aggregate(r=Sum("revenue"),c=Sum("cost"),l=Sum("leads"),cu=Sum("customers"))
    prior=records.filter(recorded_on__gte=prior_start,recorded_on__lt=window_start).aggregate(r=Sum("revenue"),c=Sum("cost"),l=Sum("leads"),cu=Sum("customers"))
    cur_rev=current["r"] or 0; pri_rev=prior["r"] or 0
    cur_profit=(current["r"] or 0)-(current["c"] or 0); pri_profit=(prior["r"] or 0)-(prior["c"] or 0)
    cur_conv=round((current["cu"] or 0)/(current["l"] or 1)*100,1); pri_conv=round((prior["cu"] or 0)/(prior["l"] or 1)*100,1)

    # Daily series drive the sparklines. Flat lines mean there is not enough history yet.
    daily=list(records.values("recorded_on").annotate(r=Sum("revenue"),c=Sum("cost"),l=Sum("leads")).order_by("recorded_on"))
    rev_series=[d["r"] or 0 for d in daily]
    profit_series=[(d["r"] or 0)-(d["c"] or 0) for d in daily]
    lead_series=[d["l"] or 0 for d in daily]

    automations_active=Automation.objects.filter(status="active").count()
    wa=WhatsAppTemplate.objects.aggregate(s=Sum("sent"),d=Sum("delivered"))
    whatsapp_sent=wa["s"] or 0; whatsapp_delivered=wa["d"] or 0
    # Delivery rate, not a period-over-period delta.
    whatsapp_rate=round(whatsapp_delivered/whatsapp_sent*100,1) if whatsapp_sent else None
    whatsapp_pending=MessageDelivery.objects.filter(channel="whatsapp").exclude(status="sent").count()
    email_drafts=EmailCampaign.objects.filter(status="draft").count()

    items=list(AttentionItem.objects.order_by("-created_at"))
    open_items=[i for i in items if i.status!="resolved"]
    for item in open_items:
        item.is_overdue=bool(item.due_at and item.due_at<now)
    by_severity={s:sum(1 for i in items if i.severity==s) for s in SEVERITIES}
    by_category={row["category"]:row["n"] for row in AttentionItem.objects.values("category").annotate(n=Count("id"))}

    cards=[dict(c,count=by_category.get(c["category"],0)) for c in ATTENTION_CARDS]
    opportunity_value=AttentionItem.objects.filter(category="High-Impact Opportunity").aggregate(v=Sum("impact"))["v"] or 0
    overdue=sum(1 for i in items if i.due_at and i.due_at<now and i.status!="resolved")

    # 5x5 risk matrix: impact magnitude down, severity across. Both axes come from
    # the AttentionItem rows, so an empty cell genuinely means no item in that band.
    matrix=[{"label":label,
             "cells":[{"n":sum(1 for i in items if i.severity==sev and _impact_band(i.impact)==label),"tone":sev}
                      for sev in SEVERITIES]}
            for label,_ in IMPACT_BANDS]
    column_totals=[{"n":by_severity[s],"tone":s} for s in SEVERITIES]

    filters=[{"key":"all","label":"All","count":len(items),"tone":"all"}]
    filters+= [{"key":s,"label":l,"count":by_severity[s],"tone":s} for s,l in
               [("critical","Critical"),("high","High Impact"),("medium","Medium"),("low","Low"),("safe","Safe")]]
    filters.append({"key":"overdue","label":"Overdue","count":overdue,"tone":"overdue"})

    summary=[
        {"tone":"critical","count":by_severity["critical"],"title":"Critical Items","note":"Need immediate action"},
        {"tone":"high","count":by_category.get("High-Impact Opportunity",0),"title":"High-Impact Opportunities","note":f"Potential value € {opportunity_value:,.0f}"},
        {"tone":"conflict","count":by_category.get("AI Engine Conflict",0),"title":"AI Engine Conflicts","note":"Owner decision required"},
        {"tone":"low","count":by_category.get("Low-Confidence Situation",0),"title":"Low-Confidence Items","note":"Review recommended"},
        {"tone":"medium","count":by_category.get("Governance / Ethical Alert",0),"title":"Governance / Ethical Alerts","note":"Compliance action required"},
        {"tone":"safe","count":by_severity["safe"],"title":"Safe / Auto-Executed","note":"Running smoothly"},
    ]

    avg_confidence=round(sum(i.confidence for i in items)/len(items)) if items else None
    decisions=list(AiDecision.objects.all())
    # None rather than 0 so the template can show "—" instead of implying a real
    # score of zero when the orchestrator has not produced any decisions yet.
    decision_accuracy=round(sum(d.confidence for d in decisions)/len(decisions),1) if decisions else None
    risk_score=round(sum(d.risk_score for d in decisions)/len(decisions)) if decisions else None

    return render(request,"dashboard.html",{
        "revenue":revenue,"profit":profit,"leads":Lead.objects.count(),"conversion":conversion,
        "automations_active":automations_active,"whatsapp_sent":whatsapp_sent,
        "whatsapp_pending":whatsapp_pending,"email_drafts":email_drafts,
        "revenue_delta":_delta(cur_rev,pri_rev),"profit_delta":_delta(cur_profit,pri_profit),
        "leads_delta":_delta(current["l"] or 0,prior["l"] or 0),"conversion_delta":_delta(cur_conv,pri_conv),
        "whatsapp_delta":whatsapp_rate,
        "spark_revenue":_spark(rev_series),"spark_profit":_spark(profit_series),"spark_leads":_spark(lead_series),
        "spark_conversion":_spark(lead_series),"spark_whatsapp":_spark(rev_series),
        "cards":cards,"matrix":matrix,"column_totals":column_totals,"severities":SEVERITIES,
        "filters":filters,"summary":summary,"attention":open_items[:8],"open_count":len(open_items),
        "overdue":overdue,"avg_confidence":avg_confidence,"decision_accuracy":decision_accuracy,
        "risk_score":risk_score,"decisions":AiDecision.objects.filter(status="pending").count(),
        "generated_at":timezone.localtime(now),
    })

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
