"""
services/retry_utils.py
────────────────────────────────────────────────────────────────────
Exponential backoff and retry logic for RPC calls and external APIs.

All Solana RPC calls, Lit Protocol node requests, and external API calls
must use these utilities to gracefully handle transient failures.

────────────────────────────────────────────────────────────────────
"""

import asyncio
import random
from typing import TypeVar, Callable, Any, Optional
from functools import wraps
from datetime import datetime, timezone

T = TypeVar('T')


class RetryConfig:
    """Configuration for retry behavior."""
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay_ms: int = 100,
        max_delay_ms: int = 5000,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """
        Args:
            max_retries: Maximum number of retry attempts
            base_delay_ms: Initial delay in milliseconds
            max_delay_ms: Maximum delay between retries
            exponential_base: Exponential backoff multiplier
            jitter: Add randomness to prevent thundering herd
        """
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_delay_ms = max_delay_ms
        self.exponential_base = exponential_base
        self.jitter = jitter
    
    def get_delay_ms(self, attempt: int) -> int:
        """Calculate delay for a given attempt number (0-indexed)."""
        delay = self.base_delay_ms * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay_ms)
        
        if self.jitter:
            delay *= (0.5 + random.random())
        
        return int(delay)


# Default configs for different scenarios
RPC_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay_ms=100,
    max_delay_ms=2000,
    exponential_base=2.0,
    jitter=True,
)

LIT_PROTOCOL_RETRY_CONFIG = RetryConfig(
    max_retries=2,
    base_delay_ms=200,
    max_delay_ms=3000,
    exponential_base=2.5,
    jitter=True,
)

DATABASE_RETRY_CONFIG = RetryConfig(
    max_retries=2,
    base_delay_ms=50,
    max_delay_ms=500,
    exponential_base=2.0,
    jitter=False,
)


# ════════════════════════════════════════════════════════════════════
#  RETRY DECORATORS & HELPERS
# ════════════════════════════════════════════════════════════════════

def should_retry(exception: Exception) -> bool:
    """
    Determines if an exception is retryable.
    
    Retryable:
      • Network timeouts
      • Transient HTTP 5xx errors
      • Rate limits (429)
      • Temporary RPC failures
    
    Non-retryable:
      • Validation errors (400)
      • Authentication errors (401, 403)
      • Not found (404)
      • Program errors (custom contract logic)
    """
    error_msg = str(exception).lower()
    
    # Non-retryable patterns
    non_retryable_patterns = [
        "not found",
        "404",
        "invalid",
        "unauthorized",
        "403",
        "401",
        "permission denied",
        "already exists",
        "constraint",
    ]
    
    if any(pattern in error_msg for pattern in non_retryable_patterns):
        return False
    
    # Retryable patterns
    retryable_patterns = [
        "timeout",
        "connection",
        "econnrefused",
        "econnreset",
        "nodatareceived",
        "500",
        "502",
        "503",
        "504",
        "429",  # Rate limit
        "temporarily unavailable",
        "service unavailable",
        "too many requests",
    ]
    
    return any(pattern in error_msg for pattern in retryable_patterns)


async def retry_with_backoff(
    async_fn: Callable[..., Any],
    *args,
    config: RetryConfig = RPC_RETRY_CONFIG,
    operation_name: str = "operation",
    **kwargs
) -> Any:
    """
    Executes an async function with exponential backoff retry logic.
    
    Args:
        async_fn: Async function to execute
        args: Positional arguments
        config: Retry configuration
        operation_name: Name for logging
        kwargs: Keyword arguments
    
    Returns:
        Result of async_fn
    
    Raises:
        The last exception if all retries are exhausted
    """
    last_exception = None
    
    for attempt in range(config.max_retries + 1):
        try:
            return await async_fn(*args, **kwargs)
        
        except Exception as e:
            last_exception = e
            
            if attempt >= config.max_retries or not should_retry(e):
                print(
                    f"❌ [RETRY] {operation_name} failed after {attempt + 1} attempts: {str(e)}"
                )
                raise
            
            delay_ms = config.get_delay_ms(attempt)
            print(
                f"⚠️  [RETRY] {operation_name} attempt {attempt + 1}/{config.max_retries} "
                f"failed ({type(e).__name__}). Retrying in {delay_ms}ms..."
            )
            
            await asyncio.sleep(delay_ms / 1000.0)
    
    if last_exception:
        raise last_exception


def async_retry(
    config: RetryConfig = RPC_RETRY_CONFIG,
    operation_name: Optional[str] = None,
):
    """
    Decorator for async functions to add retry logic.
    
    @example
    @async_retry(config=RPC_RETRY_CONFIG, operation_name="Fetch vault state")
    async def get_vault_state(vault_pda: str):
        # Automatically retried with exponential backoff
        ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            name = operation_name or f"{func.__module__}.{func.__name__}"
            return await retry_with_backoff(
                func,
                *args,
                config=config,
                operation_name=name,
                **kwargs
            )
        return wrapper
    return decorator


# ════════════════════════════════════════════════════════════════════
#  ERROR CONTEXT & LOGGING
# ════════════════════════════════════════════════════════════════════

class ErrorContext:
    """Structured error context for logging and debugging."""
    
    def __init__(
        self,
        operation: str,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.operation = operation
        self.request_id = request_id
        self.user_id = user_id
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.attempts = 0
        self.errors: list[dict] = []
    
    def add_error(self, exception: Exception, attempt: int):
        """Record an error attempt."""
        self.errors.append({
            "attempt": attempt,
            "error_type": type(exception).__name__,
            "error_message": str(exception),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    
    def to_dict(self) -> dict:
        """Export context as dictionary."""
        return {
            "operation": self.operation,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "total_attempts": len(self.errors) + 1,
            "errors": self.errors,
        }
    
    def to_log_string(self) -> str:
        """Export as human-readable log string."""
        log = f"\n[ERROR CONTEXT] {self.operation}\n"
        log += f"  Request ID: {self.request_id}\n"
        log += f"  User ID: {self.user_id}\n"
        log += f"  Total Attempts: {len(self.errors) + 1}\n"
        
        if self.errors:
            log += "  Errors:\n"
            for i, err in enumerate(self.errors, 1):
                log += f"    [{i}] {err['error_type']}: {err['error_message']}\n"
        
        return log


# ════════════════════════════════════════════════════════════════════
#  TESTING & EXAMPLES
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    async def test_retry():
        """Test retry logic with a failing function."""
        
        attempt_count = 0
        
        async def flaky_operation():
            nonlocal attempt_count
            attempt_count += 1
            
            if attempt_count < 3:
                raise ConnectionError(f"Network timeout (attempt {attempt_count})")
            
            return "Success!"
        
        try:
            result = await retry_with_backoff(
                flaky_operation,
                config=RPC_RETRY_CONFIG,
                operation_name="Test flaky operation",
            )
            print(f"✓ Result: {result}")
        except Exception as e:
            print(f"✗ Failed: {e}")
    
    asyncio.run(test_retry())
