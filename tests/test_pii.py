from libs.common.pii import redact


def test_redact_masks_separated_pan_and_sms_code():
    text = 'Карта 2200 1234 5678 9999, sms код 123456'
    out, summary = redact(text)

    assert '<masked_card_last4:9999>' in out
    assert '<masked_otp>' in out
    assert summary['pan'] == 1
    assert summary['otp_code'] == 1


def test_redact_masks_contract_and_dob():
    text = 'договор №ABCD-778899, дата рождения 01.02.1999'
    out, summary = redact(text)

    assert '<masked_contract>' in out
    assert '<masked_dob>' in out
    assert summary['contract'] == 1
    assert summary['dob'] == 1


def test_redact_masks_full_name_and_address():
    text = 'Иванов Иван Иванов\nАдрес: г. Москва, ул. Пушкина, д. 10'
    out, summary = redact(text)

    assert '<masked_name>' in out
    assert '<masked_address>' in out
    assert summary['fio'] == 1
    assert summary['address'] == 1