class RetryableJobError(Exception):
    """Signal that a job may be retried after a recoverable failure."""
