"""RedbookApiClient.note_to_dict 视频直链/视频封面解析单测。"""

from __future__ import annotations

from backend.app.infrastructure.crawlers.redbook_api_client import RedbookApiClient


def _video_note(**overrides) -> dict:
    base = {
        "id": "v1",
        "title": "视频笔记",
        "desc": "desc",
        "type": "video",
        "xsec_token": "tok",
        "liked_count": 10,
        "collected_count": 5,
        "comments_count": 2,
        "shared_count": 1,
        "user": {"userid": "u1", "nickname": "n1"},
        "images_list": [{"url": "https://img/fallback.jpg"}],
        "video_info_v2": {
            "media": {
                "video": {
                    "opaque1": {"default_screencast_stream": "https://cdn/default.mp4"},
                },
                "stream": {
                    "h264": [
                        {
                            "master_url": "https://cdn/h264_1.mp4",
                            "quality_type": "HD",
                            "backup_urls": ["https://backup/h264_1.mp4"],
                        }
                    ],
                    "h265": [
                        {
                            "master_url": "https://cdn/h265_1.mp4",
                            "quality_type": "FHD",
                            "backup_urls": [],
                        }
                    ],
                },
                "image": {
                    "thumbnail": "https://cdn/thumb.webp",
                    "first_frame": "https://cdn/first_frame.jpg",
                },
            }
        },
    }
    base.update(overrides)
    return base


def test_note_to_dict_extracts_h264_master_url_as_primary():
    note = RedbookApiClient.note_to_dict(_video_note(), "kw")

    assert note["video_url"] == "https://cdn/h264_1.mp4"
    assert [c["type"] for c in note["video_urls"]] == ["h264", "h265"]
    assert note["video_urls"][0]["backup_urls"] == ["https://backup/h264_1.mp4"]


def test_note_to_dict_prefers_video_thumbnail_over_image_cover():
    note = RedbookApiClient.note_to_dict(_video_note(), "kw")

    assert note["cover_url"] == "https://cdn/thumb.webp"


def test_note_to_dict_falls_back_to_first_frame_without_thumbnail():
    raw = _video_note()
    del raw["video_info_v2"]["media"]["image"]["thumbnail"]
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert note["cover_url"] == "https://cdn/first_frame.jpg"


def test_note_to_dict_falls_back_to_default_screencast_stream_without_h264_h265():
    raw = _video_note()
    raw["video_info_v2"]["media"]["stream"] = {"h264": [], "h265": []}
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert note["video_url"] == "https://cdn/default.mp4"
    assert note["video_urls"] == []


def test_note_to_dict_falls_back_to_image_cover_without_video_info():
    raw = _video_note()
    del raw["video_info_v2"]
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert note["video_url"] == ""
    assert note["video_urls"] == []
    assert note["cover_url"] == "https://img/fallback.jpg"


def test_note_to_dict_image_note_has_empty_video_fields():
    raw = {
        "id": "i1",
        "title": "图文笔记",
        "type": "normal",
        "xsec_token": "tok",
        "liked_count": 1,
        "collected_count": 1,
        "comments_count": 1,
        "shared_count": 0,
        "user": {"userid": "u1", "nickname": "n1"},
        "images_list": [{"url": "https://img/1.jpg"}],
    }
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert note["video_url"] == ""
    assert note["video_urls"] == []
    assert note["cover_url"] == "https://img/1.jpg"


def test_note_to_dict_malformed_video_info_does_not_raise():
    raw = _video_note(video_info_v2={"media": {}})
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert note["video_url"] == ""
    assert note["video_urls"] == []


# ---------- HEIF → JPG 转换（多模态模型无法解码三方接口返回的 HEIF 图片）----------

def test_force_jpg_replaces_heif_format_segment():
    url = (
        "https://sns-na-i6.xhscdn.com/spectrum/abc123"
        "?imageView2/2/w/608/format/heif/q/56|imageMogr2/strip"
        "&redImage/frame/0/enhance/4&ap=5&sc=SRH_PRV&sign=deadbeef&t=6a44bc61&origin=0"
    )
    converted = RedbookApiClient._force_jpg(url)

    assert "format/jpg" in converted
    assert "format/heif" not in converted
    # sign / 其余参数原样保留，仅替换 format 段
    assert "sign=deadbeef" in converted
    assert "t=6a44bc61" in converted


def test_force_jpg_leaves_non_heif_url_unchanged():
    url = "https://img/already-jpg.jpg?imageView2/2/w/608/format/jpg/q/56"
    assert RedbookApiClient._force_jpg(url) == url


def test_force_jpg_handles_empty_string():
    assert RedbookApiClient._force_jpg("") == ""


def test_note_to_dict_converts_heif_image_urls_and_cover_to_jpg():
    raw = {
        "id": "i1",
        "title": "图文笔记",
        "type": "normal",
        "xsec_token": "tok",
        "liked_count": 1,
        "collected_count": 1,
        "comments_count": 1,
        "shared_count": 0,
        "user": {"userid": "u1", "nickname": "n1"},
        "images_list": [
            {"url": "https://sns-na-i4.xhscdn.com/abc?imageView2/2/w/608/format/heif/q/56&sign=x&t=y"},
            {"url": "https://sns-na-i4.xhscdn.com/def?imageView2/2/w/1440/format/heif/q/45&sign=x&t=y"},
        ],
    }
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert note["cover_url"].endswith("format/jpg/q/56&sign=x&t=y")
    assert all("format/heif" not in u for u in note["image_urls"])
    assert all("format/jpg" in u for u in note["image_urls"])


def test_note_to_dict_converts_heif_video_thumbnail_to_jpg():
    raw = _video_note()
    raw["video_info_v2"]["media"]["image"]["thumbnail"] = (
        "https://sns-na-i4.xhscdn.com/xyz?imageView2/2/w/5000/format/heif/q/56&sc=SRH_ORG&sign=x&t=y"
    )
    note = RedbookApiClient.note_to_dict(raw, "kw")

    assert "format/heif" not in note["cover_url"]
    assert "format/jpg" in note["cover_url"]
