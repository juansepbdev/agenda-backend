"""Datos de demostración para que el dashboard tenga algo que mostrar.

    python manage.py seed_dashboard_demo --company <slug> --dry-run
    python manage.py seed_dashboard_demo --company <slug>
    python manage.py seed_dashboard_demo --company <slug> --undo

Tres propiedades que hacen que esto sea seguro de correr contra una base
compartida:

* **Todo lo que crea queda marcado.** Contactos y clientes con `source`
  `demo-seed`, eventos con `external_reference` `demo-seed`. `--undo` borra
  exactamente eso y nada más.
* **Es idempotente.** Las claves naturales (teléfono por empresa,
  `idempotency_key` del evento) hacen que repetir el comando no duplique nada.
* **Pide confirmación si la base no es SQLite.** Sembrar datos falsos en una
  base remota compartida no debe poder pasar por accidente.

Los teléfonos usan el prefijo reservado `+5730055500XX`, que no colisiona con
números reales, y las fechas se reparten en los últimos días para que las
series temporales, el mapa de calor y las tasas no salgan planas.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.advisors.models import Advisor
from apps.clients.models import Client
from apps.companies.models import Company
from apps.inbox.models import Contact, Conversation, Message
from apps.scheduling.models import Event
from apps.users.models import User

# Marca que identifica todo lo sembrado. `--undo` borra por esta etiqueta.
SEED_TAG = "demo-seed"
PHONE_PREFIX = "+57300555"

DEFAULT_COUNT = 5
MAX_COUNT = 5

# Cinco hilos, cada uno pensado para encender una métrica distinta del panel.
THREADS = [
    {
        "name": "Laura Gómez",
        "days_ago": 6,
        # Traspaso: el bot arranca y una persona termina -> handoff_rate.
        "messages": [
            ("contact", "Hola, vi el apartamento en Chapinero", 0),
            ("bot", "¡Hola Laura! Claro, ¿te gustaría agendar una visita?", 40),
            ("contact", "Sí, ¿tienen algo el martes?", 300),
            ("agent", "Te confirmo el martes a las 3pm", 1500),
        ],
        "status": Conversation.Status.RESOLVED,
        "chatbot_enabled": False,
        "client": True,
    },
    {
        "name": "Andrés Rivera",
        "days_ago": 4,
        # Solo bot -> full_automation_rate.
        "messages": [
            ("contact", "Buenas, ¿cuánto vale el arriendo?", 0),
            ("bot", "El canon es de $2.400.000 más administración", 25),
            ("contact", "Perfecto, gracias", 200),
            ("bot", "¿Quieres que te agende una visita?", 30),
        ],
        "status": Conversation.Status.OPEN,
        "chatbot_enabled": True,
        "client": False,
    },
    {
        "name": "María Fernanda Ruiz",
        "days_ago": 3,
        # Solo asesor, con una respuesta lenta -> mueve el p90.
        "messages": [
            ("contact", "Necesito hablar con un asesor", 0),
            ("agent", "Hola María, soy Carlos. ¿En qué te ayudo?", 5400),
            ("contact", "Quiero saber si aceptan mascotas", 600),
            ("agent", "Sí, en ese edificio sí las aceptan", 900),
        ],
        "status": Conversation.Status.OPEN,
        "chatbot_enabled": False,
        "client": False,
    },
    {
        "name": "Pedro Salazar",
        "days_ago": 1,
        # Nadie contestó -> unanswered_conversations y unread_count.
        "messages": [
            ("contact", "¿Hay alguien ahí?", 0),
            ("contact", "Sigo interesado en el local comercial", 1800),
        ],
        "status": Conversation.Status.OPEN,
        "chatbot_enabled": True,
        "client": False,
    },
    {
        "name": "Sofía Betancur",
        "days_ago": 0,
        # Hilo del día -> asegura datos en period=today.
        "messages": [
            ("contact", "Buenos días, ¿siguen disponibles las oficinas?", 0),
            ("bot", "¡Buenos días! Sí, quedan dos disponibles", 15),
            ("contact", "Me interesa la del piso 8", 240),
            ("bot", "Perfecto, ¿te agendo una visita esta semana?", 20),
        ],
        "status": Conversation.Status.RESOLVED,
        "chatbot_enabled": True,
        "client": True,
    },
]

# Cinco eventos que cubren todas las tasas del bloque de agenda.
EVENTS = [
    {"key": "1", "status": Event.Status.COMPLETED, "source": Event.Source.CHATBOT,
     "days": -5, "title": "Visita apartamento Chapinero", "client": 0, "auto": True},
    {"key": "2", "status": Event.Status.CANCELLED, "source": Event.Source.MANUAL,
     "days": -3, "title": "Visita casa Cedritos", "client": None, "auto": False},
    {"key": "3", "status": Event.Status.NO_SHOW, "source": Event.Source.MANUAL,
     "days": -2, "title": "Visita local comercial", "client": None, "auto": False},
    {"key": "4", "status": Event.Status.CONFIRMED, "source": Event.Source.MANUAL,
     "days": 1, "title": "Visita oficina Usaquén", "client": None, "auto": False},
    {"key": "5", "status": Event.Status.PENDING, "source": Event.Source.CHATBOT,
     "days": 3, "title": "Visita oficina piso 8", "client": 4, "auto": True},
]


class Command(BaseCommand):
    help = "Siembra datos de demostración marcados para que el dashboard muestre métricas."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Slug de la empresa destino.")
        parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                            help=f"Cuántos hilos y eventos crear (1..{MAX_COUNT}, por defecto {DEFAULT_COUNT}).")
        parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haría, sin escribir.")
        parser.add_argument("--undo", action="store_true", help="Borra solo lo sembrado por este comando.")
        parser.add_argument("--yes", action="store_true", help="No pedir confirmación en bases no locales.")

    # -- utilidades ----------------------------------------------------------

    def _phone(self, index: int) -> str:
        return f"{PHONE_PREFIX}{index:02d}"

    def _confirm_remote_write(self, options):
        """Una base que no es SQLite es, en este proyecto, la de Supabase."""
        from django.db import connection

        if connection.vendor == "sqlite" or options["yes"] or options["dry_run"]:
            return

        target = connection.settings_dict
        self.stdout.write(self.style.WARNING(
            f"\n  Destino NO local: {connection.vendor} · "
            f"{target.get('HOST') or '?'} · base '{target.get('NAME')}'."
        ))
        answer = input("  Escribe 'si' para continuar: ").strip().lower()
        if answer not in {"si", "sí"}:
            raise CommandError("Cancelado por el usuario.")

    def _require_inbox_tables(self):
        """El inbox puede no estar migrado en un entorno concreto.

        Sin esta comprobación el fallo llega como un `ProgrammingError` de
        Postgres a mitad de la transacción, que no le dice a nadie qué hacer.
        """
        from django.db import connection

        if Contact._meta.db_table in connection.introspection.table_names():
            return
        raise CommandError(
            "Las tablas del inbox no existen en esta base: falta aplicar "
            "`inbox.0001_initial`.\n"
            "  Revisa con : manage.py showmigrations inbox\n"
            "  Aplica con : manage.py migrate inbox"
        )

    def _get_advisor(self, company, *, dry_run: bool):
        """Reutiliza un asesor existente; solo crea uno si la empresa no tiene."""
        advisor = Advisor.objects.filter(company=company, is_active=True).order_by("code").first()
        if advisor:
            return advisor, False
        if dry_run:
            return None, True

        user, _ = User.objects.get_or_create(
            email=f"{SEED_TAG}.asesor@{company.slug}.demo",
            defaults={"company": company, "role": User.Role.ADVISOR,
                      "first_name": "Carlos", "last_name": "Pérez"},
        )
        advisor = Advisor.objects.create(company=company, user=user, code=f"{SEED_TAG}-A1")
        return advisor, True

    # -- ejecución -----------------------------------------------------------

    def handle(self, *args, **options):
        try:
            company = Company.objects.get(slug=options["company"])
        except Company.DoesNotExist as exc:
            raise CommandError(f"No existe la empresa con slug '{options['company']}'.") from exc

        count = max(1, min(options["count"], MAX_COUNT))
        self._require_inbox_tables()
        self._confirm_remote_write(options)

        if options["undo"]:
            return self._undo(company, dry_run=options["dry_run"])

        return self._seed(company, count=count, dry_run=options["dry_run"])

    def _seed(self, company, *, count: int, dry_run: bool):
        threads, events = THREADS[:count], EVENTS[:count]
        now = timezone.now()

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"\n[dry-run] Empresa: {company.name} ({company.slug})"))
            self.stdout.write(f"  Contactos y conversaciones: {len(threads)}")
            self.stdout.write(f"  Mensajes: {sum(len(t['messages']) for t in threads)}")
            self.stdout.write(f"  Clientes: {sum(1 for t in threads if t['client'])}")
            self.stdout.write(f"  Eventos: {len(events)}")
            advisor, would_create = self._get_advisor(company, dry_run=True)
            self.stdout.write(
                f"  Asesor: {'se crearía uno de demo' if would_create else f'se reutiliza {advisor.code}'}"
            )
            self.stdout.write("\n  Nada escrito. Quita --dry-run para aplicar.")
            return

        with transaction.atomic():
            advisor, advisor_created = self._get_advisor(company, dry_run=False)
            clients, created_contacts, created_messages = {}, 0, 0

            for index, thread in enumerate(threads):
                _, is_new = self._build_thread(company, index, thread, now)
                created_contacts += int(is_new)
                created_messages += len(thread["messages"])
                if thread["client"]:
                    clients[index] = self._build_client(company, index, thread)

            created_events = sum(
                int(self._build_event(company, advisor, clients, spec, now)) for spec in events
            )

        self.stdout.write(self.style.SUCCESS(f"\nSembrado en {company.name} ({company.slug}):"))
        self.stdout.write(f"  Contactos nuevos : {created_contacts} (de {len(threads)} hilos)")
        self.stdout.write(f"  Mensajes         : {created_messages}")
        self.stdout.write(f"  Clientes         : {len(clients)}")
        self.stdout.write(f"  Eventos nuevos   : {created_events} (de {len(events)})")
        if advisor_created:
            self.stdout.write(f"  Asesor de demo   : {advisor.code}")
        self.stdout.write(
            "\n  Revisa /api/v1/dashboard/overview/?period=7d"
            f"\n  Para revertir: manage.py seed_dashboard_demo --company {company.slug} --undo"
        )

    def _build_thread(self, company, index, thread, now):
        phone = self._phone(index)
        started = now - timedelta(days=thread["days_ago"], hours=3)

        contact, is_new = Contact.objects.get_or_create(
            company=company,
            phone_number=phone,
            defaults={"name": thread["name"], "source": SEED_TAG,
                      "chatbot_enabled": thread["chatbot_enabled"]},
        )
        conversation, _ = Conversation.objects.get_or_create(company=company, contact=contact)

        # Si el hilo ya existe, no se reescriben los mensajes: el comando es
        # idempotente y volver a correrlo no debe duplicar el historial.
        if not is_new and conversation.messages.exists():
            return contact, False

        moment, unread = started, 0
        for sender, content, offset in thread["messages"]:
            moment = moment + timedelta(seconds=offset)
            inbound = sender == "contact"
            message = Message.objects.create(
                company=company, conversation=conversation, contact=contact, content=content,
                message_type=Message.Type.INBOUND if inbound else Message.Type.OUTBOUND,
                sender_type=sender,
                status=Message.Status.RECEIVED if inbound else Message.Status.SENT,
            )
            # `created_at` es auto_now_add: hay que reescribirlo para repartir
            # los mensajes en el tiempo y que las series no salgan en un punto.
            Message.objects.filter(pk=message.pk).update(created_at=moment)
            unread = unread + 1 if inbound else 0

        last_sender = thread["messages"][-1][0]
        Conversation.objects.filter(pk=conversation.pk).update(
            status=thread["status"],
            assignment=(Conversation.Assignment.BOT if last_sender == "bot"
                        else Conversation.Assignment.ME if last_sender == "agent"
                        else Conversation.Assignment.UNASSIGNED),
            unread_count=unread,
            last_message_preview=thread["messages"][-1][1][:255],
            last_activity_at=moment,
        )
        Contact.objects.filter(pk=contact.pk).update(last_contact_at=moment, created_at=started)
        Conversation.objects.filter(pk=conversation.pk).update(created_at=started)
        return contact, is_new

    def _build_client(self, company, index, thread):
        first, _, last = thread["name"].partition(" ")
        client, _ = Client.objects.get_or_create(
            company=company,
            normalized_phone=self._phone(index),
            defaults={"first_name": first, "last_name": last, "phone": self._phone(index),
                      "source": Client.Source.CHATBOT, "notes": SEED_TAG},
        )
        # El contacto del inbox y el cliente de agenda son la misma persona.
        Contact.objects.filter(company=company, phone_number=self._phone(index)).update(client=client)
        return client

    def _build_event(self, company, advisor, clients, spec, now):
        start = (now + timedelta(days=spec["days"])).replace(minute=0, second=0, microsecond=0)
        _, created = Event.objects.get_or_create(
            company=company,
            idempotency_key=f"{SEED_TAG}-{spec['key']}",
            defaults={
                "advisor": advisor,
                "client": clients.get(spec["client"]),
                "status": spec["status"],
                "source": spec["source"],
                "title": spec["title"],
                "start_at": start,
                "end_at": start + timedelta(minutes=60),
                "external_reference": SEED_TAG,
                "assigned_automatically": spec["auto"],
                "no_show_type": (Event.NoShow.CLIENT_NO_SHOW
                                 if spec["status"] == Event.Status.NO_SHOW else ""),
                "completed_at": now + timedelta(days=spec["days"], hours=1)
                                if spec["status"] == Event.Status.COMPLETED else None,
                "cancelled_at": now + timedelta(days=spec["days"])
                                if spec["status"] == Event.Status.CANCELLED else None,
            },
        )
        return created

    # -- reverso -------------------------------------------------------------

    def _undo(self, company, *, dry_run: bool):
        """Borra solo lo marcado. El orden respeta las claves foráneas."""
        contacts = Contact.objects.filter(company=company, source=SEED_TAG)
        events = Event.objects.filter(company=company, external_reference=SEED_TAG)
        clients = Client.objects.filter(company=company, notes=SEED_TAG)
        messages = Message.objects.filter(company=company, contact__in=contacts)
        conversations = Conversation.objects.filter(company=company, contact__in=contacts)

        # Solo el asesor que creó este comando, y solo si ya no sostiene nada.
        demo_advisors = Advisor.objects.filter(company=company, code__startswith=SEED_TAG)

        counts = {
            "eventos": events.count(),
            "mensajes": messages.count(),
            "conversaciones": conversations.count(),
            "contactos": contacts.count(),
            "clientes": clients.count(),
        }

        if dry_run:
            counts["asesores de demo"] = sum(
                1 for advisor in demo_advisors if not advisor.events.exists()
            )
            self.stdout.write(self.style.NOTICE("\n[dry-run] Se borraría:"))
            for label, total in counts.items():
                self.stdout.write(f"  {label}: {total}")
            return

        with transaction.atomic():
            events.delete()
            messages.delete()
            conversations.delete()
            # Los clientes se desligan antes: el contacto muere, el cliente no
            # necesariamente, y `Client` está protegido por eventos históricos.
            contacts.update(client=None)
            contacts.delete()
            clients.delete()

            removed_advisors = 0
            for advisor in demo_advisors:
                if advisor.events.exists():
                    # Alguien le colgó eventos reales: se queda.
                    continue
                user = advisor.user
                advisor.delete()
                if not user.is_superuser and user.email.startswith(f"{SEED_TAG}."):
                    user.delete()
                removed_advisors += 1
            counts["asesores de demo"] = removed_advisors

        self.stdout.write(self.style.SUCCESS("\nBorrado:"))
        for label, total in counts.items():
            self.stdout.write(f"  {label}: {total}")
