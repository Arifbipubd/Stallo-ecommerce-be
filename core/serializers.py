import re

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from . import hours
from .models import Address, Category, Customer, MenuItem, Order, OrderItem, Outlet

_PHONE_RE = re.compile(r"^(?:\+?88)?(01[3-9]\d{8})$")

def normalize_phone(value: str) -> str:
    match = _PHONE_RE.match(re.sub(r"[\s-]", "", value))
    if not match:
        raise serializers.ValidationError("Enter a valid Bangladeshi mobile number, e.g. 01719 517 242.")
    return match.group(1)


MAX_LINES = 30
MAX_QTY = 50


class CategorySerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="slug")

    class Meta:
        model = Category
        fields = ["id", "name"]


class MenuItemSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="slug")
    # the category's id (slug); its display name is available from /api/categories/
    category = serializers.CharField(source="category.slug")
    category_name = serializers.CharField(source="category.name")
    # price is the regular price; offer_price is the lower price today when it is one of today's offers
    # (otherwise null). Orders are charged offer_price when there is one.
    offer_price = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = MenuItem
        fields = ["id", "name", "price", "offer_price", "category", "category_name", "blurb", "featured"]


class PickupSlotSerializer(serializers.Serializer):
    """Documentation only: one bookable pickup time."""

    value = serializers.CharField(help_text="Send this as `pickup_time`.")
    label = serializers.CharField(help_text="For display, e.g. 3:45 pm.")


class PickupDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    label = serializers.CharField(help_text="Today or Tomorrow.")
    slots = PickupSlotSerializer(many=True)


class PickupSlotsSerializer(serializers.Serializer):
    open_now = serializers.BooleanField(help_text="False when the outlets are closed, so 'as soon as possible' is not on offer.")
    lead_minutes = serializers.IntegerField(help_text="Earliest pickup is this many minutes from now.")
    days = PickupDaySerializer(many=True)


class OutletSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="slug")

    class Meta:
        model = Outlet
        fields = ["id", "name", "address", "phone"]


class OrderLineInputSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=60)
    qty = serializers.IntegerField(min_value=1, max_value=MAX_QTY)


class OrderCreateSerializer(serializers.Serializer):
    """
    Validates a checkout payload and creates the order.

    Prices are never taken from the client: every line is priced from the menu in the
    database at the moment the order is placed.
    """

    mode = serializers.ChoiceField(choices=Order.Mode.choices)
    name = serializers.CharField(min_length=2, max_length=120)
    phone = serializers.CharField(max_length=20)
    address = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")
    landmark = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    outlet = serializers.CharField(required=False, allow_blank=True, max_length=60, default="")
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")
    pickup_time = serializers.DateTimeField(
        required=False, allow_null=True, default=None,
        help_text="Takeaway only: when you'll collect (ISO 8601). Leave out for as soon as possible. "
        "Get the choices from GET /api/pickup-slots/.",
    )
    items = OrderLineInputSerializer(many=True, allow_empty=False)

    def validate_phone(self, value):
        return normalize_phone(value)

    def validate_items(self, value):
        merged: dict[str, int] = {}
        for line in value:
            merged[line["id"]] = merged.get(line["id"], 0) + line["qty"]
        if len(merged) > MAX_LINES:
            raise serializers.ValidationError(f"An order can have at most {MAX_LINES} different items.")
        if any(qty > MAX_QTY for qty in merged.values()):
            raise serializers.ValidationError(f"You can order at most {MAX_QTY} of one item.")

        found = {
            m.slug: m for m in MenuItem.objects.select_related("category", "offer").filter(slug__in=merged.keys())
        }
        missing = [slug for slug in merged if slug not in found]
        if missing:
            raise serializers.ValidationError(f"Unknown menu item: {', '.join(missing)}.")
        unavailable = [
            found[slug].name for slug in merged if not (found[slug].is_available and found[slug].category.is_active)
        ]
        if unavailable:
            raise serializers.ValidationError(f"Sorry, not available right now: {', '.join(unavailable)}.")
        return [{"menu_item": found[slug], "qty": qty} for slug, qty in merged.items()]

    def validate(self, attrs):
        mode = attrs["mode"]
        if mode == Order.Mode.DELIVERY:
            if len(attrs.get("address", "").strip()) < 8:
                raise serializers.ValidationError({"address": "Add your full delivery address."})
            attrs["outlet"] = None
            attrs["pickup_time"] = None  # only meaningful for takeaway
        else:
            slug = attrs.get("outlet", "")
            outlet = Outlet.objects.filter(slug=slug, is_active=True).first() if slug else None
            if outlet is None:
                raise serializers.ValidationError({"outlet": "Choose the outlet you'll collect from."})
            attrs["outlet"] = outlet
            attrs["address"] = ""
            attrs["landmark"] = ""
            if attrs.get("pickup_time"):
                problem = hours.check_pickup_time(attrs["pickup_time"])
                if problem:
                    raise serializers.ValidationError({"pickup_time": problem})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data["items"]
        request = self.context.get("request")
        user = getattr(request, "user", None)
        customer = getattr(user, "customer", None) if user and user.is_authenticated else None
        # each piece costs its offer price while it is one of today's offers, else the menu price
        subtotal = sum(l["menu_item"].current_price * l["qty"] for l in lines)
        # delivery orders pay a flat fee; takeaway is free. Decided here, never by the client.
        delivery_fee = settings.DELIVERY_FEE if validated_data["mode"] == Order.Mode.DELIVERY else 0
        order = Order.objects.create(
            mode=validated_data["mode"],
            customer_name=validated_data["name"].strip(),
            phone=validated_data["phone"],
            address=validated_data["address"].strip(),
            landmark=validated_data["landmark"].strip(),
            outlet=validated_data["outlet"],
            notes=validated_data["notes"].strip(),
            pickup_time=validated_data.get("pickup_time"),
            payment_method=Order.Payment.COD,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            total=subtotal + delivery_fee,
            customer=customer,
        )
        if customer and order.mode == Order.Mode.DELIVERY and not customer.addresses.exists():
            # First delivery order: remember the address (as their default) for next time.
            Address.objects.create(
                customer=customer, label="Home", address=order.address, landmark=order.landmark, is_default=True
            )
        OrderItem.objects.bulk_create(
            OrderItem(
                order=order,
                menu_item=l["menu_item"],
                name=l["menu_item"].name,
                unit_price=l["menu_item"].current_price,
                quantity=l["qty"],
            )
            for l in lines
        )
        return order


class ReceiptLineSerializer(serializers.Serializer):
    """Shape of one line in the receipt (documentation only)."""

    id = serializers.CharField()
    name = serializers.CharField()
    qty = serializers.IntegerField()
    unit_price = serializers.IntegerField()
    line_total = serializers.IntegerField()


class OrderReceiptSerializer(serializers.ModelSerializer):
    """What the customer gets back after placing an order."""

    order_id = serializers.CharField(source="public_id")
    outlet = serializers.CharField(source="outlet.name", default=None)
    items = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_id", "status", "mode", "payment_method", "outlet", "pickup_time", "subtotal", "delivery_fee", "total", "items",
        ]

    @extend_schema_field(ReceiptLineSerializer(many=True))
    def get_items(self, order):
        return [
            {
                "id": i.menu_item.slug,
                "name": i.name,
                "qty": i.quantity,
                "unit_price": i.unit_price,
                "line_total": i.line_total,
            }
            for i in order.items.all()
        ]


class OrderHistorySerializer(OrderReceiptSerializer):
    """A past order in the customer's account."""

    class Meta(OrderReceiptSerializer.Meta):
        fields = OrderReceiptSerializer.Meta.fields + ["created_at", "address", "landmark", "notes"]


# ---- Customer accounts -------------------------------------------------------------------

MIN_PASSWORD = 6  # short on purpose: our customers are often not comfortable with long passwords


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=2, max_length=120)
    phone = serializers.CharField(max_length=20)
    password = serializers.CharField(
        min_length=MIN_PASSWORD, max_length=128, write_only=True, trim_whitespace=False,
        error_messages={"min_length": f"Use at least {MIN_PASSWORD} characters."},
    )

    def validate_phone(self, value):
        phone = normalize_phone(value)
        if get_user_model().objects.filter(username=phone).exists():
            raise serializers.ValidationError("An account with this number already exists. Please log in.")
        return phone

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_model().objects.create_user(
            username=validated_data["phone"],
            password=validated_data["password"],
            first_name=validated_data["name"].strip()[:150],
        )
        return Customer.objects.create(user=user, phone=validated_data["phone"], name=validated_data["name"].strip())


class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    def validate_phone(self, value):
        return normalize_phone(value)

    def validate(self, attrs):
        user = authenticate(username=attrs["phone"], password=attrs["password"])
        customer = getattr(user, "customer", None) if user else None
        if customer is None:
            # Same message for "no such number" and "wrong password".
            raise serializers.ValidationError({"password": "Phone number or password is not right."})
        attrs["customer"] = customer
        return attrs


class CustomerSerializer(serializers.ModelSerializer):
    orders_count = serializers.IntegerField(source="orders.count", read_only=True)

    class Meta:
        model = Customer
        fields = ["name", "phone", "orders_count", "created_at"]
        read_only_fields = ["phone", "created_at"]
        extra_kwargs = {"name": {"min_length": 2}}


class AddressSerializer(serializers.ModelSerializer):
    label = serializers.CharField(max_length=30, required=False, default="Home")
    address = serializers.CharField(min_length=8, max_length=500, error_messages={
        "min_length": "Add your full delivery address.",
        "blank": "Add your full delivery address.",
    })
    landmark = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    class Meta:
        model = Address
        fields = ["id", "label", "address", "landmark", "is_default"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        customer = self.context["request"].user.customer
        if self.instance is None and customer.addresses.count() >= Address.MAX_PER_CUSTOMER:
            raise serializers.ValidationError(
                f"You can save up to {Address.MAX_PER_CUSTOMER} addresses. Please remove one first."
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        customer = self.context["request"].user.customer
        # the first address is always the default
        make_default = validated_data.get("is_default") or not customer.addresses.exists()
        if make_default:
            customer.addresses.update(is_default=False)
        validated_data["is_default"] = make_default
        return Address.objects.create(customer=customer, **validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        if validated_data.get("is_default"):
            instance.customer.addresses.exclude(pk=instance.pk).update(is_default=False)
        elif validated_data.get("is_default") is False and instance.is_default:
            # can't un-default the only default; pick another via "make default"
            validated_data["is_default"] = True
        return super().update(instance, validated_data)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(
        min_length=MIN_PASSWORD, max_length=128, write_only=True, trim_whitespace=False,
        error_messages={"min_length": f"Use at least {MIN_PASSWORD} characters."},
    )

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("That isn't your current password.")
        return value


class AuthResponseSerializer(serializers.Serializer):
    """Documentation only: what register/login return."""

    token = serializers.CharField()
    customer = CustomerSerializer()
