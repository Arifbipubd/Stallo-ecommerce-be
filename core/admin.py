from django.contrib import admin

from django.db.models import Count

from .models import Address, Category, Customer, MenuItem, Order, OrderItem, Outlet


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


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "is_available", "featured", "sort_order")
    list_editable = ("price", "is_available", "featured", "sort_order")
    list_filter = ("category", "is_available", "featured")
    list_select_related = ("category",)
    search_fields = ("name", "slug")
    fields = ("name", "category", "price", "blurb", "is_available", "featured", "sort_order", "slug")
    readonly_fields = ("slug",)  # generated from the name on creation, never changed afterwards


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
    list_display = ("public_id", "customer_name", "phone", "account", "mode", "total", "payment_method", "status", "created_at")
    list_filter = ("status", "mode", "payment_method", "created_at")
    search_fields = ("public_id", "customer_name", "phone")
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]
    readonly_fields = ("public_id", "customer", "mode", "payment_method", "subtotal", "total", "created_at", "updated_at")
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
