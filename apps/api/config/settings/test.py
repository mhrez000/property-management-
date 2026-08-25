from .base import *  # noqa: F401,F403

DEBUG = False
CELERY_TASK_ALWAYS_EAGER = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
