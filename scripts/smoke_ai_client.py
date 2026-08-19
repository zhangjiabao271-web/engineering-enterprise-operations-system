import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_client import AIClient, AIError


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.headers = {"x-request-id": "test-request"}

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.trust_env = False

    def request(self, *_args, **_kwargs):
        return self.response


def client_with(response):
    client = AIClient("test-key", model="deepseek-v4-flash")
    client._session = lambda: FakeSession(response)
    return client


def main():
    reasoning_client = client_with(
        FakeResponse(
            200,
            {
                "id": "response-1",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning_content": "经营分析结果",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    assert reasoning_client.chat_completion([]) == "经营分析结果"

    segmented_client = client_with(
        FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一段"},
                                {"type": "text", "text": "第二段"},
                            ]
                        }
                    }
                ]
            },
        )
    )
    assert segmented_client.chat_completion([]) == "第一段\n第二段"

    error_client = client_with(FakeResponse(500, {"error": {"message": None}}))
    try:
        error_client.chat_completion([])
    except AIError as error:
        assert "None" not in str(error)
        assert "HTTP 500" in str(error)
    else:
        raise AssertionError("HTTP 500 should raise AIError")

    empty_client = client_with(
        FakeResponse(
            200,
            {
                "id": "response-empty",
                "choices": [
                    {"message": {"content": None}, "finish_reason": "stop"}
                ],
            },
        )
    )
    try:
        empty_client.chat_completion([])
    except AIError as error:
        assert "空答案" in str(error)
        assert "None" not in str(error)
    else:
        raise AssertionError("Empty content should raise AIError")

    print("DeepSeek AI client smoke test passed")


if __name__ == "__main__":
    main()
