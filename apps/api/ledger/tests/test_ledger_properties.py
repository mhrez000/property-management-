"""Property-based tests (Hypothesis): money math must never leak a cent.

Across arbitrary sequences of balanced multi-line entries the trial balance
is exactly zero, and account balances equal the independent Python-side sum
of their lines — no rounding, no drift, for any amounts.
"""

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from accounts.models import Organisation
from common.tenancy import org_context
from ledger.models import Account
from ledger.services import Line, account_balance_cents, post_entry, trial_balance_cents

amounts = st.integers(min_value=1, max_value=10**9)


# One entry = a list of (account_index, amount) debits and credits with equal
# totals. We build balanced entries by mirroring each debit with a credit
# split across accounts.
entry_strategy = st.lists(
    st.tuples(st.integers(min_value=0, max_value=4), amounts), min_size=1, max_size=6
)


class LedgerMoneyMathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="PropTest")
        with org_context(cls.org.pk):
            cls.accounts = [
                Account.objects.create(
                    organisation=cls.org,
                    name=f"Account {i}",
                    type=Account.Type.LIABILITY if i else Account.Type.ASSET,
                    subtype=Account.Subtype.OTHER,
                )
                for i in range(5)
            ]

    @settings(max_examples=25, deadline=None)
    @given(entries=st.lists(entry_strategy, min_size=1, max_size=8))
    def test_trial_balance_is_always_zero(self, entries):
        with org_context(self.org.pk):
            expected = {account.pk: 0 for account in self.accounts}
            for i, debit_lines in enumerate(entries):
                total = sum(amount for _, amount in debit_lines)
                lines = [
                    Line(
                        account_id=self.accounts[idx].pk,
                        side="debit",
                        amount_cents=amount,
                    )
                    for idx, amount in debit_lines
                ]
                # Mirror the total as a single credit to the last account.
                lines.append(
                    Line(account_id=self.accounts[4].pk, side="credit", amount_cents=total)
                )
                post_entry(
                    organisation_id=self.org.pk,
                    description=f"Randomised entry {i}",
                    lines=lines,
                )
                for idx, amount in debit_lines:
                    expected[self.accounts[idx].pk] += amount
                expected[self.accounts[4].pk] -= total

            assert trial_balance_cents(self.org.pk) == 0
            for account in self.accounts:
                raw = expected[account.pk]
                signed = raw if account.is_debit_normal else -raw
                assert account_balance_cents(account) == signed
