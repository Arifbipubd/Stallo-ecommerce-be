# Stallo backend (Django)

Django 4.2 + Django REST framework API for the Stallo storefront: menu, outlets and
**cash-on-delivery orders**. SQLite for development.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_menu          # loads the menu + 8 outlets (safe to re-run)
python manage.py createsuperuser    # for /admin
python manage.py runserver 0.0.0.0:8000
```

`0.0.0.0` lets you test the storefront from a phone on the same Wi-Fi.
Run the tests with `python manage.py test`.

## API

Interactive docs (Swagger UI): <http://localhost:8000/api/docs/> · ReDoc: `/api/redoc/` · OpenAPI schema: `/api/schema/`.
After pulling this change run `pip install -r requirements.txt` once.

| Method | URL | What |
| --- | --- | --- |
| GET | `/api/health/` | liveness check |
| GET | `/api/menu/` | menu items that can currently be ordered (each has its `category` id and `category_name`) |
| GET | `/api/categories/` | active menu categories, in display order |
| GET | `/api/outlets/` | active outlets |
| POST | `/api/orders/` | place an order (returns the receipt; linked to the account if logged in) |
| POST | `/api/auth/register/` | create a customer account: `name`, `phone`, `password` (6+ chars) |
| POST | `/api/auth/login/` | log in with `phone` + `password` |
| POST | `/api/auth/logout/` | log out (needs token) |
| GET / PATCH | `/api/auth/me/` | profile: name, phone (read-only), order count (needs token) |
| POST | `/api/auth/password/` | change password: `current_password`, `new_password` (needs token) |
| GET / POST | `/api/auth/addresses/` | saved delivery addresses, main one first (max 2; needs token) |
| PATCH / DELETE | `/api/auth/addresses/<id>/` | edit, set main (`is_default: true`) or delete (needs token) |
| GET | `/api/orders/mine/` | my past orders with their menu ids, newest first (needs token) |

### Customer accounts

Customers sign up and log in with their **mobile number + password** (no email or OTP, to keep it
simple for older customers). Register/login return a `token`; send it as
`Authorization: Token <key>`. In Swagger click **Authorize** and paste `Token <key>` (including the word `Token`). Guest checkout still works without an account, and a stale token never blocks an order.

- Passwords are hashed by Django; login/sign-up are rate limited per IP (`AUTH_THROTTLE_RATE`).
- Customers appear under **Customers** in `/admin/` (with order counts). To reset a forgotten
  password, open the customer's linked **User** under *Authentication > Users* and use the password form.
- The phone number is **not verified** by SMS, so anyone can sign up with any number. Add an OTP step
  before relying on accounts for anything sensitive.

`POST /api/orders/`

```json
{
  "mode": "delivery",            // or "takeaway"
  "name": "Bipu Das",
  "phone": "01719 517 242",      // Bangladeshi mobile; +88 / spaces / dashes are fine
  "address": "House 12, Road 5, Dhanmondi",   // required for delivery
  "landmark": "",                // optional
  "outlet": "",                  // outlet id (e.g. "mirpur"), required for takeaway
  "notes": "",
  "items": [{ "id": "garlic-wings", "qty": 2 }]
}
```

- Prices are **never** read from the request. Every line is priced from the menu in the database.
- Payment is always cash (on delivery, or at the outlet for takeaway). Orders start as `pending`
  until staff call the customer to confirm.
- Errors are `400` with `{ "field": ["message"] }`; more than `ORDER_THROTTLE_RATE` orders per hour
  from one IP gives `429`.

## Managing orders and the menu

Open `/admin/`:

- **Orders**: change status (Confirmed, Preparing, Out for delivery, Completed, Cancelled) with the bulk actions.
- **Categories**: add, rename, reorder (sort order) or hide a menu category. Hiding a category hides all its products
  from the menu and stops them being ordered. A category that still has products can't be deleted; move or delete
  the products first.
- **Menu items**: add products, pick a category, edit prices, switch items on/off ("Is available").
- **Slugs are automatic.** A product's or category's slug (e.g. `mango-lassi`) is made from its name when it is
  created ("mango-lassi-2" if taken) and never changes afterwards, even if you rename the product, because carts and
  past orders refer to it. Names with no English letters (e.g. only Bangla) get `item`, `item-2`, ... instead.

## Menu data

`python manage.py seed_menu` loads `core/fixtures/menu.json` (exported from the storefront's `src/data/site.ts`).
It never overwrites category names or order you changed in the admin.
The storefront still shows its own copy of the menu, so a **new product or category added in `/admin/` will not
appear on the website**, and a changed price keeps showing the old one, until the frontend reads the menu from
`/api/menu/` and `/api/categories/`. Orders are always charged at the database price.

## CORS

In development (`DJANGO_DEBUG=True`) `localhost` and private-network addresses are allowed.
In production set `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and
`CORS_ALLOWED_ORIGINS` in `.env`.
