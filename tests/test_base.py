"""BaseCollector 단위 테스트."""

import os
import pytest
from unittest.mock import AsyncMock, patch
from bid_collectors.base import BaseCollector
from bid_collectors.models import Notice


class ConcreteCollector(BaseCollector):
    """테스트용 구체 수집기."""
    source_name = "test_source"

    def __init__(self, api_key=None, **kwargs):
        super().__init__(api_key=api_key, **kwargs)
        self._notices: list[Notice] = []

    async def _fetch(self, days=1, **kwargs) -> tuple[list[Notice], int]:
        return self._notices, 1


def _make_notice(bid_no="BID-001", source="test_source"):
    return Notice(
        source=source,
        bid_no=bid_no,
        title="테스트",
        organization="기관",
        url="https://example.com",
    )


class TestBaseCollectorInit:
    """초기화 및 API 키 검증."""

    def test_api_key_from_constructor(self):
        collector = ConcreteCollector(api_key="test-key")
        assert collector.api_key == "test-key"

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"DATA_GO_KR_KEY": "env-key"}):
            collector = ConcreteCollector()
            assert collector.api_key == "env-key"

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            # DATA_GO_KR_KEY가 없는 상태에서
            env = os.environ.copy()
            env.pop("DATA_GO_KR_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="API 키가 필요합니다"):
                    ConcreteCollector()


class TestBaseCollectorAbstract:
    """추상 메서드 검증."""

    def test_cannot_instantiate_without_fetch(self):
        """_fetch를 구현하지 않으면 인스턴스화 불가."""
        class IncompleteCollector(BaseCollector):
            source_name = "incomplete"

        with pytest.raises(TypeError):
            IncompleteCollector(api_key="key")


class TestBaseCollectorCollect:
    """collect() 메서드: 중복 제거, 에러 핸들링."""

    @pytest.mark.asyncio
    async def test_collect_returns_result(self):
        collector = ConcreteCollector(api_key="key")
        collector._notices = [_make_notice()]
        result = await collector.collect()
        assert result.source == "test_source"
        assert len(result.notices) == 1
        assert result.total_fetched == 1
        assert result.total_after_dedup == 1

    @pytest.mark.asyncio
    async def test_collect_deduplicates(self):
        collector = ConcreteCollector(api_key="key")
        collector._notices = [
            _make_notice("BID-001"),
            _make_notice("BID-001"),  # 중복
            _make_notice("BID-002"),
        ]
        result = await collector.collect()
        assert result.total_fetched == 3
        assert result.total_after_dedup == 2
        bid_nos = [n.bid_no for n in result.notices]
        assert bid_nos == ["BID-001", "BID-002"]

    @pytest.mark.asyncio
    async def test_collect_handles_fetch_error(self):
        """_fetch에서 예외 발생 시 errors에 기록, is_partial=True."""

        class FailingCollector(BaseCollector):
            source_name = "failing"

            async def _fetch(self, days=1, **kwargs):
                raise RuntimeError("API 장애")

        collector = FailingCollector(api_key="key")
        result = await collector.collect()
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "API 장애" in result.errors[0]
        assert len(result.notices) == 0

    @pytest.mark.asyncio
    async def test_collect_empty(self):
        collector = ConcreteCollector(api_key="key")
        collector._notices = []
        result = await collector.collect()
        assert result.total_fetched == 0
        assert result.total_after_dedup == 0
        assert result.is_partial is False
