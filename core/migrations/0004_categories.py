import django.db.models.deletion
from django.db import migrations, models

# The five fixed sections the menu used before categories became editable.
OLD_CATEGORIES = [
    ("special", "Specials"),
    ("combo", "Combos"),
    ("single", "Wings & bites"),
    ("burger", "Burgers"),
    ("sides", "Sides"),
]


def create_categories(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    for order, (slug, name) in enumerate(OLD_CATEGORIES):
        Category.objects.get_or_create(slug=slug, defaults={"name": name, "sort_order": order})


def link_items(apps, schema_editor):
    """Point every existing product at the Category row matching its old text value."""
    Category = apps.get_model("core", "Category")
    MenuItem = apps.get_model("core", "MenuItem")
    for item in MenuItem.objects.all():
        category = Category.objects.filter(slug=item.category).first()
        if category is None:  # unknown old value: keep the product, give it its own category
            category = Category.objects.create(slug=item.category, name=item.category.title())
        item.category_fk = category
        item.save(update_fields=["category_fk"])


def unlink_items(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    for item in MenuItem.objects.select_related("category_fk"):
        item.category = item.category_fk.slug[:20]
        item.save(update_fields=["category"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_address_book"),
    ]

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "slug",
                    models.SlugField(blank=True, editable=False, help_text="Made from the name automatically.", unique=True),
                ),
                ("name", models.CharField(max_length=80, unique=True)),
                ("sort_order", models.PositiveIntegerField(default=0, help_text="Smaller numbers show first.")),
                (
                    "is_active",
                    models.BooleanField(default=True, help_text="Untick to hide the category and all its items."),
                ),
            ],
            options={"verbose_name_plural": "categories", "ordering": ["sort_order", "id"]},
        ),
        migrations.RunPython(create_categories, migrations.RunPython.noop),
        migrations.AddField(
            model_name="menuitem",
            name="category_fk",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="items", to="core.category"
            ),
        ),
        migrations.RunPython(link_items, unlink_items),
        migrations.RemoveField(model_name="menuitem", name="category"),
        migrations.RenameField(model_name="menuitem", old_name="category_fk", new_name="category"),
        migrations.AlterField(
            model_name="menuitem",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="items", to="core.category"
            ),
        ),
        migrations.AlterField(
            model_name="menuitem",
            name="slug",
            field=models.SlugField(blank=True, editable=False, help_text="Made from the name automatically.", unique=True),
        ),
    ]
