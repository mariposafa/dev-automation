import unittest
from datetime import date
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
import main
import profiling


class ServiceTests(unittest.TestCase):
    def test_routes(self):
        client = TestClient(profiling.app)
        try:
            with patch('profiling.calculate_profile', new_callable=AsyncMock, return_value={'status':'above_average','alert':True}) as calculate:
                self.assertTrue(client.post('/profiling/check', json={'year':2026,'month':9}).json()['alert'])
                calculate.assert_awaited_once_with(2026,9)
                self.assertEqual(client.get('/profiling/summary?year=2026&month=9').status_code,200)
                self.assertEqual(client.post('/profiling/check',json={'year':2026,'month':13}).status_code,422)
        finally:
            client.close()


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_http_contract_between_apps(self):
        real_client = httpx.AsyncClient
        transport = httpx.ASGITransport(app=profiling.app)
        def factory(**kwargs):
            return real_client(transport=transport, **kwargs)
        with patch('main.httpx.AsyncClient', side_effect=factory), patch('profiling.calculate_profile',new_callable=AsyncMock,return_value={'status':'above_average','alert':True}) as calculate:
            result = await main.get_expense_profile(date(2026,9,24))
        self.assertTrue(result['alert'])
        calculate.assert_awaited_once_with(2026,9)

    async def test_timeout_keeps_saved_transaction(self):
        from types import SimpleNamespace
        from datetime import datetime
        record = SimpleNamespace(id='test',date=datetime(2026,9,24),amount=10,method='Cash',desc='test',trx_type='pembelian',insert=AsyncMock())
        with patch('main.Transaction',return_value=record), patch('main.get_expense_profile',side_effect=httpx.ReadTimeout('private')):
            result = await main.add_transaction(main.RequestNewTransaction(amount=10,method='Cash',desc='test',trx_type='pembelian'))
        record.insert.assert_awaited_once()
        self.assertEqual(result['profiling']['status'],'unavailable')
