from erp_assistant.acquisition.crawling.screen_availability import ScreenAvailabilityClassifier


def test_classifier_marks_configured_soft_404_as_not_found():
    profile = {
        "screen_availability": {
            "enabled": True,
            "unavailable_status": "not_found",
            "min_pattern_matches": 2,
            "unavailable_text_patterns": [
                "PAGINA NO ENCONTRADA",
                "No hemos podido encontrar la página que buscas",
            ],
        }
    }
    classifier = ScreenAvailabilityClassifier(profile)

    result = classifier.classify(
        {
            "main_visible_text": (
                "PÁGINA NO ENCONTRADA No hemos podido encontrar la pagina que buscas."
            )
        }
    )

    assert result.available is False
    assert result.status == "not_found"
    assert len(result.matched_patterns) == 2


def test_classifier_does_not_mark_partial_match_when_threshold_is_two():
    profile = {
        "screen_availability": {
            "enabled": True,
            "unavailable_status": "not_found",
            "min_pattern_matches": 2,
            "unavailable_text_patterns": [
                "pagina no encontrada",
                "no hemos podido encontrar la pagina que buscas",
            ],
        }
    }
    classifier = ScreenAvailabilityClassifier(profile)

    result = classifier.classify(
        {"main_visible_text": "Registro: pagina no encontrada en una llamada externa"}
    )

    assert result.available is True
