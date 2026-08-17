"""Organisation scoping.

Every business model now carries an `organisation`. The console still behaves
as a single-entity system until a second organisation exists, and that is a
deliberate safety property rather than an unfinished edge:

* With one organisation, `scope()` is a no-op. No filter can hide a record from
  the owner because of a tenant key that was never meant to matter yet.
* With two or more, scoping is strict — a record belongs to exactly one entity
  and is invisible from the others.

The switch is the count of active organisations, so adding a second entity
turns isolation on everywhere at once instead of module by module.

`assign()` stamps the current organisation onto new records. Background work
has no request, so it falls back to the default entity; that is what keeps a
Celery-created attention item from becoming an orphan nobody can see.
"""
from django.core.cache import cache

from .models import Organisation

SESSION_KEY = "organisation_id"
COUNT_KEY = "core.tenancy.active_count"
COUNT_TTL = 300


def has_organisation(model):
    """Whether this model carries the tenant key."""
    return any(field.name == "organisation" for field in model._meta.fields)


def default_organisation():
    return Organisation.objects.filter(is_active=True).order_by("pk").first()


def active_count():
    """Cached count of active entities; read on nearly every page."""
    count = cache.get(COUNT_KEY)
    if count is None:
        count = Organisation.objects.filter(is_active=True).count()
        cache.set(COUNT_KEY, count, COUNT_TTL)
    return count


def forget_count():
    """Drop the cached count after an organisation is added, removed or renamed."""
    cache.delete(COUNT_KEY)


def is_multi_entity():
    return active_count() > 1


def current_organisation(request=None):
    """The entity the console is showing, honouring the session switcher."""
    if request is None:
        return default_organisation()
    cached = getattr(request, "organisation", None)
    if cached is not None:
        return cached
    chosen = None
    session = getattr(request, "session", None)
    if session is not None and session.get(SESSION_KEY):
        chosen = Organisation.objects.filter(pk=session[SESSION_KEY], is_active=True).first()
    return chosen or default_organisation()


def scope(queryset, organisation):
    """Limit a queryset to one entity, once more than one entity exists."""
    if organisation is None or not has_organisation(queryset.model) or not is_multi_entity():
        return queryset
    return queryset.filter(organisation=organisation)


def assign(obj, organisation=None):
    """Stamp an organisation onto a record that does not have one yet."""
    if not has_organisation(type(obj)) or getattr(obj, "organisation_id", None):
        return obj
    obj.organisation = organisation or default_organisation()
    return obj


class OrganisationMiddleware:
    """Resolve the active entity once per request.

    Placed after AuthenticationMiddleware so the session is available, and
    before the audit middleware so a mutation is logged against the entity it
    actually changed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organisation = current_organisation(request)
        request.multi_entity = is_multi_entity()
        return self.get_response(request)
