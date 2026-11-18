import json
from pathlib import Path

from django.core.management.base import BaseCommand

from core.models import Category, MenuItem, Outlet

# Names for the category ids used in menu.json. Existing categories keep any name/order edited in admin.
CATEGORY_NAMES = {
    "special": "Specials",
    "combo": "Combos",
    "single": "Wings & bites",
    "burger": "Burgers",
    "sides": "Sides",
}

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "menu.json"


class Command(BaseCommand):
    help = "Load the Stallo menu and outlets from core/fixtures/menu.json (safe to re-run)."

    def handle(self, *args, **options):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))

        categories = {}
        for order, (slug, name) in enumerate(CATEGORY_NAMES.items()):
            categories[slug], _ = Category.objects.get_or_create(slug=slug, defaults={"name": name, "sort_order": order})

        for order, item in enumerate(data["items"]):
            MenuItem.objects.update_or_create(
                slug=item["id"],
                defaults={
                    "name": item["name"],
                    "price": item["price"],
                    "category": categories[item["category"]],
                    "blurb": item.get("blurb", ""),
                    "featured": item.get("featured", False),
                    "sort_order": order,
                },
            )
        for order, outlet in enumerate(data["outlets"]):
            Outlet.objects.update_or_create(
                slug=outlet["id"],
                defaults={
                    "name": outlet["name"],
                    "address": outlet["address"],
                    "phone": outlet["phone"],
                    "sort_order": order,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(data['items'])} menu items and {len(data['outlets'])} outlets."))
