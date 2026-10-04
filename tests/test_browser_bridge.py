"""Browser bridge + adapter tests (no real Chrome required)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.browser import bridge_server
from adapters.browser.browser_adapter import BrowserAdapter


def test_bridge_status_and_extension_update():
    bridge_server.stop_bridge()
    bridge_server.reset_state()
    # Use high port to avoid collisions
    port = 18765
    try:
        bridge_server.start_bridge(port=port)
        base = bridge_server.bridge_base_url(port=port)
        st = bridge_server.http_json("GET", f"{base}/status")
        assert st.get("ok") is True
        assert st.get("connected") is False

        hello = bridge_server.http_json(
            "POST",
            f"{base}/extension/hello",
            {
                "tabs": [{"id": 1, "url": "https://example.com", "title": "Example"}],
                "active_tab": {
                    "id": 1,
                    "url": "https://example.com",
                    "title": "Example",
                    "text": "Hello Merge AI page text",
                },
            },
        )
        assert hello.get("ok") is True
        st2 = bridge_server.http_json("GET", f"{base}/status")
        assert st2.get("connected") is True

        adapter = BrowserAdapter(source="Chrome", base_url=base)
        assert adapter.is_connected()
        text = adapter.read_all()
        assert "Hello Merge AI" in text
        hits = adapter.search("Merge")
        assert hits

        prev = adapter.google_search("merge ai")
        assert "merge ai" in str(prev.new_value).lower()
        result = prev.apply()
        assert result.get("ok") is True
        polled = bridge_server.http_json("GET", f"{base}/poll_command")
        assert polled.get("command", {}).get("op") == "google_search"
    finally:
        bridge_server.stop_bridge()
        time.sleep(0.05)


if __name__ == "__main__":
    test_bridge_status_and_extension_update()
    print("Browser bridge tests passed.")
