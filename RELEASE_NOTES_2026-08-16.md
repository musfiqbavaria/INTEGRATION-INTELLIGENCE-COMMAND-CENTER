# Emerald Rozalia Marketing Centre — server-master correction build

This build is based directly on the uploaded `Automation(1).zip`, which was identified as the version currently running on the server.

## Corrections included

- Separates `/` Executive Dashboard from `/command-center/` operational Command Center.
- Adds a live Executive Dashboard sourced from current database records.
- Adds visible reconciliation warnings when CRM, analytics, or email recipient totals disagree.
- Fixes Audit Logs inheriting the Analytics & Reports title/KPI block.
- Makes AI Intelligence generic records read-only; owner review remains in the AI Orchestrator workflow.
- Makes Finance generic outcome records read-only so revenue/cost/leads/customers are not casually manufactured through CRUD.
- Removes Campaign audience/revenue/cost from generic operator editing.
- Removes Content SEO score and AI confidence from generic operator editing.
- Restricts Settings to editing approved existing keys and prevents arbitrary key creation.
- Generates Help Desk ticket references server-side and audit-logs ticket creation.
- Preserves protection against deleting the final owner/superuser.
- Keeps the existing visual system, integrations, email, WhatsApp, automation, AEO, deployment and database architecture.

## Security / deployment

The export does not contain the uploaded server `.env`, `.git`, `.venv`, local SQLite database, caches, or compiled bytecode. Do not overwrite the live server `.env`; keep the existing server-side secrets in place.

## Validation

- Python source compile check: passed (`python -m compileall`).
- Full Django test execution could not be rerun in the artifact environment because package installation requires external network access, which is unavailable here.
- No model/schema changes were made in this correction build, so no new Django migration is required.

## Deployment outline

1. Back up the current server directory, PostgreSQL database, and `.env`.
2. Extract this package into the application release directory.
3. Preserve/restore the live `.env` from the server.
4. Install dependencies from `requirements.txt` in the server virtual environment/container.
5. Run `python manage.py check`.
6. Run `python manage.py migrate` (safe even though this build adds no migration).
7. Run `python manage.py collectstatic --noinput`.
8. Restart the web and Celery services/containers.
9. Verify `/`, `/command-center/`, `/integration-health/`, email, WhatsApp webhook, automation worker and audit pages.
