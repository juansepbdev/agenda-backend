"""Alta y mantenimiento del canal de WhatsApp de una empresa.

La credencial del webhook se guarda hasheada, así que este comando es la única
vía para obtenerla en claro (una sola vez, al generarla).

    python manage.py inbox_channel --company <slug> --rotate-key
    python manage.py inbox_channel --company <slug> --ycloud-from +573001112233
"""

from django.core.management.base import BaseCommand, CommandError

from apps.companies.models import Company
from apps.inbox.models import WhatsAppChannel


class Command(BaseCommand):
    help = "Crea o actualiza el canal de WhatsApp de una empresa y rota su credencial de webhook."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Slug de la empresa.")
        parser.add_argument("--rotate-key", action="store_true", help="Genera una credencial nueva.")
        parser.add_argument("--inbox-name")
        parser.add_argument("--ycloud-api-key")
        parser.add_argument("--ycloud-from")
        parser.add_argument("--ycloud-api-base")
        parser.add_argument("--n8n-url")
        parser.add_argument("--n8n-timeout", type=float)
        parser.add_argument("--verify-token")
        parser.add_argument("--deactivate", action="store_true")
        parser.add_argument("--activate", action="store_true")

    def handle(self, *args, **options):
        try:
            company = Company.objects.get(slug=options["company"])
        except Company.DoesNotExist as exc:
            raise CommandError(f"No existe la empresa con slug '{options['company']}'.") from exc

        channel, created = WhatsAppChannel.objects.get_or_create(company=company)

        updates = {
            "inbox_name": options.get("inbox_name"),
            "ycloud_api_key": options.get("ycloud_api_key"),
            "ycloud_from": options.get("ycloud_from"),
            "ycloud_api_base": options.get("ycloud_api_base"),
            "n8n_webhook_url": options.get("n8n_url"),
            "n8n_timeout_seconds": options.get("n8n_timeout"),
            "verify_token": options.get("verify_token"),
        }
        for field, value in updates.items():
            if value is not None:
                setattr(channel, field, value)

        if options["deactivate"]:
            channel.is_active = False
        if options["activate"]:
            channel.is_active = True

        raw_key = None
        if created or options["rotate_key"] or not channel.webhook_key_hash:
            raw_key = channel.rotate_webhook_key(save=False)

        channel.save()

        self.stdout.write(self.style.SUCCESS(f"{'Creado' if created else 'Actualizado'} el canal de {company.name}."))
        self.stdout.write(f"  envío YCloud configurado: {'sí' if channel.can_send else 'no'}")
        self.stdout.write(f"  n8n configurado: {'sí' if channel.n8n_webhook_url else 'no'}")
        if raw_key:
            self.stdout.write(self.style.WARNING("  credencial de webhook (no se vuelve a mostrar):"))
            self.stdout.write(f"  X-API-Key: {raw_key}")
