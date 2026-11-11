"""Smoke tests: every demo runs end to end and tells the story it claims to."""
from examples import facility_report, payment_posting, streaming_ingest


def test_payment_demo_has_no_duplicates_with_ledger(capsys):
    payment_posting.main()
    ledger_section = capsys.readouterr().out.split("Ledger bot:")[-1]
    assert "DUPLICATE" not in ledger_section


def test_facility_demo_resumes(capsys):
    facility_report.main()
    out = capsys.readouterr().out
    assert "STOPPED at FAC-27" in out and "already done       : 25" in out


def test_streaming_demo_delivers_every_row_once(capsys):
    streaming_ingest.main()
    assert "rows delivered       : 10000 of 10000 (unique: 10000)" in capsys.readouterr().out
