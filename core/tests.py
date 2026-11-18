from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
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
        # 2 × 299 + 1 × 160
        self.assertEqual(body["total"], 758)

        order = Order.objects.get(public_id=body["order_id"])
        self.assertEqual(order.phone, "01719517242")
        self.assertEqual(order.items.count(), 2)

    def test_prices_come_from_the_server_not_the_client(self):
        payload = self.delivery_payload(items=[{"id": "garlic-wings", "qty": 1, "price": 1, "unit_price": 1}], total=1)
        res = self.client.post(self.url, payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["total"], 299)

    def test_duplicate_lines_are_merged(self):
        payload = self.delivery_payload(items=[{"id": "fries", "qty": 1}, {"id": "fries", "qty": 2}])
        res = self.client.post(self.url, payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["total"], 480)
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
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_add_category_and_product_in_admin_without_typing_a_slug(self):
        res = self.client.post(
            "/admin/core/category/add/", {"name": "Drinks", "sort_order": 5, "is_active": "on"}
        )
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        drinks = Category.objects.get(name="Drinks")
        self.assertEqual(drinks.slug, "drinks")

        res = self.client.post(
            "/admin/core/menuitem/add/",
            {"name": "Cold Coffee", "category": drinks.pk, "price": 150, "blurb": "", "is_available": "on", "sort_order": 50},
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
        self.assertEqual(order.json()["total"], 300)
