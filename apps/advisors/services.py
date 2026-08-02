from django.db import transaction
from django.utils import timezone

from .models import AdvisorSupervision


@transaction.atomic
def assign_advisor_to_supervisor(*, company, supervisor_user, advisor, assigned_by, valid_from):
    if any(obj.company_id != company.id for obj in (supervisor_user, advisor, assigned_by)): raise ValueError("Relación fuera de empresa.")
    AdvisorSupervision.objects.filter(company=company, advisor=advisor, is_active=True).update(is_active=False, valid_until=valid_from)
    return AdvisorSupervision.objects.create(company=company, supervisor_user=supervisor_user, advisor=advisor, assigned_by=assigned_by, valid_from=valid_from)
@transaction.atomic
def remove_advisor_from_supervisor(*, supervision):
    supervision.is_active=False; supervision.valid_until=timezone.localdate(); supervision.save(update_fields=["is_active","valid_until","updated_at"]); return supervision
def get_supervisor_active_advisors(*, user): return user.supervised_assignments.filter(is_active=True).select_related("advisor__user")
def validate_supervision_scope(*, user, advisor): return user.role == user.Role.ADMIN or get_supervisor_active_advisors(user=user).filter(advisor=advisor).exists()
