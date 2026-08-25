import pytest

from accounts.models import Membership, Organisation, User
from common.tenancy import org_context
from ledger.models import TrustAccount


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Harbour Realty")


@pytest.fixture
def other_org(db):
    return Organisation.objects.create(name="Rival Agency")


@pytest.fixture
def user(db, org):
    user = User.objects.create_user("pm@harbour.example", "test-pass-123")
    Membership.objects.create(user=user, organisation=org, role=Membership.Role.ADMIN)
    return user


@pytest.fixture
def trust_account(org):
    with org_context(org.pk):
        yield TrustAccount.objects.create(
            organisation=org, name="Harbour Realty Trust", bsb="012-345", account_number="123456789"
        )


@pytest.fixture
def tenancy(org):
    from portfolio.models import Property, Tenancy, Tenant

    with org_context(org.pk):
        prop = Property.objects.create(
            organisation=org,
            address_line_1="1 Example St",
            suburb="Newtown",
            state="NSW",
            postcode="2042",
        )
        tenant = Tenant.objects.create(organisation=org, name="Tessa Tenant")
        tenancy = Tenancy.objects.create(organisation=org, property=prop, reference="TEN-0001")
        tenancy.tenants.add(tenant)
        yield tenancy
