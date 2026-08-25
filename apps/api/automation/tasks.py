from celery import shared_task

from automation import services


@shared_task
def process_outbox():
    return services.process_outbox()


@shared_task
def evaluate_arrears():
    return services.evaluate_arrears_all_orgs()
