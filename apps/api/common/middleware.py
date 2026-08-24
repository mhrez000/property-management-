"""Request-scoped tenant context.

For session-authenticated requests (admin, browsable API) the organisation is
resolved here. Token-authenticated API requests are resolved later, in the DRF
layer (``common.drf.OrgScopedViewMixin``), because DRF authentication runs
inside the view. Either way this middleware guarantees the context is cleared
when the request ends, so no organisation leaks onto a pooled connection.
"""

from common.tenancy import activate_org, deactivate_org


class TenantContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                org = self._resolve_org(request, user)
                if org is not None:
                    activate_org(org.pk)
        except Exception:
            deactivate_org()
            raise
        try:
            return self.get_response(request)
        finally:
            deactivate_org()

    @staticmethod
    def _resolve_org(request, user):
        """Pick the organisation for this request.

        An explicit ``X-Organisation-Id`` header wins if the user is a member
        of that organisation; otherwise the user's sole/first membership is
        used.
        """
        memberships = user.memberships.select_related("organisation")
        requested = request.headers.get("X-Organisation-Id")
        if requested:
            membership = memberships.filter(organisation_id=requested).first()
            return membership.organisation if membership else None
        membership = memberships.first()
        return membership.organisation if membership else None
