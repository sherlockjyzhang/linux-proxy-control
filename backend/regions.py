"""Country/region inference for proxy node names."""


# Match longer names before shorter names, for example 印度尼西亚 before 印度.
COUNTRY_NAME_RULES = (
    ("印度尼西亚", "印度尼西亚"),
    ("阿联酋", "阿联酋"),
    ("澳大利亚", "澳大利亚"),
    ("马来西亚", "马来西亚"),
    ("新西兰", "新西兰"),
    ("菲律宾", "菲律宾"),
    ("加拿大", "加拿大"),
    ("阿根廷", "阿根廷"),
    ("土耳其", "土耳其"),
    ("俄罗斯", "俄罗斯"),
    ("乌克兰", "乌克兰"),
    ("新加坡", "新加坡"),
    ("中国大陆", "中国大陆"),
    ("香港", "香港"),
    ("澳门", "澳门"),
    ("台湾", "台湾"),
    ("日本", "日本"),
    ("韩国", "韩国"),
    ("美国", "美国"),
    ("墨西哥", "墨西哥"),
    ("巴西", "巴西"),
    ("法国", "法国"),
    ("德国", "德国"),
    ("英国", "英国"),
    ("荷兰", "荷兰"),
    ("爱尔兰", "爱尔兰"),
    ("瑞士", "瑞士"),
    ("西班牙", "西班牙"),
    ("意大利", "意大利"),
    ("波兰", "波兰"),
    ("罗马尼亚", "罗马尼亚"),
    ("印度", "印度"),
    ("泰国", "泰国"),
    ("越南", "越南"),
    ("南非", "南非"),
    ("以色列", "以色列"),
    ("中国", "中国"),
)


def region_from_proxy_name(name):
    """Return the first recognized Chinese country/region name in a node name."""
    if not isinstance(name, str):
        return None
    for country, region in COUNTRY_NAME_RULES:
        if country in name:
            return region
    return None
