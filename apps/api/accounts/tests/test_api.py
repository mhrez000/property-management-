"""API-level tenancy tests: what a real client sees across org boundaries."""

import pytest
from rest_framework.test import APIClient

from accounts.models import Membership, Organisation, User
from common.tenancy import org_context
from portfolio.models import Property

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def other_user(other_org):
    user = User.objects.create_user("pm@rival.example", "test-pass-123")
    Membership.objects.create(
        user=user, organisation=other_org, role=Membership.Role.PROPERTY_MANAGER
    )
    return user


def _make_property(org, street="10 Api St"):
    with org_context(org.pk):
        return Property.objects.create(
            organisation=org,
            address_line_1=street,
            suburb="Testville",
            state="NSW",
            postcode="2000",
        )


def test_me_lists_memberships(api, user, org):
    api.force_authenticate(user)
    response = api.get("/api/v1/me/")
    assert response.status_code == 200
    assert response.data["email"] == "pm@harbour.example"
    assert response.data["memberships"][0]["organisation"]["name"] == "Harbour Realty"
    assert response.data["memberships"][0]["role"] == "admin"


def test_member_sees_only_their_orgs_properties(api, user, org, other_org, other_user):
    _make_property(org, "1 Mine St")
    _make_property(other_org, "1 Theirs St")

    api.force_authenticate(user)
    response = api.get("/api/v1/properties/")
    assert response.status_code == 200
    assert [p["address_line_1"] for p in response.data["results"]] == ["1 Mine St"]

    api.force_authenticate(other_user)
    response = api.get("/api/v1/properties/")
    assert [p["address_line_1"] for p in response.data["results"]] == ["1 Theirs St"]


def test_cannot_impersonate_foreign_org_via_header(api, user, other_org):
    _make_property(other_org, "1 Theirs St")
    api.force_authenticate(user)
    response = api.get("/api/v1/properties/", headers={"X-Organisation-Id": str(other_org.pk)})
    assert response.status_code == 403


def test_create_property_stamps_org(api, user, org):
    api.force_authenticate(user)
    response = api.post(
        "/api/v1/properties/",
        {
            "address_line_1": "5 New St",
            "suburb": "Newtown",
            "state": "NSW",
            "postcode": "2042",
        },
        format="json",
    )
    assert response.status_code == 201
    with org_context(org.pk):
        assert Property.objects.get().organisation_id == org.pk


def test_viewer_role_cannot_write(api, org):
    viewer = User.objects.create_user("viewer@harbour.example", "test-pass-123")
    Membership.objects.create(user=viewer, organisation=org, role=Membership.Role.VIEWER)
    api.force_authenticate(viewer)
    response = api.post(
        "/api/v1/properties/",
        {"address_line_1": "5 New St", "suburb": "Newtown", "state": "NSW", "postcode": "2042"},
        format="json",
    )
    assert response.status_code == 403


def test_user_without_membership_is_denied(api, db):
    loner = User.objects.create_user("loner@example.com", "test-pass-123")
    api.force_authenticate(loner)
    response = api.get("/api/v1/properties/")
    assert response.status_code == 403


def test_compliance_rules_are_readable_by_any_member(api, user):
    api.force_authenticate(user)
    response = api.get("/api/v1/compliance/rules/", {"jurisdiction": "VIC"})
    assert response.status_code == 200
    assert len(response.data) > 0
