import pytest

from backend.regions import region_from_proxy_name


@pytest.mark.parametrize(
    ("name", "region"),
    [
        ("亚洲 | 日本 H", "日本"),
        ("🇯🇵日本东京01-0.1倍 | 电信联通推荐", "日本"),
        ("🇸🇬新加坡01 | 电信联通推荐", "新加坡"),
        ("亚洲 | 印度尼西亚1", "印度尼西亚"),
        ("🇺🇸美国圣何塞01 | 三网推荐", "美国"),
        ("亚洲 | 中国大陆1", "中国大陆"),
    ],
)
def test_region_is_inferred_from_chinese_proxy_name(name, region):
    assert region_from_proxy_name(name) == region


def test_longer_country_name_wins_over_shorter_name():
    assert region_from_proxy_name("亚洲 | 印度尼西亚1") == "印度尼西亚"


def test_unknown_proxy_name_returns_no_inference():
    assert region_from_proxy_name("剩余流量：494.9 GB") is None
