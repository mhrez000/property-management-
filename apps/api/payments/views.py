import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.providers import WebhookVerificationError, get_provider
from payments.services import process_notification

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def payment_webhook(request, version=None, provider_name: str = ""):
    """Inbound payment webhook. Authenticated by HMAC signature, not a user.

    Response contract for providers: 2xx = processed (don't retry),
    400 = bad request (don't retry), 404/5xx = retry later.
    """
    provider = get_provider(provider_name)
    if provider is None:
        return JsonResponse({"detail": "Unknown provider"}, status=404)
    try:
        notification = provider.verify_and_parse(request.body, request.headers)
    except WebhookVerificationError as exc:
        logger.warning("Rejected webhook for %s: %s", provider_name, exc)
        return JsonResponse({"detail": str(exc)}, status=400)
    try:
        process_notification(notification)
    except LookupError as exc:
        # Unknown payment reference: ask the provider to retry (a new
        # tenancy's reference may not be active yet).
        return JsonResponse({"detail": str(exc)}, status=404)
    return HttpResponse(status=200)
