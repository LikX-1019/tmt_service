def test_application_import() -> None:
    import main

    assert main.app is not None
