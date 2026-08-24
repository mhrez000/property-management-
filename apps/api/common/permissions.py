from rest_framework.permissions import SAFE_METHODS, BasePermission


class OrgRolePermission(BasePermission):
    """Reads for any member; writes require a write-capable role.

    Relies on ``OrgScopedViewMixin`` having resolved ``view.membership``.
    """

    def has_permission(self, request, view):
        membership = getattr(view, "membership", None)
        if membership is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        return membership.can_write
