# Stallo backend (Django)

Basic Django 4.2 + Django REST framework API for the Stallo storefront.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser   # optional, for /admin
python manage.py runserver         # http://127.0.0.1:8000
```

- Health check: `GET /api/health/`
- Admin: `/admin/`
- CORS is allowed for the Next.js dev server (`localhost:3000` / `3002`); edit `CORS_ALLOWED_ORIGINS` in `.env`.
