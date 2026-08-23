"""Crea un juego de datos de prueba (5 de cada entidad) sobre una empresa.

Es idempotente: se puede ejecutar varias veces sin duplicar registros, porque
todo se busca por su clave natural (email, código de asesor, teléfono
normalizado, idempotency_key del evento).

    DJANGO_ENV=production python manage.py seed_demo --settings=config.settings.production
"""

from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.advisors.models import Advisor, AdvisorAvailability
from apps.clients.models import Client
from apps.clients.services import normalize_phone
from apps.companies.models import Company
from apps.scheduling.models import Event, SchedulingConfiguration
from apps.scheduling.services.event_actions import (
    cancel_event,
    complete_event,
    confirm_event,
    create_manual_event,
    start_event,
)

User = get_user_model()

DEFAULT_PASSWORD = "Demo1234!"

ADVISORS = [
    {"code": "ASE-001", "first_name": "Ana", "last_name": "Torres", "email": "ana.torres@demo.local", "phone": "+573001110001", "priority": 10},
    {"code": "ASE-002", "first_name": "Bruno", "last_name": "Mejía", "email": "bruno.mejia@demo.local", "phone": "+573001110002", "priority": 20},
    {"code": "ASE-003", "first_name": "Carla", "last_name": "Ríos", "email": "carla.rios@demo.local", "phone": "+573001110003", "priority": 30},
    {"code": "ASE-004", "first_name": "Diego", "last_name": "Peña", "email": "diego.pena@demo.local", "phone": "+573001110004", "priority": 40},
    {"code": "ASE-005", "first_name": "Elena", "last_name": "Vargas", "email": "elena.vargas@demo.local", "phone": "+573001110005", "priority": 50},
]

CLIENTS = [
    {"first_name": "Laura", "last_name": "Gómez", "phone": "3002220001", "email": "laura.gomez@demo.local", "source": Client.Source.CHATBOT, "channel": Client.Channel.WHATSAPP},
    {"first_name": "Mario", "last_name": "Castro", "phone": "3002220002", "email": "mario.castro@demo.local", "source": Client.Source.MANUAL, "channel": Client.Channel.PHONE},
    {"first_name": "Natalia", "last_name": "Suárez", "phone": "3002220003", "email": "natalia.suarez@demo.local", "source": Client.Source.WEBSITE, "channel": Client.Channel.EMAIL},
    {"first_name": "Óscar", "last_name": "Lozano", "phone": "3002220004", "email": "oscar.lozano@demo.local", "source": Client.Source.PHONE_CALL, "channel": Client.Channel.PHONE},
    {"first_name": "Paula", "last_name": "Restrepo", "phone": "3002220005", "email": "paula.restrepo@demo.local", "source": Client.Source.CHATBOT, "channel": Client.Channel.WHATSAPP},
]

# (offset en días hábiles desde hoy, hora, tipo, estado final deseado)
EVENTS = [
    {"key": "seed-demo-001", "day_offset": -3, "hour": 9, "type": Event.Type.PROPERTY_VISIT, "final_status": Event.Status.COMPLETED, "title": "Visita apartamento Chapinero", "location": "Cra 13 #63-45, Bogotá", "property_code": "APT-1001"},
    {"key": "seed-demo-002", "day_offset": -1, "hour": 11, "type": Event.Type.CLIENT_MEETING, "final_status": Event.Status.CANCELLED, "title": "Reunión de asesoría comercial", "location": "Oficina principal", "property_code": ""},
    {"key": "seed-demo-003", "day_offset": 1, "hour": 10, "type": Event.Type.PROPERTY_VISIT, "final_status": Event.Status.CONFIRMED, "title": "Visita casa Cedritos", "location": "Calle 140 #12-30, Bogotá", "property_code": "CAS-2002"},
    {"key": "seed-demo-004", "day_offset": 2, "hour": 14, "type": Event.Type.PHONE_CALL, "final_status": Event.Status.PENDING, "title": "Llamada de seguimiento", "location": "", "property_code": ""},
    {"key": "seed-demo-005", "day_offset": 4, "hour": 16, "type": Event.Type.PROPERTY_VISIT, "final_status": Event.Status.PENDING, "title": "Visita local comercial Usaquén", "location": "Cra 7 #117-20, Bogotá", "property_code": "LOC-3003"},
]


def shift_business_days(reference: date, offset: int) -> date:
    """Devuelve la fecha a `offset` días hábiles de `reference` (lun-vie)."""
    step = 1 if offset >= 0 else -1
    current, remaining = reference, abs(offset)
    while remaining or current.weekday() > 4:
        current += timedelta(days=step)
        if current.weekday() <= 4 and remaining:
            remaining -= 1
    return current


class Command(BaseCommand):
    help = "Crea 5 asesores, 5 clientes y 5 eventos de prueba en una empresa."

    def add_arguments(self, parser):
        parser.add_argument("--company-slug", default=None, help="Slug de la empresa destino. Por defecto, la primera empresa activa.")
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Contraseña de los usuarios asesores creados.")

    @transaction.atomic
    def handle(self, *args, **options):
        company = self._get_company(options["company_slug"])
        actor = User.objects.filter(company=company, role=User.Role.ADMIN).order_by("created_at").first()
        if actor is None:
            raise CommandError(f"La empresa '{company.slug}' no tiene ningún usuario ADMIN que actúe como autor de los datos.")

        self.stdout.write(f"Empresa: {company.name} ({company.slug})")
        self.stdout.write(f"Actor:   {actor.email}")

        configuration = self._seed_configuration(company, actor)
        advisors = self._seed_advisors(company, actor, options["password"])
        clients = self._seed_clients(company)
        self._seed_events(company, actor, advisors, clients)

        self.stdout.write(self.style.SUCCESS("\nResumen"))
        self.stdout.write(f"  configuración : {configuration.name}")
        for model in (User, Advisor, AdvisorAvailability, Client, Event):
            filters = {"company": company}
            self.stdout.write(f"  {model.__name__:<20}: {model.objects.filter(**filters).count()}")
        self.stdout.write(f"\nContraseña de los asesores: {options['password']}")

    def _get_company(self, slug):
        if slug:
            try:
                return Company.objects.get(slug=slug)
            except Company.DoesNotExist as exc:
                raise CommandError(f"No existe una empresa con slug '{slug}'.") from exc
        company = Company.objects.filter(is_active=True).order_by("created_at").first()
        if company is None:
            raise CommandError("No hay ninguna empresa activa. Crea una antes de sembrar datos.")
        return company

    def _seed_configuration(self, company, actor):
        configuration = SchedulingConfiguration.objects.filter(company=company, is_default=True, is_active=True).first()
        if configuration:
            self.stdout.write("  = configuración de agendamiento ya existente")
            return configuration
        configuration = SchedulingConfiguration.objects.create(
            company=company,
            name="Configuración demo",
            default_event_duration_minutes=60,
            minimum_advance_minutes=0,
            maximum_advance_days=90,
            assignment_strategy=SchedulingConfiguration.Strategy.LEAST_EVENTS,
            timezone=company.timezone,
            created_by=actor,
            updated_by=actor,
        )
        self.stdout.write(self.style.SUCCESS("  + configuración de agendamiento"))
        return configuration

    def _seed_advisors(self, company, actor, password):
        advisors = []
        for data in ADVISORS:
            user, created = User.objects.get_or_create(
                email=data["email"],
                defaults={
                    "company": company,
                    "first_name": data["first_name"],
                    "last_name": data["last_name"],
                    "phone": data["phone"],
                    "role": User.Role.ADVISOR,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
                self.stdout.write(self.style.SUCCESS(f"  + usuario {user.email}"))

            advisor, created = Advisor.objects.get_or_create(
                company=company,
                code=data["code"],
                defaults={
                    "user": user,
                    "phone": data["phone"],
                    "timezone": company.timezone,
                    "default_event_duration_minutes": 60,
                    "max_daily_events": 8,
                    "assignment_priority": data["priority"],
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + asesor {advisor.code}"))
            advisors.append(advisor)

            for day_of_week in range(5):  # lunes a viernes
                _, created = AdvisorAvailability.objects.get_or_create(
                    advisor=advisor,
                    day_of_week=day_of_week,
                    start_time=time(8, 0),
                    end_time=time(18, 0),
                    defaults={"company": company, "slot_duration_minutes": 60, "configured_by": actor},
                )
            self.stdout.write(f"    disponibilidad lun-vie 08:00-18:00 para {advisor.code}")
        return advisors

    def _seed_clients(self, company):
        clients = []
        for data in CLIENTS:
            client, created = Client.objects.get_or_create(
                company=company,
                normalized_phone=normalize_phone(data["phone"]),
                defaults={
                    "first_name": data["first_name"],
                    "last_name": data["last_name"],
                    "phone": data["phone"],
                    "email": data["email"],
                    "source": data["source"],
                    "preferred_contact_channel": data["channel"],
                    "notes": "Cliente de prueba creado por seed_demo.",
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + cliente {client}"))
            clients.append(client)
        return clients

    def _seed_events(self, company, actor, advisors, clients):
        today = timezone.localdate()
        for index, data in enumerate(EVENTS):
            if Event.objects.filter(company=company, idempotency_key=data["key"]).exists():
                self.stdout.write(f"  = evento {data['key']} ya existente")
                continue

            local_date = shift_business_days(today, data["day_offset"])
            start_at = timezone.make_aware(datetime.combine(local_date, time(data["hour"], 0)))
            event = create_manual_event(
                company=company,
                advisor=advisors[index],
                actor=actor,
                client=clients[index],
                event_type=data["type"],
                title=data["title"],
                description="Evento de prueba creado por seed_demo.",
                start_at=start_at,
                end_at=start_at + timedelta(minutes=60),
                timezone=company.timezone,
                location=data["location"],
                property_code=data["property_code"],
                property_title=data["title"],
                idempotency_key=data["key"],
            )

            final_status = data["final_status"]
            if final_status in {Event.Status.CONFIRMED, Event.Status.COMPLETED, Event.Status.CANCELLED}:
                if final_status == Event.Status.CANCELLED:
                    cancel_event(event=event, actor=actor, reason="El cliente reprogramará más adelante.", source="ADMIN")
                else:
                    confirm_event(event=event, actor=actor)
                    if final_status == Event.Status.COMPLETED:
                        start_event(event=event, actor=actor)
                        complete_event(event=event, actor=actor, notes="Visita realizada sin novedades.")

            self.stdout.write(self.style.SUCCESS(f"  + evento {data['key']} {start_at:%Y-%m-%d %H:%M} -> {event.status}"))
