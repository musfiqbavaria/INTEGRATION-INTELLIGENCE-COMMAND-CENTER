import json, uuid, httpx
from django.conf import settings
from django.core.mail import send_mail
from .models import AiDecision

def test_integration(kind):
    if kind=="database":
        from django.db import connection
        with connection.cursor() as cursor: cursor.execute("SELECT 1")
        return "Database connection successful"
    if kind=="smtp":
        send_mail("Emerald Rozalia SMTP verification","Your Python Marketing Centre SMTP connection is working.",None,["urmos@rozalia.ie"])
        return "Verification email queued"
    if kind=="whatsapp":
        if not settings.WHATSAPP_ACCESS_TOKEN: raise ValueError("WhatsApp credentials are not configured")
        r=httpx.get(f"https://graph.facebook.com/v23.0/{settings.WHATSAPP_PHONE_NUMBER_ID}",headers={"Authorization":f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},timeout=15); r.raise_for_status()
        return "WhatsApp connection successful"
    if kind=="openai":
        if not settings.OPENAI_API_KEY: raise ValueError("OpenAI API key is not configured")
        from openai import OpenAI
        OpenAI(api_key=settings.OPENAI_API_KEY).responses.create(model=settings.OPENAI_MODEL,input="Reply only with OK",max_output_tokens=64)
        return "OpenAI connection successful"
    raise ValueError("Unknown integration")

def run_ai_engine(engine, prompt, user=None):
    if not settings.OPENAI_API_KEY: raise ValueError("OpenAI API key is not configured")
    from openai import OpenAI
    schema={"type":"object","properties":{"title":{"type":"string"},"recommendation":{"type":"string"},"evidence":{"type":"array","items":{"type":"string"}},"confidence":{"type":"integer","minimum":0,"maximum":100},"impact":{"type":"string","enum":["low","medium","high","critical"]},"risk_score":{"type":"integer","minimum":0,"maximum":100},"expected_outcome":{"type":"string"}},"required":["title","recommendation","evidence","confidence","impact","risk_score","expected_outcome"],"additionalProperties":False}
    response=OpenAI(api_key=settings.OPENAI_API_KEY).responses.create(model=settings.OPENAI_MODEL,input=[{"role":"system","content":"You are the governed Emerald Rozalia marketing decision engine. Use cautious evidence-based analysis."},{"role":"user","content":prompt}],text={"format":{"type":"json_schema","name":"decision","schema":schema,"strict":True}})
    data=json.loads(response.output_text)
    return AiDecision.objects.create(decision_id=f"ER-{uuid.uuid4().hex[:10].upper()}",engine=engine,governance_level="review",status="pending",owner=user,**data)

def send_whatsapp_template(recipient, template_name, language="en_IE"):
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID: raise ValueError("WhatsApp production credentials are not configured")
    payload={"messaging_product":"whatsapp","to":recipient,"type":"template","template":{"name":template_name,"language":{"code":language}}}
    response=httpx.post(f"https://graph.facebook.com/v23.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",headers={"Authorization":f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},json=payload,timeout=20)
    response.raise_for_status(); data=response.json(); return data.get("messages",[{}])[0].get("id","")
