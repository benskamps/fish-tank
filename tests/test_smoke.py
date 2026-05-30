import tank


def test_version():
    assert tank.__version__ == "0.1.0"


def test_import_does_not_explode():
    from tank import __version__  # noqa: F401
