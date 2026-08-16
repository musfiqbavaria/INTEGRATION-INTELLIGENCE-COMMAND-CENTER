from django.contrib import admin
from . import models
for model in [models.Campaign,models.Lead,models.AttentionItem,models.ContentItem,models.EmailCampaign,models.Automation,models.Integration,models.FinancialRecord,models.AeoEntry,models.AiDecision,models.WhatsAppTemplate,models.Setting,models.AuditLog,models.SupportTicket,models.ConsentEvent,models.WorkflowRun,models.IntegrationCheck,models.MessageDelivery]: admin.site.register(model)
