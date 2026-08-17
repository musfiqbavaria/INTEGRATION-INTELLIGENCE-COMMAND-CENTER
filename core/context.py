"""Template context shared by every page of the console."""
from .models import Organisation
from .tenancy import is_multi_entity


def organisations(request):
    """The entity switcher's options.

    Only queried when a second organisation exists, so the single-entity case
    — which is every installation until someone adds one — costs nothing.
    """
    if not is_multi_entity():
        return {"organisations": []}
    return {"organisations": Organisation.objects.filter(is_active=True)}
