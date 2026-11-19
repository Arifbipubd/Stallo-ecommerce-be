from unittest.mock import patch

from django.core.cache import cache
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from .models import Category, Customer, MenuItem, Order


class ApiTestBase(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limiting between tests
        call_command("seed_menu", verbosity=0)

    def delivery_payload(self, **overrides):
        payload = {
            "mode": "delivery",
            "name": "Bipu Das",
            "phone": "01719 517 242",
            "address": "House 12, Road 5, Dhanmondi, Dhaka",
            "landmark": "Near the mosque",
            "notes": "Extra spicy",
            "items": [{"id": "garlic-wings", "qty": 2}, {"id": "fries", "qty": 1}],
        }
        payload.update(overrides)
        return payload


class PublicEndpointTests(ApiTestBase):
    def test_health(self):
        res = self.client.get(reverse("health"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_menu_lists_available_items_only(self):
        MenuItem.objects.filter(slug="fries").update(is_available=False)
        res = self.client.get(reverse("menu"))
        self.assertEqual(res.status_code, 200)
        ids = [i["id"] for i in res.json()]
        self.assertIn("garlic-wings", ids)
        self.assertNotIn("fries", ids)
        self.assertEqual(len(ids), 23)

    def test_outlets(self):
        res = self.client.get(reverse("outlets"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 8)


class CreateOrderTests(ApiTestBase):
    url = "/api/orders/"

    def test_delivery_order_is_created_cash_on_delivery(self):
        res = self.client.post(self.url, self.delivery_payload(), format="json")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body["order_id"].startswith("ST-"))
        self.assertEqual(body["payment_method"], "cod")
        self.assertEqual(body["status"], "pending")
        # 2 × 299 + 1 × 160 = 758, plus the ৳80 delivery charge
        self.assertEqual((body["subtotal"], body["delivery_fee"], body["total"]), (758, 80, 838))

        order = Order.objects.get(public_id=body["order_id"])
        self.assertEqual(order.phone, "01719517242")
        self.assertEqual(order.items.count(), 2)

    def test_prices_come_from_the_server_not_the_client(self):
        payload = self.delivery_payload(items=[{"id": "garlic-wings", "qty": 1, "price": 1, "unit_price": 1}], total=1)
        res = self.client.post(self.url, payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["subtotal"], 299)
        self.assertEqual(res.json()["total"], 299 + 80)

    def test_duplicate_lines_are_merged(self):
        payload = self.delivery_payload(items=[{"id": "fries", "qty": 1}, {"id": "fries", "qty": 2}])
        res = self.client.post(self.url, payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["subtotal"], 480)
        self.assertEqual(res.json()["total"], 480 + 80)
        self.assertEqual(len(res.json()["items"]), 1)

    def test_takeaway_needs_an_outlet(self):
        res = self.client.post(self.url, self.delivery_payload(mode="takeaway", address=""), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("outlet", res.json())

        res = self.client.post(
            self.url, self.delivery_payload(mode="takeaway", address="", outlet="mirpur"), format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["outlet"], "Mirpur")
        self.assertEqual(Order.objects.get(public_id=res.json()["order_id"]).address, "")

    def test_delivery_needs_an_address(self):
        res = self.client.post(self.url, self.delivery_payload(address="short"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("address", res.json())

    def test_rejects_bad_phone(self):
        for bad in ["12345", "0171951724", "abcdefghijk", "01119517242"]:
            res = self.client.post(self.url, self.delivery_payload(phone=bad), format="json")
            self.assertEqual(res.status_code, 400, bad)
            self.assertIn("phone", res.json())

    def test_accepts_phone_with_country_code(self):
        res = self.client.post(self.url, self.delivery_payload(phone="+880 1719-517242"), format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Order.objects.get().phone, "01719517242")

    def test_rejects_empty_or_unknown_or_unavailable_items(self):
        res = self.client.post(self.url, self.delivery_payload(items=[]), format="json")
        self.assertEqual(res.status_code, 400)

        res = self.client.post(self.url, self.delivery_payload(items=[{"id": "nope", "qty": 1}]), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("items", res.json())

        MenuItem.objects.filter(slug="fries").update(is_available=False)
        res = self.client.post(self.url, self.delivery_payload(items=[{"id": "fries", "qty": 1}]), format="json")
        self.assertEqual(res.status_code, 400)

        res = self.client.post(self.url, self.delivery_payload(items=[{"id": "fries", "qty": 0}]), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_order_keeps_its_price_after_the_menu_changes(self):
        res = self.client.post(self.url, self.delivery_payload(items=[{"id": "fries", "qty": 1}]), format="json")
        MenuItem.objects.filter(slug="fries").update(price=999)
        item = Order.objects.get(public_id=res.json()["order_id"]).items.get()
        self.assertEqual(item.unit_price, 160)

    def test_rate_limit(self):
        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"orders": "2/hour"}):
            codes = [self.client.post(self.url, self.delivery_payload(), format="json").status_code for _ in range(3)]
        self.assertEqual(codes, [201, 201, 429])


class ProxyClientIpTests(ApiTestBase):
    """Behind the reverse proxy each visitor must be rate limited on their own address."""

    url = "/api/orders/"

    def post_as(self, ip):
        return self.client.post(self.url, self.delivery_payload(), format="json", HTTP_X_FORWARDED_FOR=ip).status_code

    def test_visitors_have_separate_limits_when_a_proxy_is_trusted(self):
        from rest_framework.settings import api_settings

        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"orders": "1/hour"}), patch.object(api_settings, "NUM_PROXIES", 1):
            codes = [self.post_as("203.0.113.1"), self.post_as("203.0.113.1"), self.post_as("203.0.113.2")]
        self.assertEqual(codes, [201, 429, 201])

    def test_without_a_proxy_the_forwarded_header_is_ignored(self):
        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"orders": "1/hour"}):
            codes = [self.post_as("203.0.113.1"), self.post_as("203.0.113.2")]
        self.assertEqual(codes, [201, 429])  # a visitor cannot dodge the limit by making up an address


class DeliveryFeeTests(ApiTestBase):
    url = "/api/orders/"

    def test_delivery_orders_pay_a_flat_fee_and_it_is_stored(self):
        res = self.client.post(self.url, self.delivery_payload(items=[{"id": "garlic-wings", "qty": 1}]), format="json")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body["subtotal"], body["delivery_fee"], body["total"]), (299, 80, 379))
        order = Order.objects.get(public_id=body["order_id"])
        self.assertEqual((order.subtotal, order.delivery_fee, order.total), (299, 80, 379))

    def test_takeaway_has_no_delivery_fee(self):
        payload = self.delivery_payload(mode="takeaway", address="", outlet="mirpur", items=[{"id": "garlic-wings", "qty": 1}])
        body = self.client.post(self.url, payload, format="json").json()
        self.assertEqual((body["subtotal"], body["delivery_fee"], body["total"]), (299, 0, 299))

    def test_the_client_cannot_set_the_delivery_fee(self):
        payload = self.delivery_payload(items=[{"id": "garlic-wings", "qty": 1}], delivery_fee=0, total=1)
        body = self.client.post(self.url, payload, format="json").json()
        self.assertEqual((body["delivery_fee"], body["total"]), (80, 379))

    def test_fee_comes_from_settings(self):
        from django.test import override_settings

        with override_settings(DELIVERY_FEE=120):
            body = self.client.post(self.url, self.delivery_payload(items=[{"id": "garlic-wings", "qty": 1}]), format="json").json()
        self.assertEqual((body["delivery_fee"], body["total"]), (120, 419))


class AccountHelpers(ApiTestBase):
    def register(self, **overrides):
        data = {"name": "Rahim Uddin", "phone": "01719 517 242", "password": "secret1"}
        data.update(overrides)
        return self.client.post("/api/auth/register/", data, format="json")

    def auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")


class AccountTests(AccountHelpers):
    def test_register_returns_token_and_creates_customer(self):
        res = self.register()
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["customer"]["phone"], "01719517242")
        customer = Customer.objects.get(phone="01719517242")
        self.assertEqual(customer.name, "Rahim Uddin")
        # password is hashed, never stored as typed
        self.assertNotEqual(customer.user.password, "secret1")
        self.assertTrue(customer.user.check_password("secret1"))
        self.assertNotIn("password", body["customer"])

    def test_register_rejects_duplicate_bad_phone_and_short_password(self):
        self.assertEqual(self.register().status_code, 201)
        dup = self.register(phone="+8801719517242")
        self.assertEqual(dup.status_code, 400)
        self.assertIn("phone", dup.json())
        self.assertIn("phone", self.register(phone="12345").json())
        self.assertIn("password", self.register(phone="01811111111", password="123").json())

    def test_login_and_wrong_password(self):
        self.register()
        ok = self.client.post("/api/auth/login/", {"phone": "01719-517-242", "password": "secret1"}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["token"])
        bad = self.client.post("/api/auth/login/", {"phone": "01719517242", "password": "nope"}, format="json")
        self.assertEqual(bad.status_code, 400)
        unknown = self.client.post("/api/auth/login/", {"phone": "01911111111", "password": "secret1"}, format="json")
        # same message either way, so nobody can probe which numbers have accounts
        self.assertEqual(bad.json(), unknown.json())

    def test_staff_user_cannot_log_in_as_customer(self):
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_superuser("01712345678", "a@b.c", "secret1")
        res = self.client.post("/api/auth/login/", {"phone": "01712345678", "password": "secret1"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_me_requires_login_and_can_update_profile(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.auth(self.register().json()["token"])
        me = self.client.get("/api/auth/me/")
        self.assertEqual(me.json()["name"], "Rahim Uddin")
        self.assertEqual(me.json()["orders_count"], 0)
        res = self.client.patch("/api/auth/me/", {"name": "Rahim U.", "phone": "01999999999"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "Rahim U.")
        self.assertEqual(res.json()["phone"], "01719517242")  # phone can't be changed

    def test_logout_invalidates_token(self):
        token = self.register().json()["token"]
        self.auth(token)
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_logged_in_order_is_linked_and_listed_and_saves_address(self):
        self.auth(self.register().json()["token"])
        res = self.client.post("/api/orders/", self.delivery_payload(), format="json")
        self.assertEqual(res.status_code, 201, res.content)
        order = Order.objects.get(public_id=res.json()["order_id"])
        self.assertEqual(order.customer.phone, "01719517242")
        # the first delivery address is remembered as the default
        addresses = self.client.get("/api/auth/addresses/").json()
        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses[0]["address"], "House 12, Road 5, Dhanmondi, Dhaka")
        self.assertTrue(addresses[0]["is_default"])
        # a second order does not add duplicates
        self.client.post("/api/orders/", self.delivery_payload(address="Another place, Gulshan 1"), format="json")
        self.assertEqual(len(self.client.get("/api/auth/addresses/").json()), 1)

        mine = self.client.get("/api/orders/mine/")
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine.json()[-1]["order_id"], order.public_id)
        first_item = mine.json()[-1]["items"][0]
        self.assertEqual((first_item["id"], first_item["name"]), ("garlic-wings", "Garlic Wings"))

    def test_orders_are_private_to_each_customer(self):
        self.auth(self.register().json()["token"])
        self.client.post("/api/orders/", self.delivery_payload(), format="json")
        self.client.credentials()
        other = self.register(phone="01811111111", name="Karim").json()["token"]
        self.auth(other)
        self.assertEqual(self.client.get("/api/orders/mine/").json(), [])

    def test_guest_order_still_works_and_stale_token_does_not_block_it(self):
        res = self.client.post("/api/orders/", self.delivery_payload(), format="json")
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(Order.objects.get(public_id=res.json()["order_id"]).customer)

        self.auth("not-a-real-token")
        res = self.client.post("/api/orders/", self.delivery_payload(), format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(self.client.get("/api/orders/mine/").status_code, 401)

    def test_auth_endpoints_are_rate_limited(self):
        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"auth": "2/hour", "orders": "30/hour"}):
            payload = {"phone": "01719517242", "password": "x"}
            codes = [self.client.post("/api/auth/login/", payload, format="json").status_code for _ in range(3)]
        self.assertEqual(codes, [400, 400, 429])


class AddressBookTests(AccountHelpers):
    def setUp(self):
        super().setUp()
        self.auth(self.register().json()["token"])

    def add(self, **overrides):
        data = {"label": "Home", "address": "House 12, Road 5, Dhanmondi, Dhaka", "landmark": "Near the mosque"}
        data.update(overrides)
        return self.client.post("/api/auth/addresses/", data, format="json")

    def test_requires_login(self):
        self.client.credentials()
        self.assertEqual(self.client.get("/api/auth/addresses/").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/addresses/", {}, format="json").status_code, 401)

    def test_first_address_is_default_and_new_default_replaces_it(self):
        a = self.add()
        self.assertEqual(a.status_code, 201, a.content)
        self.assertTrue(a.json()["is_default"])
        b = self.add(label="Office", address="Level 5, Gulshan Avenue, Dhaka", is_default=True)
        self.assertTrue(b.json()["is_default"])
        listing = self.client.get("/api/auth/addresses/").json()
        self.assertEqual([x["label"] for x in listing], ["Office", "Home"])  # default first
        self.assertEqual([x["is_default"] for x in listing], [True, False])

    def test_non_default_second_address_stays_non_default(self):
        self.add()
        second = self.add(label="Office", address="Level 5, Gulshan Avenue, Dhaka")
        self.assertFalse(second.json()["is_default"])

    def test_edit_and_make_default(self):
        self.add()
        other = self.add(label="Office", address="Level 5, Gulshan Avenue, Dhaka").json()
        res = self.client.patch(f"/api/auth/addresses/{other['id']}/", {"is_default": True}, format="json")
        self.assertEqual(res.status_code, 200)
        flags = {a["label"]: a["is_default"] for a in self.client.get("/api/auth/addresses/").json()}
        self.assertEqual(flags, {"Office": True, "Home": False})
        res = self.client.patch(f"/api/auth/addresses/{other['id']}/", {"landmark": "Blue gate"}, format="json")
        self.assertEqual(res.json()["landmark"], "Blue gate")

    def test_deleting_default_promotes_another(self):
        first = self.add().json()
        self.add(label="Office", address="Level 5, Gulshan Avenue, Dhaka")
        self.assertEqual(self.client.delete(f"/api/auth/addresses/{first['id']}/").status_code, 204)
        left = self.client.get("/api/auth/addresses/").json()
        self.assertEqual([(a["label"], a["is_default"]) for a in left], [("Office", True)])

    def test_validation_and_limit(self):
        self.assertIn("address", self.add(address="short").json())
        self.assertEqual(self.add().status_code, 201)  # 1st
        self.assertEqual(self.add(label="Office", address="Level 5, Gulshan Avenue, Dhaka").status_code, 201)  # 2nd
        third = self.add(label="Other", address="Flat 9, Lake Road, Uttara, Dhaka")
        self.assertEqual(third.status_code, 400)
        self.assertIn("up to 2 addresses", str(third.json()))
        # editing existing ones still works at the limit, and deleting frees a slot
        first = self.client.get("/api/auth/addresses/").json()[0]
        self.assertEqual(self.client.patch(f"/api/auth/addresses/{first['id']}/", {"label": "Other"}, format="json").status_code, 200)
        self.client.delete(f"/api/auth/addresses/{first['id']}/")
        self.assertEqual(self.add(address="Flat 9, Lake Road, Uttara, Dhaka").status_code, 201)

    def test_addresses_are_private(self):
        mine = self.add().json()
        self.client.credentials()
        self.auth(self.register(phone="01811111111", name="Karim").json()["token"])
        self.assertEqual(self.client.get("/api/auth/addresses/").json(), [])
        self.assertEqual(self.client.patch(f"/api/auth/addresses/{mine['id']}/", {"label": "x"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/auth/addresses/{mine['id']}/").status_code, 404)

    def test_change_password(self):
        url = "/api/auth/password/"
        bad = self.client.post(url, {"current_password": "wrong", "new_password": "newpass1"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("current_password", bad.json())
        short = self.client.post(url, {"current_password": "secret1", "new_password": "123"}, format="json")
        self.assertIn("new_password", short.json())
        ok = self.client.post(url, {"current_password": "secret1", "new_password": "newpass1"}, format="json")
        self.assertEqual(ok.status_code, 204)
        self.client.credentials()
        old = self.client.post("/api/auth/login/", {"phone": "01719517242", "password": "secret1"}, format="json")
        new = self.client.post("/api/auth/login/", {"phone": "01719517242", "password": "newpass1"}, format="json")
        self.assertEqual((old.status_code, new.status_code), (400, 200))


class CategoryAndSlugTests(ApiTestBase):
    def test_slugs_are_generated_from_the_name_and_stay_unique(self):
        cat = Category.objects.create(name="Desserts & Drinks")
        self.assertEqual(cat.slug, "desserts-drinks")
        a = MenuItem.objects.create(name="Mango Lassi", price=120, category=cat)
        b = MenuItem.objects.create(name="Mango Lassi", price=130, category=cat)
        c = MenuItem.objects.create(name="Mango  Lassi!", price=140, category=cat)
        self.assertEqual((a.slug, b.slug, c.slug), ("mango-lassi", "mango-lassi-2", "mango-lassi-3"))
        # a category and a product are independent namespaces
        self.assertEqual(Category.objects.create(name="Mango Lassi").slug, "mango-lassi")

    def test_slug_never_changes_after_creation(self):
        item = MenuItem.objects.create(name="Spicy Wrap", price=200, category=Category.objects.first())
        item.name = "Super Spicy Wrap"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.slug, "spicy-wrap")
        # existing storefront ids are untouched
        self.assertTrue(MenuItem.objects.filter(slug="bucket-26").exists())

    def test_name_with_no_latin_letters_still_gets_a_slug(self):
        cat = Category.objects.first()
        # no ASCII letters to build a slug from: fall back to "item", still unique
        self.assertEqual(MenuItem.objects.create(name="চিকেন বার্গার", price=250, category=cat).slug, "item")
        self.assertEqual(MenuItem.objects.create(name="!!!", price=1, category=cat).slug, "item-2")

    def test_categories_endpoint_and_menu_items_carry_their_category(self):
        res = self.client.get(reverse("categories"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([c["id"] for c in res.json()], ["special", "combo", "single", "burger", "sides"])
        item = next(i for i in self.client.get(reverse("menu")).json() if i["id"] == "garlic-wings")
        self.assertEqual((item["category"], item["category_name"]), ("single", "Wings & bites"))

    def test_hiding_a_category_hides_its_items_and_blocks_ordering_them(self):
        Category.objects.filter(slug="sides").update(is_active=False)
        ids = [i["id"] for i in self.client.get(reverse("menu")).json()]
        self.assertNotIn("fries", ids)
        self.assertEqual(len(ids), 22)  # 24 items minus the 2 sides
        self.assertNotIn("sides", [c["id"] for c in self.client.get(reverse("categories")).json()])
        res = self.client.post("/api/orders/", self.delivery_payload(), format="json")  # includes fries
        self.assertEqual(res.status_code, 400)
        self.assertIn("items", res.json())

    def test_seed_keeps_admin_edits_to_categories(self):
        Category.objects.filter(slug="burger").update(name="Burgers & Sandwiches", sort_order=9)
        call_command("seed_menu", verbosity=0)
        cat = Category.objects.get(slug="burger")
        self.assertEqual((cat.name, cat.sort_order), ("Burgers & Sandwiches", 9))

    def test_a_category_with_products_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            Category.objects.get(slug="combo").delete()


class AdminPanelTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-12345")
        self.client.force_login(self.admin)

    def test_admin_pages_load(self):
        for url in [
            "/admin/core/category/",
            "/admin/core/category/add/",
            "/admin/core/menuitem/",
            "/admin/core/menuitem/add/",
            "/admin/core/todaysoffer/",
            "/admin/core/todaysoffer/add/",
            "/admin/core/addon/",
            "/admin/core/addon/add/",
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_pick_a_todays_offer_from_the_menu_in_admin(self):
        from .models import TodaysOffer

        TodaysOffer.objects.all().delete()
        fries = MenuItem.objects.get(slug="fries")
        res = self.client.post(
            "/admin/core/todaysoffer/add/", {"menu_item": fries.pk, "offer_price": fries.price - 10, "sort_order": 1, "is_active": "on"}
        )
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        self.assertEqual([i["id"] for i in self.client.get("/api/offers/today/").json()], ["fries"])

    def test_pick_an_addon_from_the_menu_in_admin(self):
        from .models import AddOn

        AddOn.objects.all().delete()
        leg = MenuItem.objects.get(slug="bbq-leg")
        res = self.client.post("/admin/core/addon/add/", {"menu_item": leg.pk, "sort_order": 1, "is_active": "on"})
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        self.client.logout()
        self.assertEqual([i["id"] for i in self.client.get("/api/addons/").json()], ["bbq-leg"])

    def test_menu_item_autocomplete_works_for_the_addon_picker(self):
        res = self.client.get(
            "/admin/autocomplete/", {"term": "fries", "app_label": "core", "model_name": "addon", "field_name": "menu_item"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(any("Fries" in r["text"] for r in res.json()["results"]))

    def test_menu_item_autocomplete_works_for_the_offer_picker(self):
        res = self.client.get(
            "/admin/autocomplete/", {"term": "fries", "app_label": "core", "model_name": "todaysoffer", "field_name": "menu_item"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(any("Fries" in r["text"] for r in res.json()["results"]))

    def test_a_product_cannot_be_added_without_a_picture(self):
        drinks = Category.objects.create(name="Drinks")
        res = self.client.post(
            "/admin/core/menuitem/add/",
            {"name": "Cold Coffee", "category": drinks.pk, "price": 150, "blurb": "", "is_available": "on", "sort_order": 50},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("image", res.context["adminform"].form.errors)
        self.assertFalse(MenuItem.objects.filter(name="Cold Coffee").exists())

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
    )
    def test_add_category_and_product_in_admin_without_typing_a_slug(self):
        res = self.client.post(
            "/admin/core/category/add/", {"name": "Drinks", "sort_order": 5, "is_active": "on"}
        )
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        drinks = Category.objects.get(name="Drinks")
        self.assertEqual(drinks.slug, "drinks")

        res = self.client.post(
            "/admin/core/menuitem/add/",
            {
                "name": "Cold Coffee",
                "category": drinks.pk,
                "price": 150,
                "blurb": "",
                "is_available": "on",
                "sort_order": 50,
                "image": picture(width=300, height=300),
            },
        )
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        item = MenuItem.objects.get(name="Cold Coffee")
        self.assertEqual((item.slug, item.category_id), ("cold-coffee", drinks.pk))
        # and it is immediately orderable through the API
        self.client.logout()
        self.assertIn("cold-coffee", [i["id"] for i in self.client.get(reverse("menu")).json()])
        order = self.client.post(
            "/api/orders/", self.delivery_payload(items=[{"id": "cold-coffee", "qty": 2}]), format="json"
        )
        self.assertEqual(order.status_code, 201, order.content)
        self.assertEqual(order.json()["total"], 300 + 80)


def at_dhaka(day, hour, minute=0):
    """A fixed 'now' in Dhaka time (UTC+6, no daylight saving)."""
    from datetime import datetime, timedelta, timezone as dt_timezone

    return datetime(2026, 11, day, hour, minute, tzinfo=dt_timezone(timedelta(hours=6)))


class TodaysOfferTests(ApiTestBase):
    url = "/api/offers/today/"

    def slugs(self):
        return [i["id"] for i in self.client.get(self.url).json()]

    def test_starter_offers_come_from_the_seed_in_order(self):
        self.assertEqual(self.slugs(), ["bucket-26", "bogo-burger", "pkg-3", "pkg-10"])

    def test_response_has_the_menu_item_shape(self):
        first = self.client.get(self.url).json()[0]
        self.assertEqual(
            set(first), {"id", "name", "price", "offer_price", "category", "category_name", "blurb", "image", "featured"}
        )

    def test_admin_can_pick_any_menu_item_and_order_them(self):
        from .models import TodaysOffer

        TodaysOffer.objects.all().delete()
        TodaysOffer.objects.create(menu_item=MenuItem.objects.get(slug="fries"), sort_order=2)
        TodaysOffer.objects.create(menu_item=MenuItem.objects.get(slug="garlic-wings"), sort_order=1)
        self.assertEqual(self.slugs(), ["garlic-wings", "fries"])

    def test_switched_off_or_unavailable_items_are_not_shown(self):
        from .models import TodaysOffer

        TodaysOffer.objects.filter(menu_item__slug="pkg-3").update(is_active=False)
        MenuItem.objects.filter(slug="pkg-10").update(is_available=False)
        self.assertEqual(self.slugs(), ["bucket-26", "bogo-burger"])

    def test_an_item_cannot_be_an_offer_twice(self):
        from django.db import IntegrityError, transaction
        from .models import TodaysOffer

        with self.assertRaises(IntegrityError), transaction.atomic():
            TodaysOffer.objects.create(menu_item=MenuItem.objects.get(slug="bucket-26"))

    def test_seeding_again_does_not_bring_back_removed_offers(self):
        from .models import TodaysOffer

        TodaysOffer.objects.all().delete()
        TodaysOffer.objects.create(menu_item=MenuItem.objects.get(slug="fries"))
        call_command("seed_menu", verbosity=0)
        self.assertEqual(self.slugs(), ["fries"])

    def test_offer_price_follows_the_menu_item(self):
        MenuItem.objects.filter(slug="bucket-26").update(price=999)
        self.assertEqual(self.client.get(self.url).json()[0]["price"], 999)


class AddOnTests(ApiTestBase):
    url = "/api/addons/"

    def slugs(self):
        return [i["id"] for i in self.client.get(self.url).json()]

    def test_starter_addons_come_from_the_seed_in_order(self):
        self.assertEqual(self.slugs(), ["fries", "wrap", "nuggets", "pops", "hot-wings", "garlic-wings"])

    def test_response_has_the_menu_item_shape(self):
        first = self.client.get(self.url).json()[0]
        self.assertEqual(
            set(first), {"id", "name", "price", "offer_price", "category", "category_name", "blurb", "image", "featured"}
        )

    def test_admin_can_pick_any_menu_item_and_order_them(self):
        from .models import AddOn

        AddOn.objects.all().delete()
        AddOn.objects.create(menu_item=MenuItem.objects.get(slug="bbq-leg"), sort_order=2)
        AddOn.objects.create(menu_item=MenuItem.objects.get(slug="fries"), sort_order=1)
        self.assertEqual(self.slugs(), ["fries", "bbq-leg"])

    def test_switched_off_or_unavailable_items_are_not_suggested(self):
        from .models import AddOn

        AddOn.objects.filter(menu_item__slug="wrap").update(is_active=False)
        MenuItem.objects.filter(slug="pops").update(is_available=False)
        self.assertEqual(self.slugs(), ["fries", "nuggets", "hot-wings", "garlic-wings"])

    def test_an_item_cannot_be_an_addon_twice(self):
        from django.db import IntegrityError, transaction
        from .models import AddOn

        with self.assertRaises(IntegrityError), transaction.atomic():
            AddOn.objects.create(menu_item=MenuItem.objects.get(slug="fries"))

    def test_seeding_again_does_not_bring_back_removed_addons(self):
        from .models import AddOn

        AddOn.objects.all().delete()
        AddOn.objects.create(menu_item=MenuItem.objects.get(slug="bbq-leg"))
        call_command("seed_menu", verbosity=0)
        self.assertEqual(self.slugs(), ["bbq-leg"])

    def test_an_active_offer_on_an_addon_shows_and_is_charged(self):
        from .models import TodaysOffer

        TodaysOffer.objects.create(menu_item=MenuItem.objects.get(slug="fries"), offer_price=100)  # fries cost 160
        fries = {i["id"]: i for i in self.client.get(self.url).json()}["fries"]
        self.assertEqual((fries["price"], fries["offer_price"]), (160, 100))
        order = self.client.post(
            "/api/orders/",
            self.delivery_payload(items=[{"id": "fries", "qty": 2}]),
            format="json",
        )
        self.assertEqual(order.status_code, 201, order.content)
        self.assertEqual(order.json()["total"], 200 + 80)


def picture(width=3000, height=2000, fmt="JPEG", orientation=None, color=(200, 40, 20)):
    """An uploaded picture, as the admin form would send it."""
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    out = BytesIO()
    extra = {}
    if orientation:
        exif = img.getexif()
        exif[0x0112] = orientation
        extra["exif"] = exif
    img.save(out, format=fmt, **extra)
    return SimpleUploadedFile(f"photo.{fmt.lower()}", out.getvalue(), content_type=f"image/{fmt.lower()}")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class ProductPictureTests(ApiTestBase):
    def item(self, slug="fries"):
        return MenuItem.objects.get(slug=slug)

    def attach(self, slug="fries", **kwargs):
        item = self.item(slug)
        item.image = picture(**kwargs)
        item.save()
        item.refresh_from_db()
        return item

    def test_items_have_no_picture_until_one_is_uploaded(self):
        for row in self.client.get(reverse("menu")).json():
            self.assertIsNone(row["image"])

    def test_upload_is_shrunk_and_stored_as_webp(self):
        from PIL import Image

        item = self.attach(width=3000, height=2000)
        self.assertRegex(item.image.name, r"^menu/fries-[0-9a-f]{8}\.webp$")
        with item.image.open("rb") as f, Image.open(f) as stored:
            self.assertEqual(stored.format, "WEBP")
            self.assertEqual(max(stored.size), 1200)
            self.assertEqual(stored.size, (1200, 800))  # same proportions

    def test_small_pictures_are_not_enlarged(self):
        from PIL import Image

        item = self.attach(width=400, height=300)
        with item.image.open("rb") as f, Image.open(f) as stored:
            self.assertEqual(stored.size, (400, 300))

    def test_phone_photos_are_turned_the_right_way_up(self):
        from PIL import Image

        item = self.attach(width=200, height=100, orientation=6)  # "rotate 90 degrees" in the metadata
        with item.image.open("rb") as f, Image.open(f) as stored:
            self.assertEqual(stored.size, (100, 200))

    def test_png_with_transparency_is_kept(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        out = BytesIO()
        Image.new("RGBA", (50, 50), (255, 0, 0, 0)).save(out, format="PNG")
        item = self.item()
        item.image = SimpleUploadedFile("cutout.png", out.getvalue(), content_type="image/png")
        item.save()
        with item.image.open("rb") as f, Image.open(f) as stored:
            self.assertIn("A", stored.getbands())

    def test_api_gives_the_full_address_of_the_picture(self):
        item = self.attach()
        row = next(i for i in self.client.get(reverse("menu")).json() if i["id"] == "fries")
        self.assertTrue(row["image"].startswith("http://testserver/media/menu/fries-"), row["image"])
        self.assertTrue(row["image"].endswith(item.image.name.split("/")[-1]))

    def test_offers_and_addons_carry_the_picture_too(self):
        self.attach("fries")  # an add-on
        self.attach("bucket-26")  # an offer
        addon = next(i for i in self.client.get("/api/addons/").json() if i["id"] == "fries")
        offer = next(i for i in self.client.get("/api/offers/today/").json() if i["id"] == "bucket-26")
        self.assertIn("/media/menu/fries-", addon["image"])
        self.assertIn("/media/menu/bucket-26-", offer["image"])

    def test_replacing_a_picture_removes_the_old_file(self):
        from django.core.files.storage import default_storage

        old = self.attach().image.name
        new = self.attach().image.name
        self.assertNotEqual(old, new)
        self.assertFalse(default_storage.exists(old))
        self.assertTrue(default_storage.exists(new))

    def test_clearing_or_deleting_removes_the_file(self):
        from django.core.files.storage import default_storage

        item = self.attach()
        name = item.image.name
        item.image = ""
        item.save()
        self.assertFalse(default_storage.exists(name))
        self.assertIsNone(next(i for i in self.client.get(reverse("menu")).json() if i["id"] == "fries")["image"])

        name = self.attach().image.name
        self.item().delete()
        self.assertFalse(default_storage.exists(name))

    def test_saving_without_a_new_upload_keeps_the_picture(self):
        from django.core.files.storage import default_storage

        item = self.attach()
        name = item.image.name
        item.price = 170
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.image.name, name)
        self.assertTrue(default_storage.exists(name))

    def test_something_that_is_not_a_picture_is_refused_in_admin(self):
        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-12345"))
        res = self.client.post(
            "/admin/core/menuitem/add/",
            {
                "name": "Coleslaw",
                "category": Category.objects.get(slug="sides").pk,
                "price": 90,
                "image": SimpleUploadedFile("notes.jpg", b"this is not an image", content_type="image/jpeg"),
                "is_available": "on",
                "sort_order": 60,
            },
        )
        self.assertEqual(res.status_code, 200)  # the form is shown again with an error
        self.assertIn("image", res.context["adminform"].form.errors)
        self.assertFalse(MenuItem.objects.filter(name="Coleslaw").exists())

    def test_huge_files_are_refused(self):
        from unittest import mock

        from django.core.exceptions import ValidationError

        item = self.item()
        item.image = picture(width=200, height=200)
        with mock.patch("core.images.MAX_UPLOAD_BYTES", 100), self.assertRaises(ValidationError):
            item.full_clean()

    def test_upload_a_picture_with_a_new_product_in_admin(self):
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-12345"))
        cat = Category.objects.get(slug="sides")
        res = self.client.post(
            "/admin/core/menuitem/add/",
            {
                "name": "Coleslaw",
                "category": cat.pk,
                "price": 90,
                "blurb": "",
                "image": picture(width=900, height=600),
                "is_available": "on",
                "sort_order": 60,
            },
        )
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        self.client.logout()
        row = next(i for i in self.client.get(reverse("menu")).json() if i["id"] == "coleslaw")
        self.assertIn("/media/menu/coleslaw-", row["image"])


class S3StorageSettingsTests(SimpleTestCase):
    """With a bucket named, pictures go to S3 and are shown by their plain public address."""

    def settings_output(self, **env):
        import os
        import subprocess
        import sys

        code = (
            "import django, json; django.setup();"
            "from django.conf import settings; from django.core.files.storage import default_storage as d;"
            "print(json.dumps([settings.STORAGES['default']['BACKEND'], d.url('menu/x.webp')]))"
        )
        full = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
        full.update(AWS_STORAGE_BUCKET_NAME="", AWS_S3_REGION_NAME="", AWS_S3_CUSTOM_DOMAIN="")  # .env must not leak in
        full.update(DJANGO_SETTINGS_MODULE="config.settings", AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", **env)
        out = subprocess.run([sys.executable, "-c", code], env=full, capture_output=True, text=True, cwd=str(settings.BASE_DIR))
        self.assertEqual(out.returncode, 0, out.stderr)
        import json

        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_no_bucket_means_local_disk(self):
        backend, url = self.settings_output()
        self.assertEqual(backend, "django.core.files.storage.FileSystemStorage")
        self.assertEqual(url, "/media/menu/x.webp")

    def test_bucket_means_s3_with_plain_addresses(self):
        backend, url = self.settings_output(AWS_STORAGE_BUCKET_NAME="stallo-media", AWS_S3_REGION_NAME="ap-southeast-1")
        self.assertEqual(backend, "storages.backends.s3.S3Storage")
        self.assertTrue(url.startswith("https://stallo-media.s3.ap-southeast-1.amazonaws.com/menu/x.webp"), url)
        self.assertNotIn("Signature", url)
        self.assertNotIn("Expires", url)

    def test_custom_domain_is_used_when_given(self):
        _, url = self.settings_output(AWS_STORAGE_BUCKET_NAME="stallo-media", AWS_S3_CUSTOM_DOMAIN="cdn.example.com")
        self.assertEqual(url, "https://cdn.example.com/menu/x.webp")


class PickupTimeTests(ApiTestBase):
    url = "/api/orders/"

    def book(self, now, pickup, **overrides):
        payload = self.delivery_payload(mode="takeaway", address="", outlet="mirpur", **overrides)
        if pickup is not None:
            payload["pickup_time"] = pickup.isoformat() if hasattr(pickup, "isoformat") else pickup
        with patch("django.utils.timezone.now", return_value=now):
            return self.client.post(self.url, payload, format="json")

    def slots(self, now):
        with patch("django.utils.timezone.now", return_value=now):
            return self.client.get("/api/pickup-slots/").json()

    # --- the list of times --------------------------------------------------------------

    def test_slots_start_30_minutes_out_on_the_quarter_hour(self):
        data = self.slots(at_dhaka(18, 14, 2))  # Wednesday 2:02 pm
        today = data["days"][0]
        self.assertEqual(today["label"], "Today")
        self.assertEqual(today["slots"][0]["label"], "2:45 pm")  # 2:32 rounded up
        self.assertTrue(data["open_now"])
        self.assertEqual(data["lead_minutes"], 30)

    def test_weekday_slots_stop_15_minutes_before_10pm(self):
        today = self.slots(at_dhaka(18, 14))["days"][0]
        self.assertEqual(today["slots"][-1]["label"], "9:45 pm")

    def test_friday_and_saturday_run_an_hour_later(self):
        today = self.slots(at_dhaka(20, 14))["days"][0]  # Friday
        self.assertEqual(today["slots"][-1]["label"], "10:45 pm")

    def test_only_today_is_offered(self):
        data = self.slots(at_dhaka(18, 14))
        self.assertEqual([d["label"] for d in data["days"]], ["Today"])

    def test_after_closing_nothing_is_left(self):
        data = self.slots(at_dhaka(18, 22, 30))
        self.assertFalse(data["open_now"])
        self.assertEqual(data["days"], [])

    def test_before_opening_today_starts_at_opening(self):
        data = self.slots(at_dhaka(18, 7))
        self.assertFalse(data["open_now"])
        self.assertEqual(data["days"][0]["slots"][0]["label"], "10:00 am")

    def test_every_offered_slot_is_accepted(self):
        now = at_dhaka(18, 14)
        for day in self.slots(now)["days"]:
            for slot in (day["slots"][0], day["slots"][-1]):
                res = self.book(now, slot["value"])
                self.assertEqual(res.status_code, 201, (slot, res.json()))

    # --- placing an order ---------------------------------------------------------------

    def test_pickup_time_is_saved_and_returned(self):
        now = at_dhaka(18, 14)
        res = self.book(now, at_dhaka(18, 16, 15))
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(Order.objects.get(public_id=res.json()["order_id"]).pickup_time, at_dhaka(18, 16, 15))
        self.assertTrue(res.json()["pickup_time"].startswith("2026-11-18T16:15:00"))

    def test_no_pickup_time_means_as_soon_as_possible(self):
        res = self.book(at_dhaka(18, 14), None)
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.json()["pickup_time"])

    def test_another_day_is_rejected(self):
        res = self.book(at_dhaka(18, 14), at_dhaka(19, 12))  # tomorrow
        self.assertEqual(res.status_code, 400)
        self.assertIn("today only", res.json()["pickup_time"][0])
        self.assertEqual(self.book(at_dhaka(18, 14), at_dhaka(25, 12)).status_code, 400)

    def test_too_soon_is_rejected(self):
        res = self.book(at_dhaka(18, 14), at_dhaka(18, 14, 15))
        self.assertEqual(res.status_code, 400)
        self.assertIn("at least 30 minutes", res.json()["pickup_time"][0])

    def test_a_time_in_the_past_is_rejected(self):
        self.assertEqual(self.book(at_dhaka(18, 14), at_dhaka(18, 9)).status_code, 400)

    def test_slot_picked_a_few_minutes_ago_still_works(self):
        # offered as the earliest at 2:02 (2:45); the form is submitted at 2:12
        self.assertEqual(self.book(at_dhaka(18, 14, 12), at_dhaka(18, 14, 45)).status_code, 201)

    def test_off_the_quarter_hour_is_rejected(self):
        res = self.book(at_dhaka(18, 14), at_dhaka(18, 16, 10))
        self.assertEqual(res.status_code, 400)
        self.assertIn("quarter hour", res.json()["pickup_time"][0])

    def test_outside_opening_hours_is_rejected(self):
        now = at_dhaka(18, 14)
        self.assertEqual(self.book(now, at_dhaka(18, 22)).status_code, 400)  # closing time itself
        self.assertEqual(self.book(now, at_dhaka(18, 21, 45)).status_code, 201)  # last slot
        self.assertEqual(self.book(at_dhaka(18, 7), at_dhaka(18, 9, 45)).status_code, 400)  # before opening
        self.assertEqual(self.book(at_dhaka(18, 7), at_dhaka(18, 10)).status_code, 201)  # opening time
        self.assertEqual(self.book(at_dhaka(20, 14), at_dhaka(20, 22, 45)).status_code, 201)  # Friday is later

    def test_garbage_is_rejected(self):
        self.assertEqual(self.book(at_dhaka(18, 14), "tomorrow-ish").status_code, 400)

    def test_delivery_orders_ignore_a_pickup_time(self):
        payload = self.delivery_payload()
        payload["pickup_time"] = at_dhaka(18, 16).isoformat()
        with patch("django.utils.timezone.now", return_value=at_dhaka(18, 14)):
            res = self.client.post(self.url, payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.json()["pickup_time"])
        self.assertIsNone(Order.objects.get(public_id=res.json()["order_id"]).pickup_time)


class OfferPriceTests(ApiTestBase):
    """An offer shows the regular price next to a lower offer price, and the lower price is what is charged."""

    def setUp(self):
        super().setUp()
        from .models import TodaysOffer

        self.offer = TodaysOffer.objects.get(menu_item__slug="bucket-26")  # regular price 1050
        self.offer.offer_price = 899
        self.offer.save()

    def offers(self):
        return {i["id"]: i for i in self.client.get("/api/offers/today/").json()}

    def order(self, items, **overrides):
        payload = self.delivery_payload(items=items, **overrides)
        return self.client.post("/api/orders/", payload, format="json")

    def test_offer_shows_both_prices(self):
        item = self.offers()["bucket-26"]
        self.assertEqual((item["price"], item["offer_price"]), (1050, 899))

    def test_offer_without_a_special_price_has_no_offer_price(self):
        self.assertIsNone(self.offers()["bogo-burger"]["offer_price"])

    def test_menu_endpoint_carries_the_offer_price_too(self):
        by_id = {i["id"]: i for i in self.client.get("/api/menu/").json()}
        self.assertEqual(by_id["bucket-26"]["offer_price"], 899)
        self.assertIsNone(by_id["fries"]["offer_price"])

    def test_order_is_charged_the_offer_price(self):
        res = self.order([{"id": "bucket-26", "qty": 2}])
        body = res.json()
        self.assertEqual(res.status_code, 201, body)
        self.assertEqual(body["items"][0]["unit_price"], 899)
        self.assertEqual(body["subtotal"], 1798)
        self.assertEqual(body["total"], 1798 + 80)
        self.assertEqual(Order.objects.get(public_id=body["order_id"]).items.get().unit_price, 899)

    def test_other_items_still_cost_the_regular_price(self):
        body = self.order([{"id": "bucket-26", "qty": 1}, {"id": "fries", "qty": 1}]).json()
        fries = MenuItem.objects.get(slug="fries").price
        self.assertEqual(body["subtotal"], 899 + fries)

    def test_client_cannot_set_the_price(self):
        body = self.order([{"id": "bucket-26", "qty": 1, "price": 1, "unit_price": 1}]).json()
        self.assertEqual(body["subtotal"], 899)

    def test_switching_the_offer_off_restores_the_regular_price(self):
        self.offer.is_active = False
        self.offer.save()
        self.assertEqual(self.order([{"id": "bucket-26", "qty": 1}]).json()["subtotal"], 1050)
        self.assertNotIn("bucket-26", self.offers())
        self.assertIsNone({i["id"]: i for i in self.client.get("/api/menu/").json()}["bucket-26"]["offer_price"])

    def test_removing_the_offer_restores_the_regular_price(self):
        self.offer.delete()
        self.assertEqual(self.order([{"id": "bucket-26", "qty": 1}]).json()["subtotal"], 1050)

    def test_an_old_order_keeps_the_price_it_was_paid_at(self):
        order_id = self.order([{"id": "bucket-26", "qty": 1}]).json()["order_id"]
        self.offer.delete()
        self.assertEqual(Order.objects.get(public_id=order_id).items.get().unit_price, 899)

    def test_offer_price_not_below_the_regular_price_is_ignored(self):
        # e.g. the menu price was cut to 800 after the offer was set at 899
        MenuItem.objects.filter(slug="bucket-26").update(price=800)
        self.assertIsNone(self.offers()["bucket-26"]["offer_price"])
        self.assertEqual(self.order([{"id": "bucket-26", "qty": 1}]).json()["subtotal"], 800)

    def test_admin_rejects_an_offer_price_that_is_not_lower(self):
        from django.contrib.auth import get_user_model
        from .models import TodaysOffer

        self.client.force_login(get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345"))
        TodaysOffer.objects.all().delete()
        fries = MenuItem.objects.get(slug="fries")
        for bad in (fries.price, fries.price + 50):
            res = self.client.post("/admin/core/todaysoffer/add/", {"menu_item": fries.pk, "offer_price": bad, "sort_order": 1, "is_active": "on"})
            self.assertEqual(res.status_code, 200, bad)  # form shown again with an error
            self.assertContains(res, "must be lower than the regular price")
        self.assertFalse(TodaysOffer.objects.exists())
        ok = self.client.post("/admin/core/todaysoffer/add/", {"menu_item": fries.pk, "offer_price": fries.price - 10, "sort_order": 1, "is_active": "on"})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(self.offers()["fries"]["offer_price"], fries.price - 10)

    def test_admin_requires_an_offer_price(self):
        from django.contrib.auth import get_user_model
        from .models import TodaysOffer

        self.client.force_login(get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345"))
        TodaysOffer.objects.all().delete()
        res = self.client.post("/admin/core/todaysoffer/add/", {"menu_item": MenuItem.objects.get(slug="fries").pk, "sort_order": 1, "is_active": "on"})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(TodaysOffer.objects.exists())
