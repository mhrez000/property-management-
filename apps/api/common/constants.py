from django.db import models


class Jurisdiction(models.TextChoices):
    """Australian states and territories — the axis of the compliance engine."""

    NSW = "NSW", "New South Wales"
    VIC = "VIC", "Victoria"
    QLD = "QLD", "Queensland"
    WA = "WA", "Western Australia"
    SA = "SA", "South Australia"
    TAS = "TAS", "Tasmania"
    ACT = "ACT", "Australian Capital Territory"
    NT = "NT", "Northern Territory"
