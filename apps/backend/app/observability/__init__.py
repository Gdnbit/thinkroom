"""Observability 子模块：Sentry / Prometheus / Rate limit 统一入口。

导出：
- init_sentry(app)
- install_metrics(app)
- get_limiter() / install_rate_limit(app)
- install_request_id(app)
- install_exception_handler(app)
"""

from app.observability.sentry import init_sentry  # noqa: F401
from app.observability.metrics import install_metrics  # noqa: F401
from app.observability.rate_limit import get_limiter, install_rate_limit  # noqa: F401
from app.observability.request_id import install_request_id  # noqa: F401
from app.observability.exception_handler import install_exception_handler  # noqa: F401
