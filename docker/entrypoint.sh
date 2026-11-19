#!/bin/sh
# Container start-up: get the database ready, then hand over to the real command (gunicorn).
set -e

python manage.py migrate --noinput

# First start only: load the menu and outlets. Never again, so admin edits are not overwritten.
if python manage.py shell -c "import sys; from core.models import MenuItem; sys.exit(0 if MenuItem.objects.exists() else 1)"; then
  :
else
  python manage.py seed_menu
fi

# Optional: create an admin login from env vars (skipped if it already exists).
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
User = get_user_model()
name = os.environ['DJANGO_SUPERUSER_USERNAME']
if not User.objects.filter(username=name).exists():
    User.objects.create_superuser(name, os.environ.get('DJANGO_SUPERUSER_EMAIL', ''), os.environ['DJANGO_SUPERUSER_PASSWORD'])
    print('Created admin user', name)
"
fi

exec "$@"
