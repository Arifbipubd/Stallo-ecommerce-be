import secrets

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.text import slugify

from .images import menu_image_path, optimize, validate_image_size

# No 0/O/1/I so order numbers are easy to read out over the phone.
_ORDER_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_public_id() -> str:
    return "ST-" + "".join(secrets.choice(_ORDER_ID_ALPHABET) for _ in range(6))


def make_unique_slug(instance, source: str, fallback: str) -> str:
    """
    Slug from `source` (e.g. the name), made unique within the model: "wings", "wings-2", "wings-3"…
    Only used when an object is first created; an existing slug never changes, because the
    storefront cart and past orders refer to it.
    """
    model = type(instance)
    base = (slugify(source) or fallback)[:50].strip("-") or fallback
    slug, n = base, 2
    while model.objects.filter(slug=slug).exclude(pk=instance.pk).exists():
        suffix = f"-{n}"
        slug = f"{base[: 50 - len(suffix)]}{suffix}"
        n += 1
    return slug


class Category(models.Model):
    """A menu section (Burgers, Combos…). Manage these in the admin panel."""

    slug = models.SlugField(unique=True, blank=True, editable=False, help_text="Made from the name automatically.")
    name = models.CharField(max_length=80, unique=True)
    sort_order = models.PositiveIntegerField(default=0, help_text="Smaller numbers show first.")
    is_active = models.BooleanField(default=True, help_text="Untick to hide the category and all its items.")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = make_unique_slug(self, self.name, "category")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    # Same id the storefront uses (e.g. "bucket-26"), so cart lines map 1:1.
    # Generated from the name when a product is created; never changed afterwards.
    slug = models.SlugField(unique=True, blank=True, editable=False, help_text="Made from the name automatically.")
    name = models.CharField(max_length=120)
    price = models.PositiveIntegerField(help_text="Price in BDT (whole taka).")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    blurb = models.CharField(max_length=300, blank=True)
    image = models.ImageField(
        upload_to=menu_image_path,
        validators=[validate_image_size],
        help_text="Required. Any photo up to 8 MB: it is resized and stored for you.",
    )
    featured = models.BooleanField(default=False)
    is_available = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = make_unique_slug(self, self.name, "item")
        old_image = ""
        if self.pk:
            old_image = MenuItem.objects.filter(pk=self.pk).values_list("image", flat=True).first() or ""
        if self.image and not self.image._committed:  # a new upload: shrink it before it is stored
            self.image = optimize(self.image)
        super().save(*args, **kwargs)
        if old_image and old_image != (self.image.name or ""):  # replaced or removed: don't leave the old file behind
            self.image.storage.delete(old_image)

    @property
    def offer_price(self):
        """
        The lower price while this item is one of today's active offers, else None.
        An offer price that is not below the regular price (say the menu price was cut later) is ignored.
        """
        try:
            offer = self.offer
        except ObjectDoesNotExist:
            return None
        if offer.is_active and offer.offer_price is not None and offer.offer_price < self.price:
            return offer.offer_price
        return None

    @property
    def current_price(self) -> int:
        """What a customer pays per piece right now: the offer price if there is one, else the menu price."""
        offer_price = self.offer_price
        return self.price if offer_price is None else offer_price

    def __str__(self):
        return f"{self.name} (৳{self.price})"


@receiver(post_delete, sender=MenuItem)
def _delete_picture_with_item(sender, instance, **kwargs):
    if instance.image:
        instance.image.storage.delete(instance.image.name)


class TodaysOffer(models.Model):
    """
    A menu item the admin has picked for "Today's offers". The item itself (name, price,
    category) is still managed under Menu items: this only says which ones to show.
    """

    menu_item = models.OneToOneField(MenuItem, on_delete=models.CASCADE, related_name="offer")
    # Empty means "listed as an offer but no special price": it sells at the regular price.
    offer_price = models.PositiveIntegerField(
        null=True, blank=False, help_text="What the customer pays today (৳). Must be lower than the regular price."
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Smaller numbers show first.")
    is_active = models.BooleanField(default=True, help_text="Untick to hide it today without removing it.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "today's offer"
        verbose_name_plural = "today's offers"

    def clean(self):
        super().clean()
        item = getattr(self, "menu_item", None) if self.menu_item_id else None
        if item is not None and self.offer_price is not None and self.offer_price >= item.price:
            raise ValidationError(
                {"offer_price": f"The offer price must be lower than the regular price (৳{item.price})."}
            )

    def __str__(self):
        return self.menu_item.name


class AddOn(models.Model):
    """
    A menu item the admin offers as an extra ("Make it a meal") when a customer adds something to
    the cart. The item itself (name, price, category) is still managed under Menu items: this only
    says which ones to suggest, and in what order. An extra is sold at the item's current price,
    so an active Today's offer on it applies here too.
    """

    menu_item = models.OneToOneField(MenuItem, on_delete=models.CASCADE, related_name="addon")
    sort_order = models.PositiveIntegerField(default=0, help_text="Smaller numbers show first.")
    is_active = models.BooleanField(default=True, help_text="Untick to stop suggesting it without removing it.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "add-on"
        verbose_name_plural = "add-ons"

    def __str__(self):
        return self.menu_item.name


class Outlet(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=300)
    phone = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class Customer(models.Model):
    """
    A customer account. Log in with phone number + password (the phone is also the Django
    username, so passwords are hashed and managed by django.contrib.auth).
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer")
    phone = models.CharField(max_length=11, unique=True, help_text="Normalised 01XXXXXXXXX.")
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} · {self.phone}"


class Address(models.Model):
    """A saved delivery address (Home, Office…). One per customer is the default."""

    MAX_PER_CUSTOMER = 2

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=30, default="Home")
    address = models.TextField()
    landmark = models.CharField(max_length=200, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        verbose_name_plural = "addresses"

    def __str__(self):
        return f"{self.label}: {self.address[:40]}"


class Order(models.Model):
    class Mode(models.TextChoices):
        DELIVERY = "delivery", "Delivery"
        TAKEAWAY = "takeaway", "Takeaway"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending (needs confirmation call)"
        CONFIRMED = "confirmed", "Confirmed"
        PREPARING = "preparing", "Preparing"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Payment(models.TextChoices):
        COD = "cod", "Cash on delivery"

    public_id = models.CharField(max_length=12, unique=True, editable=False)
    mode = models.CharField(max_length=10, choices=Mode.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_method = models.CharField(max_length=10, choices=Payment.choices, default=Payment.COD)

    # Set when the customer was logged in; guest orders leave this empty.
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    customer_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    landmark = models.CharField(max_length=200, blank=True)
    outlet = models.ForeignKey(Outlet, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    notes = models.TextField(blank=True)
    # Takeaway only. Empty means "as soon as possible".
    pickup_time = models.DateTimeField(null=True, blank=True, help_text="When the customer will collect (takeaway). Empty = as soon as possible.")

    subtotal = models.PositiveIntegerField(help_text="Items total, before delivery.")
    delivery_fee = models.PositiveIntegerField(default=0, help_text="Flat delivery charge. 0 for takeaway.")
    total = models.PositiveIntegerField(help_text="Subtotal + delivery fee: what the customer pays in cash.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            for _ in range(10):
                candidate = _new_public_id()
                if not Order.objects.filter(public_id=candidate).exists():
                    self.public_id = candidate
                    break
            else:  # pragma: no cover - astronomically unlikely
                raise RuntimeError("Could not generate a unique order id")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.public_id} · {self.customer_name} · ৳{self.total}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="+")
    # Snapshot so old orders keep their name/price if the menu changes later.
    name = models.CharField(max_length=120)
    unit_price = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()

    @property
    def line_total(self) -> int:
        return self.unit_price * self.quantity

    def __str__(self):
        return f"{self.quantity} × {self.name}"
