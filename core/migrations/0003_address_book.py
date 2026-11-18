import django.db.models.deletion
from django.db import migrations, models


def move_single_address(apps, schema_editor):
    """The old Customer.address/landmark becomes the customer's default 'Home' address."""
    Customer = apps.get_model("core", "Customer")
    Address = apps.get_model("core", "Address")
    for c in Customer.objects.exclude(address=""):
        Address.objects.create(customer=c, label="Home", address=c.address, landmark=c.landmark, is_default=True)


def restore_single_address(apps, schema_editor):
    Customer = apps.get_model("core", "Customer")
    for c in Customer.objects.all():
        a = c.addresses.order_by("-is_default", "-created_at").first()
        if a:
            c.address, c.landmark = a.address, a.landmark
            c.save(update_fields=["address", "landmark"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_customers"),
    ]

    operations = [
        migrations.CreateModel(
            name="Address",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(default="Home", max_length=30)),
                ("address", models.TextField()),
                ("landmark", models.CharField(blank=True, max_length=200)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="addresses", to="core.customer"
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "addresses",
                "ordering": ["-is_default", "-created_at"],
            },
        ),
        migrations.RunPython(move_single_address, restore_single_address),
        migrations.RemoveField(model_name="customer", name="address"),
        migrations.RemoveField(model_name="customer", name="landmark"),
    ]
