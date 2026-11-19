from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("menu/", views.MenuListView.as_view(), name="menu"),
    path("offers/today/", views.TodaysOffersView.as_view(), name="offers-today"),
    path("addons/", views.AddOnsView.as_view(), name="addons"),
    path("categories/", views.CategoryListView.as_view(), name="categories"),
    path("outlets/", views.OutletListView.as_view(), name="outlets"),
    path("pickup-slots/", views.pickup_slots, name="pickup-slots"),
    path("orders/", views.OrderCreateView.as_view(), name="order-create"),
    path("orders/mine/", views.MyOrdersView.as_view(), name="orders-mine"),
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/password/", views.ChangePasswordView.as_view(), name="auth-password"),
    path("auth/addresses/", views.AddressListCreateView.as_view(), name="addresses"),
    path("auth/addresses/<int:pk>/", views.AddressDetailView.as_view(), name="address-detail"),
]
