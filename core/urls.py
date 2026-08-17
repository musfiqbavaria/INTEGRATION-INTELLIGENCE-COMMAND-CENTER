from django.urls import path
from . import views

# The generic `<slug:slug>/` module route is a catch-all and must stay last:
# anything added below it would never be reached.
urlpatterns=[
    path("login/",views.login_view,name="login"),
    path("logout/",views.logout_view,name="logout"),
    path("",views.executive_dashboard,name="dashboard"),
    path("command-center/",views.command_center,name="command-center"),
    path("revenue/",views.revenue_center,name="revenue-center"),
    path("email-marketing/",views.email_center,name="email-center"),
    path("whatsapp/",views.whatsapp_center,name="whatsapp-center"),
    path("automations/",views.automation_center,name="automation-center"),
    path("integrations/",views.integrations_center,name="integrations-center"),
    path("aeo/",views.aeo_center,name="aeo-center"),
    path("integration-health/",views.integration_health,name="integration-health"),
    path("ai-orchestrator/",views.orchestrator,name="orchestrator"),
    path("organisation/switch",views.switch_organisation,name="switch-organisation"),
    path("api/dashboard/",views.api_dashboard,name="api-dashboard"),
    path("api/webhooks/whatsapp",views.whatsapp_webhook,name="whatsapp-webhook"),
    path("api/consent/unsubscribe",views.unsubscribe,name="unsubscribe"),
    path("up",views.health,name="health"),
    # Engagement tracking. Short paths keep the URLs small inside the message.
    path("e/o/<str:token>.gif",views.track_open,name="track-open"),
    path("e/c/<str:token>",views.track_click,name="track-click"),
    path("e/u/<str:token>",views.track_unsubscribe,name="track-unsubscribe"),
    # Signed one-click owner actions from an alert email.
    path("act/<str:payload>",views.owner_action,name="owner-action"),
    path("answers/",views.public_answers,name="public-answers"),
    path("answers/<int:pk>/",views.public_answer,name="public-answer"),
    path("sitemap.xml",views.sitemap,name="sitemap"),
    path("robots.txt",views.robots,name="robots"),
    path("<slug:slug>/",views.module,name="module"),
]
