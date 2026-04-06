"""bid-collectors: 공공기관 입찰공고/지원사업 API 통합 수집 패키지"""

__version__ = "0.1.0"

from .models import Notice, CollectResult
from .base import BaseCollector

__all__ = ["Notice", "CollectResult", "BaseCollector"]
