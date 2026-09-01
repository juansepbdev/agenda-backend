"""Envía los seguimientos vencidos. El espejo local del cron de Vercel.

    python manage.py dispatch_follow_ups --company demo --dry-run
    python manage.py dispatch_follow_ups

La vista `follow_up_cron` es una cáscara de autenticación sobre este mismo
servicio: lo que se prueba aquí es lo que corre en producción.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.clients.follow_ups import MAX_SENDS_PER_RUN, dispatch_company
from apps.companies.models import Company


class Command(BaseCommand):
    help = "Envía los seguimientos de leads que ya vencieron."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="Slug de una empresa. Por defecto, todas las activas.")
        parser.add_argument("--limit", type=int, default=MAX_SENDS_PER_RUN, help="Tope de envíos por empresa.")
        parser.add_argument("--dry-run", action="store_true", help="Cuenta lo que enviaría, sin enviar.")

    def handle(self, *args, **options):
        companies = Company.objects.filter(is_active=True).order_by("created_at")
        if options["company"]:
            companies = companies.filter(slug=options["company"])
            if not companies.exists():
                raise CommandError(f"No existe una empresa activa con slug '{options['company']}'.")

        total = 0
        for company in companies:
            if not company.can_operate:
                continue
            result = dispatch_company(company=company, limit=options["limit"], dry_run=options["dry_run"])
            total += result["sent"]
            detail = result.get("skipped") or (
                f"{result['sent']} enviados, {result.get('skipped_sends', 0)} omitidos, {result['pending']} en espera"
            )
            self.stdout.write(f"  {company.slug:<20} {detail}")

        self.stdout.write(self.style.SUCCESS(f"\nSeguimientos enviados: {total}"))
