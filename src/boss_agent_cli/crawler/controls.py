"""Transport-independent crawl stop signals; they must never trigger fallback."""


class CrawlLimitExceeded(RuntimeError):
	"""A user-configured crawl budget has reached its hard limit."""

	request_control_stop = True


class CrawlStopRequested(RuntimeError):
	"""The user requested a checkpoint stop at the next safe point."""

	request_control_stop = True


class CrawlWorkflowStopRequested(CrawlStopRequested):
	"""The owning workflow requested cooperative stop."""


class CrawlTimeoutRequested(CrawlStopRequested):
	"""The owning workflow deadline expired."""


class CrawlRuntimeUnavailable(RuntimeError):
	"""The optional crawl dependencies cannot be imported; retrying or resuming cannot change that."""
