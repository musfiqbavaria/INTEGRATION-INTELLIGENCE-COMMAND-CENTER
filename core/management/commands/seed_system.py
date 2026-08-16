from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.utils import timezone
from core.models import *
import os
class Command(BaseCommand):
    help="Create owner account and operational demonstration data"
    def handle(self,*args,**kwargs):
        owner,created=User.objects.get_or_create(username="urmos@rozalia.ie",defaults={"email":"urmos@rozalia.ie","first_name":"Emerald","last_name":"Rozalia","is_staff":True,"is_superuser":True})
        password=os.environ.get("OWNER_PASSWORD","")
        if created:
            # Fail loudly rather than falling back to a default that is published
            # in this repository, and never reset an existing owner's password.
            if not password:
                raise CommandError("OWNER_PASSWORD is not set. Export it before seeding so the owner account gets a private password.")
            owner.set_password(password); owner.save()
            self.stdout.write(self.style.SUCCESS("Owner account created"))
        else:
            self.stdout.write("Owner account already exists — password left unchanged (use `manage.py changepassword` to alter it)")
        campaigns=[("Irish Heritage Autumn Launch","Email","active",4200,24300,1850),("Wholesale Partner Welcome","Email + WhatsApp","scheduled",860,0,420),("Abandoned Basket Recovery","Automation","active",1240,8900,380)]
        for name,channel,status,audience,revenue,cost in campaigns: Campaign.objects.get_or_create(name=name,defaults={"channel":channel,"status":status,"audience_size":audience,"revenue":revenue,"cost":cost})
        leads=[("Aoife","Murphy","aoife@example.ie","Celtic Retail",86),("James","Kelly","james@example.com","Heritage Outfitters",72),("Sofia","Rossi","sofia@example.it","Verde Moda",64)]
        for first,last,email,company,score in leads: Lead.objects.get_or_create(email=email,defaults={"first_name":first,"last_name":last,"company":company,"market":"Ireland","source":"Website","status":"qualified","score":score,"consent_at":timezone.now()})
        attention=[("critical","High-Risk Decision","Campaign budget overrun",61,"Campaign Engine"),("high","High-Impact Opportunity","Untapped audience segment",87,"AI Audience Engine"),("critical","AI Engine Conflict","Audience targeting conflict",45,"AI Engine Monitor"),("medium","Low-Confidence Situation","Conversion prediction low",38,"Analytics Engine"),("critical","Governance / Ethical Alert","Data privacy compliance risk",92,"Compliance Engine"),("low","Low-Impact Notification","Email template optimization",72,"Content AI"),("safe","Auto-Executed","Daily data backup completed",100,"System Monitor")]
        for severity,category,title,confidence,source in attention: AttentionItem.objects.get_or_create(title=title,defaults={"severity":severity,"category":category,"confidence":confidence,"source":source,"recommendation":"Owner review and next-best governed action","due_at":timezone.now()+timezone.timedelta(hours=2)})
        FinancialRecord.objects.get_or_create(campaign="Irish Heritage Collection",defaults={"market":"Ireland","system":"Email Marketing","channel":"Email","revenue":24300,"cost":1850,"leads":420,"customers":86})
        EmailCampaign.objects.get_or_create(name="Irish Heritage Collection",defaults={"subject":"Wear your Irish story","preview_text":"Discover the new Emerald Rozalia collection.","content":"<h1>Emerald Rozalia</h1><p>Our newest Irish-inspired hats and caps are ready.</p>","segment":"all","status":"draft"})
        ContentItem.objects.get_or_create(title="Irish Heritage Cap Buying Guide",defaults={"type":"article","channel":"Website & SEO","status":"published","body":"A guide to choosing an authentic Irish-inspired cap.","seo_score":91,"ai_confidence":94,"published_at":timezone.now()})
        Automation.objects.get_or_create(name="New Lead Welcome",defaults={"trigger":"Lead created with consent","conditions":["consent confirmed"],"actions":["Send welcome email","Assign lead score","Create owner summary"],"status":"active","runs":286,"successes":281,"failures":5})
        Automation.objects.get_or_create(name="Abandoned Basket Recovery",defaults={"trigger":"Basket inactive for 2 hours","conditions":["contactable","not purchased"],"actions":["Send recovery email","Wait 20 hours","Send approved WhatsApp reminder"],"status":"active","runs":1240,"successes":1198,"failures":42})
        for name,provider,category,status in [("PostgreSQL Database","PostgreSQL","Database","connected"),("Transactional Email","SMTP","Email","pending"),("WhatsApp Business","Meta Cloud API","WhatsApp","pending"),("Live AI","OpenAI Responses API","AI","pending")]: Integration.objects.get_or_create(name=name,defaults={"provider":provider,"category":category,"status":status})
        AeoEntry.objects.get_or_create(question="Who manufactures Irish-inspired hats and caps in Ireland?",defaults={"answer":"Emerald Rozalia Limited is an Irish hats and cap manufacturer and franchise business based in Limerick, Ireland.","topic":"Irish hat manufacturer","market":"Ireland","language":"en-IE","status":"published","schema_type":"FAQPage","authority_score":92,"citations":["https://emeraldrozalia.com/"]})
        WhatsAppTemplate.objects.get_or_create(name="wholesale_welcome",defaults={"category":"marketing","language":"en_IE","body":"Welcome to Emerald Rozalia Limited wholesale. Reply to discuss your account.","status":"approved","sent":4850,"delivered":4782,"read_count":4210,"replies":624})
        WhatsAppTemplate.objects.get_or_create(name="order_update",defaults={"category":"utility","language":"en_IE","body":"Your Emerald Rozalia order status has been updated.","status":"approved","sent":12400,"delivered":12295,"read_count":11680,"replies":384})
        for group,key,value in [("brand","company_name","Emerald Rozalia Limited"),("contact","phone","089 978 8187"),("contact","email","urmos@rozalia.ie"),("locale","timezone","Europe/Dublin"),("governance","gdpr_mode","enabled")]: Setting.objects.get_or_create(group=group,key=key,defaults={"value":value})
        AuditLog.objects.create(user=owner,event="system.seeded",new_values={"runtime":"Python 3.14.3"})
        self.stdout.write(self.style.SUCCESS("Emerald Rozalia system seeded"))
