"""Conversaciones de prueba para el módulo de chat.

    python manage.py seed_chat_demo --company demo
    python manage.py seed_chat_demo --company demo --undo

Mismas garantías que `seed_dashboard_demo`: todo lo que crea queda marcado
(`Contact.source = "chat-seed"`, `Message.wa_message_id` con prefijo
`chat-seed-`), `--undo` borra exactamente eso y nada más, y repetir el comando
no duplica nada porque las claves naturales son el teléfono por empresa y el
`wa_message_id`.

Los teléfonos usan el prefijo reservado `+57300556XX`, que no colisiona con
números reales ni con los del seed del dashboard.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.advisors.models import Advisor
from apps.companies.models import Company
from apps.inbox.models import Contact, Conversation, Message
from apps.inbox.services import messaging

SEED_TAG = "chat-seed"
PHONE_PREFIX = "+57300556"

# (nombre, hilo, chatbot_encendido, sin_asignar, estado)
# El hilo son pares (quién, texto): "contact", "bot" o "agent".
THREADS = [
    {
        "name": "Camila Ospina",
        "chatbot": False,
        "unassigned": False,
        "status": Conversation.Status.OPEN,
        "messages": [
            ("contact", "Hola, vi el apartamento de Chapinero en la página. ¿Sigue disponible?"),
            ("bot", "¡Hola Camila! Sí, sigue disponible. ¿Quieres agendar una visita?"),
            ("contact", "Sí, me interesa. ¿Tienen algo el jueves por la tarde?"),
            ("agent", "Hola Camila, soy tu asesora. El jueves tengo libre a las 3:00 p. m., ¿te sirve?"),
            ("contact", "Perfecto, a esa hora me queda bien."),
        ],
    },
    {
        "name": "Andrés Beltrán",
        "chatbot": True,
        "unassigned": False,
        "status": Conversation.Status.OPEN,
        "messages": [
            ("contact", "Buenas, ¿cuánto vale el arriendo del local de Usaquén?"),
            ("bot", "Hola Andrés, el canon es de $4.500.000 más administración. ¿Te comparto la ficha?"),
            ("contact", "Sí por favor, y si tienen fotos mejor."),
        ],
    },
    {
        "name": "Diana Quintero",
        "chatbot": False,
        "unassigned": False,
        "status": Conversation.Status.OPEN,
        "unread": 2,
        "messages": [
            ("agent", "Diana, te confirmo la visita de mañana a las 10:00 a. m."),
            ("contact", "Gracias. ¿Qué documentos debo llevar?"),
            ("contact", "¿Y hay parqueadero para visitantes?"),
        ],
    },
    {
        "name": "Felipe Naranjo",
        "chatbot": True,
        "unassigned": True,
        "status": Conversation.Status.OPEN,
        "messages": [
            ("contact", "Hola, quiero información de casas en Cedritos."),
            ("bot", "¡Hola Felipe! Claro. ¿Buscas para compra o para arriendo?"),
        ],
    },
    {
        "name": "Marcela Ruiz",
        "chatbot": False,
        "unassigned": False,
        "status": Conversation.Status.RESOLVED,
        "messages": [
            ("contact", "Ya firmamos el contrato, muchas gracias por todo."),
            ("agent", "¡Con mucho gusto, Marcela! Cualquier cosa por aquí estamos."),
        ],
    },
    {
        "name": "Julián Cortés",
        "chatbot": False,
        "unassigned": False,
        "status": Conversation.Status.OPEN,
        "messages": [
            ("contact", "¿Me pueden llamar hoy? Estoy interesado en el apartaestudio."),
            ("agent", "Claro Julián, te marco en la próxima hora."),
        ],
        # Envío que WhatsApp rechazó: la UI debe marcarlo como fallido.
        "last_failed": True,
    },
]


class Command(BaseCommand):
    help = "Siembra conversaciones de chat de prueba repartidas entre los asesores."

    def add_arguments(self, parser):
        parser.add_argument("--company", default="demo", help="Slug de la empresa destino.")
        parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haría, sin escribir.")
        parser.add_argument("--undo", action="store_true", help="Borra solo lo sembrado por este comando.")
        parser.add_argument("--yes", action="store_true", help="No pedir confirmación en bases no locales.")

    def handle(self, *args, **options):
        company = self._company(options["company"])
        self._guard_remote_database(options)

        if options["undo"]:
            return self._undo(company, dry_run=options["dry_run"])

        advisors = list(
            Advisor.objects.select_related("user")
            .filter(company=company, is_active=True)
            .order_by("assignment_priority", "code")
        )
        if not advisors:
            raise CommandError(f"La empresa '{company.slug}' no tiene asesores activos. Corre antes `seed_demo`.")

        if options["dry_run"]:
            self.stdout.write(f"Crearía {len(THREADS)} conversaciones sobre {len(advisors)} asesores.")
            return

        created = 0
        for index, thread in enumerate(THREADS):
            advisor = None if thread.get("unassigned") else advisors[index % len(advisors)]
            if self._seed_thread(company, index, thread, advisor):
                created += 1

        self.stdout.write(self.style.SUCCESS(f"\nConversaciones sembradas: {created} nuevas, {len(THREADS)} en total."))
        self.stdout.write("Entra con cualquier asesor para ver solo las suyas.")

    # -- pasos ---------------------------------------------------------------

    @transaction.atomic
    def _seed_thread(self, company, index, thread, advisor) -> bool:
        phone = f"{PHONE_PREFIX}{index:02d}"
        contact, is_new = Contact.objects.get_or_create(
            company=company,
            phone_number=phone,
            defaults={"name": thread["name"], "source": SEED_TAG},
        )

        # El bot se apaga *después* de sembrar, porque `store_message` no lo mira,
        # pero el estado final es el que decide si el composer se habilita.
        conversation = messaging.get_or_create_open_conversation(contact)
        Conversation.objects.filter(pk=conversation.pk).update(advisor=advisor)

        moment = timezone.now()
        total = len(thread["messages"])
        for position, (sender, text) in enumerate(thread["messages"]):
            messaging.store_message(
                company=company,
                conversation_id=conversation.id,
                content=text,
                sender_type=sender,
                # Escalonado hacia atrás: el hilo sale en orden y con horas creíbles.
                timestamp=moment - timezone.timedelta(minutes=7 * (total - position)),
                wa_message_id=f"{SEED_TAG}-{index:02d}-{position:02d}",
                mark_unread=False,
                status=(
                    Message.Status.FAILED
                    if thread.get("last_failed") and position == total - 1 and sender == "agent"
                    else None
                ),
            )

        contact.chatbot_enabled = thread["chatbot"]
        contact.source = SEED_TAG
        contact.save(update_fields=["chatbot_enabled", "source", "updated_at"])

        Conversation.objects.filter(pk=conversation.pk).update(
            status=thread["status"], unread_count=thread.get("unread", 0)
        )

        owner = advisor.user.get_full_name() if advisor else "sin asignar"
        mark = self.style.SUCCESS("+") if is_new else "="
        self.stdout.write(f"  {mark} {thread['name']:<20} → {owner}")
        return is_new

    def _undo(self, company, *, dry_run: bool):
        contacts = Contact.objects.filter(company=company, source=SEED_TAG)
        messages = Message.objects.filter(company=company, wa_message_id__startswith=f"{SEED_TAG}-")
        conversations = Conversation.objects.filter(company=company, contact__in=contacts)

        self.stdout.write(
            f"Borraría {contacts.count()} contactos, {conversations.count()} conversaciones "
            f"y {messages.count()} mensajes."
        )
        if dry_run:
            return

        with transaction.atomic():
            messages.delete()
            conversations.delete()
            contacts.delete()
        self.stdout.write(self.style.SUCCESS("Datos de chat de prueba eliminados."))

    # -- utilidades ----------------------------------------------------------

    def _company(self, slug):
        company = Company.objects.filter(slug=slug).first()
        if company is None:
            raise CommandError(f"No existe una empresa con slug '{slug}'.")
        return company

    def _guard_remote_database(self, options):
        """Sembrar datos falsos en una base compartida no debe poder pasar por accidente."""
        from django.db import connection

        if options["yes"] or "sqlite" in connection.vendor:
            return
        answer = input(f"La base no es SQLite ({connection.vendor}). ¿Continuar? [s/N] ")
        if answer.strip().lower() not in {"s", "si", "sí", "y", "yes"}:
            raise CommandError("Cancelado.")
