from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .auth import SoftTokenAuthentication
from .models import Address, Category, MenuItem, Order, Outlet
from .serializers import (
    AddressSerializer,
    AuthResponseSerializer,
    CategorySerializer,
    ChangePasswordSerializer,
    CustomerSerializer,
    LoginSerializer,
    OrderHistorySerializer,
    RegisterSerializer,
    MenuItemSerializer,
    OrderCreateSerializer,
    OrderReceiptSerializer,
    OutletSerializer,
)


@extend_schema(
    tags=["System"],
    responses=inline_serializer(
        "Health",
        {"status": serializers.CharField(), "service": serializers.CharField()},
    ),
)
@api_view(["GET"])
def health(request):
    """Simple liveness check the storefront (or a monitor) can call."""
    return Response({"status": "ok", "service": "stallo-backend"})


@extend_schema(tags=["Menu"], summary="List orderable menu items")
class MenuListView(generics.ListAPIView):
    """All menu items that can currently be ordered."""

    queryset = MenuItem.objects.filter(is_available=True, category__is_active=True).select_related("category")
    serializer_class = MenuItemSerializer
    pagination_class = None


@extend_schema(tags=["Menu"], summary="List menu categories (in display order)")
class CategoryListView(generics.ListAPIView):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    pagination_class = None


@extend_schema(tags=["Outlets"], summary="List active outlets")
class OutletListView(generics.ListAPIView):
    queryset = Outlet.objects.filter(is_active=True)
    serializer_class = OutletSerializer
    pagination_class = None


@extend_schema(
    tags=["Orders"],
    summary="Place a cash-on-delivery order",
    description=(
        "Prices come from the menu in the database. Delivery needs an address; "
        "takeaway needs an outlet id. Errors are 400 with {field: [messages]}; "
        "too many orders from one IP gives 429."
    ),
    request=OrderCreateSerializer,
    auth=[{"tokenAuth": []}, {}],  # login is optional
    responses={
        201: OrderReceiptSerializer,
        400: inline_serializer("OrderValidationError", {"field": serializers.ListField(child=serializers.CharField())}),
        429: None,
    },
    examples=[
        OpenApiExample(
            "Delivery",
            request_only=True,
            value={
                "mode": "delivery",
                "name": "Bipu Das",
                "phone": "01719 517 242",
                "address": "House 12, Road 5, Dhanmondi, Dhaka",
                "landmark": "Near the mosque",
                "notes": "Extra spicy please",
                "items": [{"id": "garlic-wings", "qty": 2}],
            },
        ),
        OpenApiExample(
            "Takeaway",
            request_only=True,
            value={
                "mode": "takeaway",
                "name": "Bipu Das",
                "phone": "+8801719517242",
                "outlet": "mirpur",
                "items": [{"id": "garlic-wings", "qty": 1}],
            },
        ),
    ],
)
class OrderCreateView(APIView):
    """Place a cash-on-delivery order. Anonymous, rate limited per IP."""

    # Optional login: if a valid token is sent the order is linked to that customer's account.
    authentication_classes = [SoftTokenAuthentication]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "orders"

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(OrderReceiptSerializer(order).data, status=status.HTTP_201_CREATED)


def _auth_payload(customer):
    token, _ = Token.objects.get_or_create(user=customer.user)
    return {"token": token.key, "customer": CustomerSerializer(customer).data}


@extend_schema(
    tags=["Account"],
    summary="Create a customer account",
    description="Sign up with name, phone number and password. Returns a token to send as `Authorization: Token <key>`.",
    request=RegisterSerializer,
    responses={201: AuthResponseSerializer},
)
class RegisterView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        return Response(_auth_payload(customer), status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Account"],
    summary="Log in with phone number and password",
    request=LoginSerializer,
    responses={200: AuthResponseSerializer},
)
class LoginView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(_auth_payload(serializer.validated_data["customer"]))


@extend_schema(tags=["Account"], summary="Log out (invalidates the token)", request=None, responses={204: None})
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Account"])
class MeView(generics.RetrieveUpdateAPIView):
    """Your profile and saved delivery address."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CustomerSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user.customer


@extend_schema(tags=["Account"], summary="My past orders (newest first)")
class MyOrdersView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OrderHistorySerializer
    pagination_class = None

    def get_queryset(self):
        return Order.objects.filter(customer=self.request.user.customer).prefetch_related("items").select_related("outlet")[:50]


@extend_schema(tags=["Account"], summary="Change password", request=ChangePasswordSerializer, responses={204: None})
class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class _MyAddresses:
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AddressSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return Address.objects.filter(customer=self.request.user.customer)


@extend_schema(tags=["Account"])
class AddressListCreateView(_MyAddresses, generics.ListCreateAPIView):
    """My saved delivery addresses (default first). The first one you save becomes the default."""


@extend_schema(tags=["Account"])
class AddressDetailView(_MyAddresses, generics.RetrieveUpdateDestroyAPIView):
    """Edit, set as default (`is_default: true`) or delete a saved address."""

    http_method_names = ["get", "patch", "delete", "head", "options"]

    def perform_destroy(self, instance):
        was_default = instance.is_default
        customer = instance.customer
        instance.delete()
        if was_default:  # hand the default to the newest remaining address
            nxt = customer.addresses.order_by("-created_at").first()
            if nxt:
                nxt.is_default = True
                nxt.save(update_fields=["is_default"])
