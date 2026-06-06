# Test with real LLMs locally and recorded replays in CI

MetaCrucible's core optimization loop is tested end-to-end with real LLMs on the developer's machine so that optimizer and judge behavior stay grounded. CI runs the same orchestration against recorded replays of those LLM responses so cost, network, and secret management stay bounded without losing real-behavior coverage. Unit tests and smoke tests cover pure logic and CLI usability without any LLM at all.
