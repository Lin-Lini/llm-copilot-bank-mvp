from libs.common.rag_eval import (
    aggregate_reports,
    evaluate_entry,
    ndcg_at_k,
    reciprocal_rank,
    recall_at_k,
    result_matches_target,
    security_coverage_at_k,
)


def test_result_matches_target_by_title():
    row = {
        'title': 'Регламент коммуникации, защиты ПДн и противодействия социнжинирингу',
        'section': 'Предупреждение по безопасности',
        'quote': 'Не сообщайте коды из SMS.',
    }
    target = {'title_contains': 'защиты пдн и противодействия социнжинирингу'}
    assert result_matches_target(row, target) is True


def test_recall_mrr_ndcg_on_simple_case():
    results = [
        {'title': 'Документ A', 'section': 'Раздел 1', 'quote': 'foo'},
        {'title': 'Регламент блокировки карты', 'section': 'Процедура', 'quote': 'bar'},
        {'title': 'Документ C', 'section': 'Раздел 3', 'quote': 'baz'},
    ]
    expected = [{'title_contains': 'блокировки карты'}]

    assert recall_at_k(results, expected, k=3) == 1.0
    assert reciprocal_rank(results, expected, k=3) == 0.5
    assert ndcg_at_k(results, expected, k=3) > 0


def test_security_coverage_metric_on_simple_case():
    results = [
        {'title': 'Регламент коммуникации, защиты ПДн и противодействия социнжинирингу', 'section': 'Предупреждение', 'quote': 'Не сообщайте коды'},
        {'title': 'Другое', 'section': 'Раздел', 'quote': '...'},
    ]
    expected_security = [{'title_contains': 'защиты пдн и противодействия социнжинирингу'}]

    assert security_coverage_at_k(results, expected_security, k=3) == 1.0


def test_evaluate_entry_marks_pass_when_expected_hit_exists():
    entry = {
        'name': 'Block card',
        'query': 'заблокировать карту',
        'top_k': 3,
        'expected_any': [{'title_contains': 'блокировки карты'}],
    }
    results = [
        {'title': 'Регламент блокировки карты', 'section': 'Процедура', 'quote': '...', 'relevance': 0.9},
        {'title': 'Другое', 'section': 'Раздел', 'quote': '...', 'relevance': 0.1},
    ]

    report = evaluate_entry(entry, results)

    assert report['passed'] is True
    assert report['recall_at_k'] == 1.0
    assert report['mrr'] == 1.0


def test_evaluate_entry_computes_security_coverage_when_declared():
    entry = {
        'name': 'Security',
        'query': 'клиент сообщил код из sms',
        'top_k': 3,
        'expected_any': [{'title_contains': 'скрипты ответов оператора'}],
        'expected_security_any': [{'title_contains': 'защиты пдн и противодействия социнжинирингу'}],
    }
    results = [
        {'title': 'Регламент коммуникации, защиты ПДн и противодействия социнжинирингу', 'section': 'Предупреждение', 'quote': '...', 'relevance': 0.95},
        {'title': 'Скрипты ответов оператора', 'section': 'Шаг 1', 'quote': '...', 'relevance': 0.90},
    ]

    report = evaluate_entry(entry, results)

    assert report['security_coverage_at_k'] == 1.0
    assert report['security_passed'] is True


def test_aggregate_reports_computes_averages():
    summary = aggregate_reports(
        [
            {'passed': True, 'recall_at_k': 1.0, 'mrr': 1.0, 'ndcg_at_k': 1.0, 'security_coverage_at_k': 1.0, 'security_passed': True},
            {'passed': False, 'recall_at_k': 0.0, 'mrr': 0.0, 'ndcg_at_k': 0.0, 'security_coverage_at_k': None, 'security_passed': None},
        ]
    )

    assert summary['queries'] == 2
    assert summary['pass_rate'] == 0.5
    assert summary['avg_recall_at_k'] == 0.5
    assert summary['avg_mrr'] == 0.5
    assert summary['avg_ndcg_at_k'] == 0.5
    assert summary['security_queries'] == 1
    assert summary['security_pass_rate'] == 1.0
    assert summary['avg_security_coverage_at_k'] == 1.0