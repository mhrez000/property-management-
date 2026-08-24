"""DRF glue for organisation scoping.

DRF authentication runs inside the view (after middleware), so token-based
API requests resolve their organisation here rather than in middleware. The
middleware still owns cleanup at end of request.
"""

from common.tenancy import activate_org, get_current_org_id


class OrgScopedViewMixin:
    """Resolve and activate the request's organisation for API views.

    Hooked into ``perform_authentication`` so ``self.membership`` is set
    before DRF's permission checks run (``common.permissions
    .OrgRolePermission`` denies requests with no membership). An explicit
    ``X-Organisation-Id`` header selects between multiple memberships, and is
    only honoured when the user actually belongs to that organisation.
    """

    membership = None

    def perform_authentication(self, request):
        super().perform_authentication(request)
        user = request.user
        if not (user and user.is_authenticated):
            return
        memberships = user.memberships.select_related("organisation")
        requested = request.headers.get("X-Organisation-Id")
        if requested:
            try:
                self.membership = memberships.filter(organisation_id=requested).first()
            except ValueError:  # malformed UUID in the header
                self.membership = None
        else:
            self.membership = memberships.first()
        if self.membership is not None:
            activate_org(self.membership.organisation_id)

    def get_organisation_id(self):
        return get_current_org_id()
