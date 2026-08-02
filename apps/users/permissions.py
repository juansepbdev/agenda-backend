from rest_framework.permissions import BasePermission


def is_company_admin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role == user.Role.ADMIN))


class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_company_admin(request.user)
