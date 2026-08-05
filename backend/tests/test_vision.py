import base64

from tests.conftest import AUTH_HEADERS


def test_vision_analyze_valid_image(client):
    image_b64 = base64.b64encode(b"\xff\xd8\xff\xfake-jpeg-bytes").decode("ascii")
    response = client.post(
        "/v1/vision/analyze",
        json={"image_base64": image_b64, "question": "what am I looking at?"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] > 0
    assert body["answer"]


def test_vision_analyze_rejects_invalid_base64(client):
    response = client.post(
        "/v1/vision/analyze",
        json={"image_base64": "not-valid-base64!!", "question": "what is this?"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422


def test_vision_analyze_read_text_is_uncertainty_aware(client):
    image_b64 = base64.b64encode(b"some-bytes").decode("ascii")
    response = client.post(
        "/v1/vision/analyze",
        json={"image_base64": image_b64, "question": "read this error message"},
        headers=AUTH_HEADERS,
    )
    body = response.json()
    assert body["confidence"] < 0.6
    assert body["uncertainty_note"] is not None
