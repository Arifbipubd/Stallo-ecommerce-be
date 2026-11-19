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

## Docker

Runs the API on its own with gunicorn (the storefront has its own Dockerfile and compose file):

```bash
cp .env.example .env             # then set DJANGO_SECRET_KEY to a long random string
docker compose up --build        # API on http://localhost:8000, docs at /api/docs/
```

- The SQLite database lives in the `stallo-data` volume, so orders and admin edits survive restarts and rebuilds.
- On start it applies migrations and, **only the first time**, loads the menu and outlets. It never reseeds,
  so admin changes are safe.
- Add `DJANGO_SUPERUSER_USERNAME` and `DJANGO_SUPERUSER_PASSWORD` to `.env` to create an admin login on start
  (skipped if it already exists). Otherwise: `docker compose exec api python manage.py createsuperuser`.
- Inside Docker `DJANGO_DEBUG` is off: set `DJANGO_ALLOWED_HOSTS` to your domain and `CORS_ALLOWED_ORIGINS` to
  the storefront's address (default `http://localhost:3000`). Behind HTTPS, also set `CSRF_TRUSTED_ORIGINS`
  (e.g. `https://api.example.com`) so the admin login works.
- It runs one worker with threads on purpose: SQLite prefers one process and the rate limits stay accurate.
  For heavier traffic, switch to PostgreSQL first.

## Deploying to AWS

`deploy/` has everything for one EC2 server (storefront + API + automatic HTTPS with Caddy, nightly backups):
see [deploy/DEPLOY.md](deploy/DEPLOY.md). It sets `DJANGO_BEHIND_PROXY=True`, which makes the API trust the
proxy's forwarded headers (real visitor IP for rate limits, HTTPS, secure cookies). Leave it off when the API is
reachable directly: with it off, `X-Forwarded-For` is ignored so nobody can dodge the rate limits.

## API

Interactive docs (Swagger UI): <http://localhost:8000/api/docs/> · ReDoc: `/api/redoc/` · OpenAPI schema: `/api/schema/`.
After pulling this change run `pip install -r requirements.txt` once.

| Method | URL | What |
| --- | --- | --- |
| GET | `/api/health/` | liveness check |
| GET | `/api/menu/` | menu items that can currently be ordered (each has its `category` id and `category_name`) |
| GET | `/api/categories/` | active menu categories, in display order |
| GET | `/api/offers/today/` | the menu items the admin picked for Today's offers, in order (same shape as `/api/menu/`) |
| GET | `/api/addons/` | the menu items the admin picked as extras ("Make it a meal"), in order (same shape as `/api/menu/`) |
| GET | `/api/outlets/` | active outlets |
| GET | `/api/pickup-slots/` | takeaway pickup times for the rest of today, plus `open_now` |
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
  "pickup_time": null,           // takeaway only: a "value" from /api/pickup-slots/; null = as soon as possible
  "notes": "",
  "items": [{ "id": "garlic-wings", "qty": 2 }]
}
```

- Prices are **never** read from the request. Every line is priced from the menu in the database.
- Delivery orders get a flat delivery charge (`DELIVERY_FEE`, default ৳80); takeaway is free. The receipt
  returns `subtotal`, `delivery_fee` and `total` (subtotal + fee). The storefront shows the same amount from
  `deliveryFee` in `stallo-frontend/src/data/site.ts`, so change both together.
- Takeaway pickup: the customer can pick a time from `/api/pickup-slots/`. Times are on the quarter hour, at
  least 30 minutes away (`PICKUP_LEAD_MINUTES`), same day only (`PICKUP_DAYS_AHEAD`), from 10:00 until 15 minutes before closing
  (22:00 Sunday to Thursday, 23:00 Friday and Saturday; see `core/hours.py`). A time that is off the grid, too
  soon, for another day or outside opening hours is a `400` on `pickup_time`. The receipt returns `pickup_time`
  (`null` = as soon as possible), and admin shows it in the orders list. "As soon as possible" is not checked
  against opening hours: staff confirm by phone, and the storefront hides it while closed.
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

## Today's offers

In admin, open **Today's offers**, add a menu item (search the whole menu by name), type the **offer price**
and set the order. Untick *Active* to switch an offer off without removing it. The list shows the regular price,
the offer price and what the customer saves. The offer price must be lower than the regular price.

- `GET /api/offers/today/` (same shape as `/api/menu/`) returns `price` (the regular price) and `offer_price`
  (the lower price today, `null` if none). `/api/menu/` carries `offer_price` too.
- **Orders are charged the offer price** while the offer is active, so what the storefront shows is what the
  customer pays. The receipt's `unit_price` is the price actually charged, and old orders keep it if the offer
  later ends. If the menu price is later cut to or below an offer price, the offer is ignored.
- An offer with the price left empty (only possible for rows that existed before this feature) is listed but sells
  at the regular price.
- The storefront shows the offer price with the regular price crossed out in Today's offers, the menu, product
  pages, the add-to-cart dialog and the cart. It can only show items it has in its built-in menu, so a product
  added in admin will not appear on the site until the storefront reads `/api/menu/`.
- The first `seed_menu` run adds the four offers the storefront used to hard-code (without prices) and never
  refills the list afterwards.

## Product pictures

In admin, **Menu items → Picture** is an uploader. Choose any photo (JPG, PNG or WebP, up to 8 MB). Saving resizes it
(longest side 1200 px), turns it into WebP (about 100 KB), stores it and shows a preview. The picture is
**required**: a product can't be saved in admin without one (to change it, upload a new one).

- **Where files go:** with `AWS_STORAGE_BUCKET_NAME` set, to that S3 bucket (`menu/<product>-<random>.webp`);
  otherwise to the `media/` folder (`/data/media` in Docker), served by this server at `/media/...` for local work.
  AWS credentials come from the server's IAM role, or from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. See
  `deploy/DEPLOY.md` (section 4b) for the bucket, its policy and the role.
- **What the database keeps:** only the file's name (`menu/fries-1a2b3c4d.webp`). The public address is built from it
  when the API answers, so changing the bucket's region or putting CloudFront in front needs no data change.
  Every upload gets a new random name, so browsers and CDNs can cache pictures for a year.
- **API:** `/api/menu/`, `/api/offers/today/` and `/api/addons/` carry `image`: the full URL, or `null` for the
  original starter items that have no picture yet (the storefront then shows a plain placeholder).
- Replacing or clearing a picture, or deleting the product, deletes the old file from storage.

## Add-ons (extras)

In admin, open **Add-ons**, add a menu item (search the whole menu by name) and set the order. Untick *Active* to
stop suggesting it without removing it. These are the extras shown under "Make it a meal" when a customer taps **+**
on a dish. The item itself (name, price, category) is still managed under Menu items.

- `GET /api/addons/` returns them in order, in the same shape as `/api/menu/` (with `price` and `offer_price`).
- An add-on is an ordinary order line: the storefront adds it to the cart and sends it in `items` like any other
  item, so there is nothing extra to send when ordering. It is charged the item's current price, so an active
  Today's offer on it applies.
- Items that are switched off, or in a hidden category, are not returned. The list is the same for every dish (the
  storefront leaves out the dish being added).
- Databases that already had a menu get the six extras the storefront used to hard-code (fries, wrap, nuggets,
  pops, hot wings, garlic wings) when the migration runs; fresh ones get them from the first `seed_menu`. Neither
  refills the list after the admin has made their own picks.

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
