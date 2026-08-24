"""Database-level ledger invariants.

1. Immutability: UPDATE or DELETE on journal entries, postings or receipts
   raises — corrections are reversing entries, per trust-accounting rules.
2. Balancing: a deferred constraint trigger verifies, at commit, that every
   journal entry has ≥2 postings and Σdebits = Σcredits.

These are the backstop behind ``ledger.services.post_entry``; nothing that
bypasses the service (raw SQL, a future bug, a bad migration) can corrupt
the ledger without Postgres itself objecting.
"""

from django.db import migrations

FORWARD = r"""
CREATE FUNCTION ledger_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Ledger records are immutable; post a reversing entry instead (table %)', TG_TABLE_NAME;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER journalentry_immutable
    BEFORE UPDATE OR DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_forbid_mutation();

CREATE TRIGGER posting_immutable
    BEFORE UPDATE OR DELETE ON ledger_posting
    FOR EACH ROW EXECUTE FUNCTION ledger_forbid_mutation();

CREATE TRIGGER receipt_immutable
    BEFORE UPDATE OR DELETE ON ledger_receipt
    FOR EACH ROW EXECUTE FUNCTION ledger_forbid_mutation();

-- The balance check must see the postings the transaction just wrote even if
-- the deferred trigger fires after the tenant context has been cleared, so it
-- temporarily raises the app.bypass_rls flag (riding the explicit bypass
-- policy from common/rls.py) and restores the caller's value before returning.
-- A function-level SET clause would be cleaner, but non-superuser roles are
-- not permitted to attach custom GUCs to functions.
CREATE FUNCTION ledger_check_entry_balanced() RETURNS trigger AS $$
DECLARE
    total_debits BIGINT;
    total_credits BIGINT;
    posting_count INT;
    prev_bypass TEXT;
BEGIN
    prev_bypass := COALESCE(current_setting('app.bypass_rls', true), '');
    PERFORM set_config('app.bypass_rls', 'on', true);
    SELECT
        COALESCE(SUM(CASE WHEN side = 'debit' THEN amount_cents ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN side = 'credit' THEN amount_cents ELSE 0 END), 0),
        COUNT(*)
    INTO total_debits, total_credits, posting_count
    FROM ledger_posting
    WHERE journal_entry_id = NEW.journal_entry_id;
    PERFORM set_config('app.bypass_rls', prev_bypass, true);

    IF posting_count < 2 THEN
        RAISE EXCEPTION 'Journal entry % must have at least two postings', NEW.journal_entry_id;
    END IF;
    IF total_debits <> total_credits THEN
        RAISE EXCEPTION 'Journal entry % is unbalanced: debits % != credits %',
            NEW.journal_entry_id, total_debits, total_credits;
    END IF;
    RETURN NULL;
END
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER posting_entry_balanced
    AFTER INSERT ON ledger_posting
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_check_entry_balanced();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS posting_entry_balanced ON ledger_posting;
DROP FUNCTION IF EXISTS ledger_check_entry_balanced();
DROP TRIGGER IF EXISTS receipt_immutable ON ledger_receipt;
DROP TRIGGER IF EXISTS posting_immutable ON ledger_posting;
DROP TRIGGER IF EXISTS journalentry_immutable ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_forbid_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
