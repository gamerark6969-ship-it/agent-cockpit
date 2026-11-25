import unittest

from fastapi import HTTPException

from app.rate_limit import SlidingWindowLimiter, client_key, enforce_rate_limit


class FakeRequest:
    def __init__(self, headers=None, host="1.2.3.4"):
        self.headers = headers or {}
        self.client = type("Client", (), {"host": host})()


class SlidingWindowLimiterTests(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        clock = {"now": 100.0}
        limiter = SlidingWindowLimiter(limit=3, window_s=10, clock=lambda: clock["now"])

        for _ in range(3):
            allowed, _ = limiter.check("client-a")
            self.assertTrue(allowed)
        allowed, retry_after = limiter.check("client-a")
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_window_slides_and_restores_capacity(self):
        clock = {"now": 100.0}
        limiter = SlidingWindowLimiter(limit=2, window_s=10, clock=lambda: clock["now"])
        limiter.check("client-a")
        limiter.check("client-a")
        self.assertFalse(limiter.check("client-a")[0])

        clock["now"] = 111.0  # both hits are now outside the window
        self.assertTrue(limiter.check("client-a")[0])

    def test_keys_are_isolated(self):
        clock = {"now": 100.0}
        limiter = SlidingWindowLimiter(limit=1, window_s=10, clock=lambda: clock["now"])
        self.assertTrue(limiter.check("client-a")[0])
        self.assertTrue(limiter.check("client-b")[0])

    def test_retry_after_points_at_window_expiry(self):
        clock = {"now": 100.0}
        limiter = SlidingWindowLimiter(limit=1, window_s=10, clock=lambda: clock["now"])
        limiter.check("client-a")
        _, retry_after = limiter.check("client-a")
        self.assertAlmostEqual(retry_after, 10.0, delta=0.001)


class EnforceRateLimitTests(unittest.TestCase):
    def test_under_limit_passes(self):
        limiter = SlidingWindowLimiter(limit=5, window_s=60, clock=lambda: 0.0)
        enforce_rate_limit(limiter, FakeRequest())  # must not raise

    def test_over_limit_raises_429_with_retry_after(self):
        limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=lambda: 0.0)
        enforce_rate_limit(limiter, FakeRequest())
        with self.assertRaises(HTTPException) as raised:
            enforce_rate_limit(limiter, FakeRequest())
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Retry-After", raised.exception.headers)

    def test_forwarded_header_takes_priority(self):
        request = FakeRequest(headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"})
        self.assertEqual(client_key(request), "9.9.9.9")


if __name__ == "__main__":
    unittest.main()
