"""§12.1 trim：data URL 体积按解码估值，避免内联大图被误删。"""

from l3_node.intent_gateway.sanitize import trim_attachments_metadata_list


def test_trim_keeps_large_data_url_when_decoded_under_cap():
    # ~1.1MB raw JPEG → base64 约 1.47M 字符；整串 len > 1.4M 但解码 < 5MB，须保留
    raw_approx = 1_100_000
    # 伪造等长 base64 字母（仅测 trim 门限，不解码）
    fake_b64 = "A" * ((raw_approx * 4 + 2) // 3)
    data_url = f"data:image/jpeg;base64,{fake_b64}"
    assert len(data_url) > raw_approx
    items = [
        {
            "name": "x.png",
            "has_image": True,
            "size_bytes": len(data_url),
            "image_url": {"url": data_url},
        }
    ]
    out = trim_attachments_metadata_list(items)
    assert len(out) == 1


def test_trim_drops_when_decoded_exceeds_cap():
    cap = 5 * 1024 * 1024
    fake_b64 = "A" * ((cap * 4) // 3 + 100)
    data_url = f"data:image/jpeg;base64,{fake_b64}"
    items = [
        {
            "name": "huge.png",
            "has_image": True,
            "size_bytes": len(data_url),
            "image_url": {"url": data_url},
        }
    ]
    out = trim_attachments_metadata_list(items)
    assert len(out) == 0
