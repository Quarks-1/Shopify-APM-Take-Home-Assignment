"""Create demo spaces and objects when the database is empty."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from api.models import Material, Object, ObjectType, SimilarItem, Space, SpaceType, Style


class Command(BaseCommand):
    help = "Seed a demo floor space with sample objects when no spaces exist."

    def handle(self, *_args, **_options):
        if Space.objects.exists():
            self.stdout.write(self.style.WARNING("Spaces already exist; leaving data unchanged."))
            return

        showroom = Space.objects.create(
            x=Decimal("24"),
            y=Decimal("16"),
            space_type=SpaceType.FLOOR,
            weight_limit=Decimal("500"),
        )
        shelf = Space.objects.create(
            x=Decimal("6"),
            y=Decimal("2"),
            z=Decimal("1.5"),
            space_type=SpaceType.SHELF,
            weight_limit=Decimal("80"),
        )

        sofa = Object.objects.create(
            x=Decimal("8"),
            y=Decimal("6"),
            weight=Decimal("90"),
            object_type=ObjectType.FURNITURE,
            material=Material.FABRIC,
            style=Style.MODERN,
            price_limit=Decimal("1200"),
        )
        sofa.spaces.add(showroom)
        SimilarItem.objects.create(
            parent_object=sofa,
            name="Cloud sectional",
            image="https://example.com/sofa.jpg",
        )

        poster = Object.objects.create(
            x=Decimal("18"),
            y=Decimal("4"),
            weight=Decimal("2"),
            object_type=ObjectType.POSTER,
            material=Material.WOOD,
            style=Style.BRUTALIST,
            price_limit=Decimal("85"),
        )
        poster.spaces.add(showroom)

        lamp = Object.objects.create(
            x=Decimal("2"),
            y=Decimal("1"),
            z=Decimal("0.5"),
            weight=Decimal("4"),
            object_type=ObjectType.FURNITURE,
            material=Material.METAL,
            style=Style.VINTAGE,
            price_limit=Decimal("240"),
        )
        lamp.spaces.add(shelf)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created demo spaces ({showroom.id}, {shelf.id}) with {Object.objects.count()} objects."
            )
        )