import random
import warnings as test_warnings
from contextlib import ExitStack
from http import HTTPStatus

import pytest
import requests

from rotkehlchen.api.server import APIServer
from rotkehlchen.constants.misc import ZERO
from rotkehlchen.fval import FVal
from rotkehlchen.serialization.serialize import process_result_list
from rotkehlchen.tests.utils.aave import (
    AAVE_TEST_ACC_1,
    AAVE_TEST_ACC_2,
    AAVE_TEST_ACC_3,
    aave_mocked_current_prices,
    aave_mocked_historical_prices,
    expected_aave_deposit_test_events,
    expected_aave_liquidation_test_events,
)
from rotkehlchen.tests.utils.api import (
    api_url_for,
    assert_error_response,
    assert_ok_async_response,
    assert_proper_response_with_result,
    wait_for_async_task,
)
from rotkehlchen.tests.utils.checks import assert_serialized_lists_equal
from rotkehlchen.tests.utils.rotkehlchen import BalancesTestSetup, setup_balances


@pytest.mark.parametrize('ethereum_accounts', [[AAVE_TEST_ACC_1]])
@pytest.mark.parametrize('ethereum_modules', [['aave']])
def test_query_aave_balances(rotkehlchen_api_server, ethereum_accounts):
    """Check querying the aave balances endpoint works. Uses real data.

    TODO: Here we should use a test account for which we will know what balances
    it has and we never modify
    """
    async_query = random.choice([False, True])
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=None,
        original_queries=['zerion'],
    )
    with ExitStack() as stack:
        # patch ethereum/etherscan to not autodetect tokens
        setup.enter_ethereum_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "aavebalancesresource",
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task(rotkehlchen_api_server, task_id)
            assert outcome['message'] == ''
            result = outcome['result']
        else:
            result = assert_proper_response_with_result(response)

    if len(result) != 1:
        test_warnings.warn(UserWarning(f'Test account {AAVE_TEST_ACC_1} has no aave balances'))
        return

    lending = result[AAVE_TEST_ACC_1]['lending']
    for _, entry in lending.items():
        assert len(entry) == 2
        assert len(entry['balance']) == 2
        assert 'amount' in entry['balance']
        assert 'usd_value' in entry['balance']
        assert '%' in entry['apy']
    borrowing = result[AAVE_TEST_ACC_1]['borrowing']
    for _, entry in borrowing.items():
        assert len(entry) == 3
        assert len(entry['balance']) == 2
        assert 'amount' in entry['balance']
        assert 'usd_value' in entry['balance']
        assert '%' in entry['variable_apr']
        assert '%' in entry['stable_apr']


@pytest.mark.parametrize('ethereum_accounts', [[AAVE_TEST_ACC_1]])
@pytest.mark.parametrize('ethereum_modules', [['makerdao_dsr']])
def test_query_aave_balances_module_not_activated(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    async_query = random.choice([False, True])
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    setup = setup_balances(rotki, ethereum_accounts=ethereum_accounts, btc_accounts=None)

    with ExitStack() as stack:
        # patch ethereum/etherscan to not autodetect tokens
        setup.enter_ethereum_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "aavebalancesresource",
        ), json={'async_query': async_query})

        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task(rotkehlchen_api_server, task_id)
            assert outcome['result'] is None
            assert outcome['message'] == 'aave module is not activated'
        else:
            assert_error_response(
                response=response,
                contained_in_msg='aave module is not activated',
                status_code=HTTPStatus.CONFLICT,
            )


def _query_simple_aave_history_test(
        setup: BalancesTestSetup,
        server: APIServer,
        async_query: bool,
        use_graph: bool,
) -> None:
    with ExitStack() as stack:
        # patch ethereum/etherscan to not autodetect tokens
        setup.enter_ethereum_patches(stack)
        response = requests.get(api_url_for(
            server,
            "aavehistoryresource",
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            # Big timeout since this test can take a long time
            outcome = wait_for_async_task(server, task_id, timeout=600)
            assert outcome['message'] == ''
            result = outcome['result']
        else:
            result = assert_proper_response_with_result(response)

    assert len(result) == 1
    assert len(result[AAVE_TEST_ACC_2]) == 3
    events = result[AAVE_TEST_ACC_2]['events']
    total_earned = result[AAVE_TEST_ACC_2]['total_earned']
    total_lost = result[AAVE_TEST_ACC_2]['total_lost']
    assert len(total_lost) == 0
    assert len(total_earned) == 1
    assert len(total_earned['aDAI']) == 2
    assert FVal(total_earned['aDAI']['amount']) >= FVal('24.207179802347627414')
    assert FVal(total_earned['aDAI']['usd_value']) >= FVal('24.580592532348742989192')

    expected_events = process_result_list(expected_aave_deposit_test_events)
    if use_graph:
        expected_events = expected_events[:7] + expected_events[8:]

    assert_serialized_lists_equal(
        a=events[:len(expected_events)],
        b=expected_events,
        ignore_keys=['log_index', 'block_number'] if use_graph else None,
    )


@pytest.mark.parametrize('ethereum_accounts', [[AAVE_TEST_ACC_2]])
@pytest.mark.parametrize('ethereum_modules', [['aave']])
@pytest.mark.parametrize('start_with_valid_premium', [True])
@pytest.mark.parametrize('mocked_price_queries', [aave_mocked_historical_prices])
@pytest.mark.parametrize('mocked_current_prices', [aave_mocked_current_prices])
@pytest.mark.parametrize('default_mock_price_value', [FVal(1)])
@pytest.mark.parametrize('aave_use_graph', [True])  # Try both with blockchain and graph
# @pytest.mark.parametrize('aave_use_graph', [True, False])  # Try both with blockchain and graph
def test_query_aave_history(rotkehlchen_api_server, ethereum_accounts, aave_use_graph):  # pylint: disable=unused-argument  # noqa: E501
    """Check querying the aave histoy endpoint works. Uses real data.

    Since this actually queries real blockchain data for aave it is a very slow test
    due to the sheer amount of log queries. We also use graph in 2nd version of test.
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=None,
        original_queries=['zerion'],
    )
    # Since this test is slow we don't run both async and sync in the same test run
    # Instead we randomly choose one. Eventually both cases will be covered.
    async_query = random.choice([True, False])

    _query_simple_aave_history_test(setup, rotkehlchen_api_server, async_query, aave_use_graph)

    if aave_use_graph:  # run it once more for graph to make sure DB querying gives same results
        _query_simple_aave_history_test(setup, rotkehlchen_api_server, async_query, aave_use_graph)


@pytest.mark.parametrize('ethereum_accounts', [[AAVE_TEST_ACC_3]])
@pytest.mark.parametrize('ethereum_modules', [['aave']])
@pytest.mark.parametrize('start_with_valid_premium', [True])
@pytest.mark.parametrize('mocked_price_queries', [aave_mocked_historical_prices])
@pytest.mark.parametrize('mocked_current_prices', [aave_mocked_current_prices])
@pytest.mark.parametrize('default_mock_price_value', [FVal(1)])
@pytest.mark.parametrize('aave_use_graph', [True])
def test_query_aave_history_with_borrowing(rotkehlchen_api_server, ethereum_accounts, aave_use_graph):  # pylint: disable=unused-argument  # noqa: E501
    """Check querying the aave histoy endpoint works. Uses real data.

    Since this actually queries real blockchain data for aave it is a very slow test
    due to the sheer amount of log queries
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=None,
        original_queries=['zerion'],
    )

    with ExitStack() as stack:
        # patch ethereum/etherscan to not autodetect tokens
        setup.enter_ethereum_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "aavehistoryresource",
        ))
        result = assert_proper_response_with_result(response)

    assert len(result) == 1
    assert len(result[AAVE_TEST_ACC_3]) == 3
    events = result[AAVE_TEST_ACC_3]['events']
    total_earned = result[AAVE_TEST_ACC_3]['total_earned']
    total_lost = result[AAVE_TEST_ACC_3]['total_lost']

    assert len(total_earned) == 1
    assert len(total_earned['aWBTC']) == 2
    assert FVal(total_earned['aWBTC']['amount']) >= FVal('0.00000833')
    assert FVal(total_earned['aWBTC']['usd_value']) >= ZERO

    assert len(total_lost) == 3
    eth_lost = total_lost['ETH']
    assert len(eth_lost) == 2
    assert FVal(eth_lost['amount']) >= FVal('0.004452186358507873')
    assert FVal(eth_lost['usd_value']) >= ZERO
    busd_lost = total_lost['BUSD']
    assert len(busd_lost) == 2
    assert FVal(busd_lost['amount']) >= FVal('21.605824443625747553')
    assert FVal(busd_lost['usd_value']) >= ZERO
    wbtc_lost = total_lost['WBTC']
    assert len(wbtc_lost) == 2
    assert FVal(wbtc_lost['amount']) >= FVal('0.41590034')  # ouch
    assert FVal(wbtc_lost['usd_value']) >= ZERO

    expected_events = process_result_list(expected_aave_liquidation_test_events)

    assert_serialized_lists_equal(
        a=events[:len(expected_events)],
        b=expected_events,
        ignore_keys=None,
    )


@pytest.mark.parametrize('ethereum_modules', [['aave']])
@pytest.mark.parametrize('start_with_valid_premium', [False])
def test_query_aave_history_non_premium(rotkehlchen_api_server, ethereum_accounts):  # pylint: disable=unused-argument  # noqa: E501
    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        "aavehistoryresource",
    ))
    assert_error_response(
        response=response,
        contained_in_msg='Currently logged in user testuser does not have a premium subscription',
        status_code=HTTPStatus.CONFLICT,
    )
