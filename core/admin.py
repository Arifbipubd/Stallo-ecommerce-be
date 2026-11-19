from django.contrib import admin
from django.utils.html import format_html

from django.db.models import Count

from .models import AddOn, Address, Category, Customer, MenuItem, Order, OrderItem, Outlet, TodaysOffer


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "item_count", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("name",)
    fields = ("name", "slug", "sort_order", "is_active")
    readonly_fields = ("slug",)  # generated from the name on creation

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_items=Count("items"))

    @admin.display(description="Products", ordering="_items")
    def item_count(self, obj):
        return obj._items


def _thumb(item, height=44):
    if not item.image:
        return "-"
    return format_html(
        '<img src="{}" alt="" style="height:{}px;width:{}px;object-fit:cover;border-radius:8px">',
        item.image.url,
        height,
        round(height * 1.25),
    )


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("thumb", "name", "category", "price", "is_available", "featured", "sort_order")
    list_display_links = ("thumb", "name")
    list_editable = ("price", "is_available", "featured", "sort_order")
    list_filter = ("category", "is_available", "featured")
    list_select_related = ("category",)
    search_fields = ("name", "slug")
    fields = ("name", "category", "price", "blurb", "image", "preview", "is_available", "featured", "sort_order", "slug")
    readonly_fields = ("slug", "preview")  # slug: generated from the name on creation, never changed afterwards

    @admin.display(description="Picture")
    def thumb(self, obj):
        return _thumb(obj)

    @admin.display(description="Current picture")
    def preview(self, obj):
        return _thumb(obj, height=160)


@admin.register(TodaysOffer)
class TodaysOfferAdmin(admin.ModelAdmin):
    """Pick which menu items appear under "Today's offers"."""

    list_display = ("menu_item", "price", "offer_price", "discount", "category", "sort_order", "is_active", "available")
    list_editable = ("offer_price", "sort_order", "is_active")
    fields = ("menu_item", "offer_price", "sort_order", "is_active")
    list_select_related = ("menu_item", "menu_item__category")
    autocomplete_fields = ("menu_item",)  # search the whole menu by name

    @admin.display(description="Regular price (৳)", ordering="menu_item__price")
    def price(self, obj):
        return obj.menu_item.price

    @admin.display(description="You save")
    def discount(self, obj):
        item = obj.menu_item
        if obj.offer_price is None or obj.offer_price >= item.price:
            return "-"
        return f"৳{item.price - obj.offer_price} ({round(100 * (item.price - obj.offer_price) / item.price)}% off)"

    @admin.display(description="Category", ordering="menu_item__category__name")
    def category(self, obj):
        return obj.menu_item.category

    @admin.display(description="On the menu", boolean=True)
    def available(self, obj):
        # an offer on an item that is switched off (or in a hidden category) is not shown to customers
        return obj.menu_item.is_available and obj.menu_item.category.is_active


@admin.register(AddOn)
class AddOnAdmin(admin.ModelAdmin):
    """Pick which menu items are suggested as extras when something is added to the cart."""

    list_display = ("menu_item", "price", "category", "sort_order", "is_active", "available")
    list_editable = ("sort_order", "is_active")
    fields = ("menu_item", "sort_order", "is_active")
    list_select_related = ("menu_item", "menu_item__category")
    autocomplete_fields = ("menu_item",)  # search the whole menu by name

    @admin.display(description="Price (৳)", ordering="menu_item__price")
    def price(self, obj):
        return obj.menu_item.price

    @admin.display(description="Category", ordering="menu_item__category__name")
    def category(self, obj):
        return obj.menu_item.category

    @admin.display(description="On the menu", boolean=True)
    def available(self, obj):
        # an extra that is switched off (or in a hidden category) is not shown to customers
        return obj.menu_item.is_available and obj.menu_item.category.is_active


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")


class AddressInline(admin.TabularInline):
    model = Address
    extra = 0
    fields = ("label", "address", "landmark", "is_default")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "order_count", "created_at")
    search_fields = ("name", "phone")
    readonly_fields = ("user", "phone", "created_at")
    inlines = [AddressInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_orders=Count("orders"))

    @admin.display(description="Orders", ordering="_orders")
    def order_count(self, obj):
        return obj._orders

    def has_add_permission(self, request):
        return False  # customers sign up on the website


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("menu_item", "name", "unit_price", "quantity", "line_total")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("public_id", "customer_name", "phone", "account", "mode", "pickup_time", "total", "payment_method", "status", "created_at")
    list_filter = ("status", "mode", "payment_method", "created_at")
    search_fields = ("public_id", "customer_name", "phone")
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]
    readonly_fields = ("public_id", "customer", "mode", "payment_method", "subtotal", "delivery_fee", "total", "created_at", "updated_at")
    actions = ["mark_confirmed", "mark_preparing", "mark_out_for_delivery", "mark_completed", "mark_cancelled"]

    @admin.display(description="Account", boolean=True)
    def account(self, obj):
        return obj.customer_id is not None

    def _set(self, request, queryset, status):
        updated = queryset.update(status=status)
        self.message_user(request, f"{updated} order(s) updated.")

    @admin.action(description="Mark as confirmed")
    def mark_confirmed(self, request, queryset):
        self._set(request, queryset, Order.Status.CONFIRMED)

    @admin.action(description="Mark as preparing")
    def mark_preparing(self, request, queryset):
        self._set(request, queryset, Order.Status.PREPARING)

    @admin.action(description="Mark as out for delivery")
    def mark_out_for_delivery(self, request, queryset):
        self._set(request, queryset, Order.Status.OUT_FOR_DELIVERY)

    @admin.action(description="Mark as completed")
    def mark_completed(self, request, queryset):
        self._set(request, queryset, Order.Status.COMPLETED)

    @admin.action(description="Mark as cancelled")
    def mark_cancelled(self, request, queryset):
        self._set(request, queryset, Order.Status.CANCELLED)
