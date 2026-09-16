from app.qa.normalizer import QueryNormalizer


def test_normalizer_removes_spaces_and_punctuation() -> None:
    assert QueryNormalizer().normalize("支持 七天无理由吗？？？") == "支持七天无理由吗"


def test_normalizer_converts_full_width_characters() -> None:
    assert QueryNormalizer().normalize(" ＡｉｒＲｕｎ　Ｘ３！ ") == "airrunx3"
